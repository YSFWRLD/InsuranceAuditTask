"""Hospital 2: the deterministic audit.

Given the parsed contract, the persisted service mappings and the invoices,
decide for every invoice whether it is wrong, why, and -- separately -- whether
the correct total can be reconstructed and what it is.  Every financial and
contractual decision in this file is made by Python.  Nothing here calls a
model; the semantic stage has already written its decisions to
``artifacts/hospital_2/service_mappings.json`` and this module only reads them.

Contents
    1. Inputs and evidence bases
    2. Line identity        mappings + the per-line unit-basis tie-break
    3. Global context       hospital-wide indexes; unresolved lines as intervals
    4. Pricing              clause 3.2 stage by stage, integer cents, with a trace
    5. Line audit
    6. Invoice audit        Article XIII checks; occurrences kept distinct
    7. Confidence           evidence bands -- NOT calibrated probabilities
    8. Pipeline and outputs

Two readings run through the whole file:

* **Detection is not reconstruction.**  ``flagged``, ``pricing_complete`` and
  ``correction_reconstructable`` are separate answers.  A cap breach is
  certain; the quantity actually delivered is not.
* **An unresolved service still exists.**  A line whose identity is
  unresolved is carried everywhere as the set of services it might be, so it
  widens cumulative utilisation, daily aggregates, bundle detection and
  exclusion history instead of silently disappearing from them.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from ..shared.data import load_occurrences, parse_iso_date
from ..shared.models import AuditResult, Finding, InvoiceOccurrence, LineItem
from ..shared.money import apply_percentage_change, multiply_cents
from ..shared.submission import SUBMISSION_COLUMNS, UNCERTAINTY_COLUMNS
from .contract import H2Contract, parse_contract
from .matcher import AMBIGUOUS, MATCHED, UNKNOWN, H2Matcher, resolve_line_identity
from .semantic import (
    MAPPINGS_FILE,
    cluster_id,
    load_mappings,
    mappings_are_current,
    semantic_counts,
)

# ==========================================================================
# 1. Inputs and evidence bases
# ==========================================================================

REPO_ROOT = Path(__file__).resolve().parents[2]
JSONL = REPO_ROOT / "invoices" / "hospital_2_invoices.jsonl"
INVOICES_CSV = REPO_ROOT / "invoices" / "hospital_2_invoices.csv"
LINES_CSV = REPO_ROOT / "invoices" / "hospital_2_line_items.csv"
ARTIFACTS = REPO_ROOT / "artifacts" / "hospital_2"
OUTPUTS = REPO_ROOT / "outputs" / "hospital_2"

#: What each finding rests on.
#:   contract    a clause of the agreement, applied to fully known inputs
#:   arithmetic  the invoice disagrees with itself
#:   data        a field is not valid data at all
#:   inferred    rests on a reading or assumption recorded in the decision log
EVIDENCE_BASIS = {
    "contract_number_mismatch": ("contract", "13.11"),
    "duplicate_invoice_id": ("contract", "13.6"),
    "late_invoice_submission": ("inferred", "13.1; invoice_date read as the submission date"),
    "facility_mismatch": ("contract", "1.3"),
    "malformed_service_date": ("data", "2.3"),
    "service_date_out_of_contract": ("contract", "1.2"),
    "service_date_after_invoice_date": ("inferred", "2.3 and 13.1; a service cannot be billed before it is delivered"),
    "line_total_arithmetic": ("arithmetic", "3.3"),
    "invoice_total_mismatch": ("arithmetic", "3.3"),
    "unknown_service": ("inferred", "23.1 and the service clauses' presentation sentence; identity read from free text"),
    "ambiguous_service_description": ("inferred", "23.1; advisory only, never flags on its own"),
    "wrong_unit_basis": ("contract", "service clause unit-basis sentence; 2.6"),
    "unit_price_mismatch": ("contract", "service clause rate; 3.2"),
    "bundle_not_applied": ("contract", "bundle clauses; 3.2(a)"),
    "bundle_incorrectly_applied": ("contract", "bundle clauses; 3.2(a)"),
    "premium_omitted": ("contract", "uplift clauses; 3.2(d), 3.4, 2.4"),
    "premium_incorrectly_applied": ("contract", "uplift clauses; 3.2(d), 3.4, 2.4"),
    "volume_discount_omitted": ("contract", "discount clauses; 3.2(e), 3.5, 2.7"),
    "volume_discount_incorrectly_applied": ("contract", "discount clauses; 3.2(e), 3.5, 2.7"),
    "daily_cap_exceeded": ("contract", "cap clauses; 3.4"),
    "exclusion_window_violation": ("contract", "exclusion clauses; 3.6"),
}

#: Findings that follow from the invoice alone, without service identity.
STRUCTURAL = frozenset({
    "contract_number_mismatch", "duplicate_invoice_id", "facility_mismatch",
    "malformed_service_date", "service_date_out_of_contract",
    "line_total_arithmetic", "invoice_total_mismatch", "late_invoice_submission",
})


# ==========================================================================
# 2. Line identity
# ==========================================================================

@dataclass(frozen=True)
class ResolvedLine:
    occurrence: InvoiceOccurrence
    line: LineItem
    cluster_id: str
    status: str
    service: Optional[str]
    candidates: tuple[str, ...]
    #: deterministic | semantic_verified | unit_basis_tiebreak | unresolved
    source: str
    used_unit_basis_for_identity: bool
    #: The ambiguity was *verified* (classifier AMBIGUOUS, accepted by Jev),
    #: as opposed to merely not yet resolved.
    verified_ambiguity: bool

    @property
    def line_id(self) -> str:
        return self.line.line_id

    @property
    def patient_id(self) -> str:
        return self.occurrence.patient_id

    @property
    def service_date(self) -> Optional[_dt.date]:
        return self.line.service_date

    @property
    def billed_basis(self) -> str:
        # The raw token, not the shared enum: Hospital 2 bills clause 4.2 on the
        # compound basis "per_hour_per_item", which the enum does not carry.
        return self.line.unit_basis_raw

    @property
    def possible_services(self) -> tuple[str, ...]:
        if self.status == MATCHED:
            return (self.service,)  # type: ignore[return-value]
        if self.status == AMBIGUOUS:
            return self.candidates
        return ()


class StaleMappingsError(Exception):
    pass


def resolve_lines(
    occurrences: Iterable[InvoiceOccurrence], matcher: H2Matcher, mappings: dict
) -> list[ResolvedLine]:
    """Attach an identity to every line of every physical occurrence."""
    if not mappings_are_current(mappings, matcher.contract):
        raise StaleMappingsError(
            "service mappings are missing or were built for a different contract "
            "or matcher version; run `python -m src.main semantic h2 prepare`"
        )
    clusters = mappings["clusters"]
    out: list[ResolvedLine] = []
    for occ in occurrences:
        for line in occ.line_items:
            det = matcher.match(line.description)
            cid = cluster_id(det.normalized_description)
            record = clusters.get(cid)
            if record is None:
                raise StaleMappingsError(f"no mapping for description {line.description!r}")
            final = record["final"]
            candidates = tuple(c["service"] for c in record["deterministic"]["candidates"])
            if final["status"] == MATCHED and record.get("used_unit_basis_for_identity"):
                # A semantic decision that consumed the billed unit basis holds
                # only for lines carrying that basis.  Those lines record the
                # consumption, so the same basis can never also accuse them of
                # wrong_unit_basis; any other line gets no identity from it.
                if line.unit_basis_raw == record.get("billed_unit_basis"):
                    status, service, source, used = MATCHED, final["service"], final["source"], True
                else:
                    status, service, source, used = AMBIGUOUS, None, "unresolved", False
            elif final["status"] == MATCHED:
                status, service, source, used = MATCHED, final["service"], final["source"], False
            elif final["status"] == UNKNOWN:
                status, service, source, used = UNKNOWN, None, final["source"], False
            else:
                # Text leaves a choice.  The billed unit basis may break a
                # genuine textual tie -- once, and recorded.
                ident = resolve_line_identity(det, line.unit_basis_raw)
                if ident.status == MATCHED:
                    status, service, source, used = MATCHED, ident.service, "unit_basis_tiebreak", True
                else:
                    status, service, source, used = AMBIGUOUS, None, final["source"], False
            out.append(ResolvedLine(
                occurrence=occ, line=line, cluster_id=cid, status=status, service=service,
                candidates=candidates, source=source, used_unit_basis_for_identity=used,
                verified_ambiguity=(status == AMBIGUOUS and final["status"] == AMBIGUOUS and final["verified"]),
            ))
    return out


# ==========================================================================
# 3. Global context
# ==========================================================================

@dataclass
class Interval:
    """A quantity known only to lie in ``[low, high]``."""

    low: int = 0
    high: int = 0

    def as_list(self) -> list[int]:
        return [self.low, self.high]


def _order(rl: ResolvedLine) -> tuple:
    """Clause 3.5: Service Date, then ascending line identifier.  A malformed
    date cannot be placed; such lines sort last and are left out of the
    cumulative count, which is recorded as uncertainty."""
    return (rl.service_date is None, rl.service_date or _dt.date.max, rl.line_id)


class AuditContext:
    """Every cross-line index, built once for the whole agreement.

    All indexes span invoice boundaries: clause 3.4's Service Day aggregate,
    clause 3.5's cumulative utilisation, the bundle clauses and clause 3.6's
    exclusion windows are all about the patient or the agreement, never about
    one invoice.
    """

    def __init__(self, resolved: Iterable[ResolvedLine]):
        self.lines = sorted(resolved, key=_order)
        self._daily: dict[tuple, Interval] = defaultdict(Interval)
        self._day_certain: dict[tuple, set] = defaultdict(set)
        self._day_possible: dict[tuple, set] = defaultdict(set)
        self._dates_certain: dict[tuple, set] = defaultdict(set)
        self._dates_possible: dict[tuple, set] = defaultdict(set)
        self._cap_lines: dict[tuple, list[ResolvedLine]] = defaultdict(list)
        self._prior: dict[str, dict[str, Interval]] = defaultdict(dict)
        self.undated_possible: Counter = Counter()

        running: dict[str, Interval] = defaultdict(Interval)
        for rl in self.lines:
            date = rl.service_date
            if date is None:
                for svc in rl.possible_services:
                    self.undated_possible[svc] += rl.line.quantity
                continue
            for svc in rl.possible_services:
                agg = self._daily[(rl.patient_id, svc, date)]
                agg.high += rl.line.quantity
                if rl.service == svc:
                    agg.low += rl.line.quantity
                self._day_possible[(rl.patient_id, date)].add(svc)
                self._dates_possible[(rl.patient_id, svc)].add(date)
                snapshot = running[svc]
                self._prior[rl.line_id][svc] = Interval(snapshot.low, snapshot.high)
            if rl.service is not None:
                self._day_certain[(rl.patient_id, date)].add(rl.service)
                self._dates_certain[(rl.patient_id, rl.service)].add(date)
                self._cap_lines[(rl.patient_id, rl.service, date)].append(rl)
            # Advance only after snapshotting: clause 2.7 excludes the line
            # being priced from its own cumulative utilisation.
            for svc in rl.possible_services:
                run = running[svc]
                run.high += rl.line.quantity
                if rl.service == svc:
                    run.low += rl.line.quantity

    def daily_quantity(self, patient: str, service: str, date: _dt.date) -> Interval:
        return self._daily.get((patient, service, date), Interval())

    def prior_cumulative(self, line_id: str, service: str) -> Interval:
        return self._prior.get(line_id, {}).get(service, Interval())

    def services_on_day(self, patient: str, date: _dt.date) -> tuple[set, set]:
        return self._day_certain.get((patient, date), set()), self._day_possible.get((patient, date), set())

    def dates_for(self, patient: str, service: str, *, certain_only: bool = True) -> set:
        index = self._dates_certain if certain_only else self._dates_possible
        return index.get((patient, service), set())

    def cap_lines(self, patient: str, service: str, date: _dt.date) -> list[ResolvedLine]:
        return self._cap_lines.get((patient, service, date), [])


# ==========================================================================
# 4. Pricing
# ==========================================================================

@dataclass
class StageInputs:
    """What each clause 3.2 stage should do, as a set of plausible values.

    One value normally.  Several where something outside the line is
    unresolved -- an unresolved description elsewhere on the patient's day may
    or may not complete a bundle or cross a Service Day threshold; unresolved
    lines earlier in the term may or may not count towards cumulative
    utilisation; a malformed date hides the day of the week.  The first value
    is the reading supported by resolved lines alone.
    """

    bundle_options: tuple[bool, ...] = (False,)
    bundle_partner: Optional[str] = None
    uplift_options: tuple[Decimal, ...] = (Decimal(0),)
    uplift_kind: Optional[str] = None
    uplift_available: Decimal = Decimal(0)
    uplift_clause: Optional[str] = None
    discount_options: tuple[Decimal, ...] = (Decimal(0),)
    discount_available: tuple[Decimal, ...] = ()
    discount_clause: Optional[str] = None
    prior_cumulative: Optional[Interval] = None
    uncertainty: tuple[str, ...] = ()

    @property
    def certain(self) -> bool:
        return len(self.bundle_options) == len(self.uplift_options) == len(self.discount_options) == 1


def is_business_day(date: _dt.date) -> bool:
    """Clause 2.4.  Invoices record dates only, so a Service Day is taken to be
    the calendar date on the line (clause 2.2's second sentence)."""
    return date.weekday() < 5


class PricingEngine:
    def __init__(self, contract: H2Contract):
        self.contract = contract

    def stage_inputs(self, rl: ResolvedLine, service: str, ctx: AuditContext) -> StageInputs:
        c = self.contract
        si = StageInputs()
        reasons: list[str] = []
        date = rl.service_date

        # (a) bundle -- across invoices: the patient's Service Day, not the invoice.
        partner = c.bundle_partner(service)
        if partner is not None:
            si.bundle_partner = partner[0]
            if date is None:
                si.bundle_options = (False, True)
                reasons.append("malformed date: bundle partner presence unknown")
            else:
                certain, possible = ctx.services_on_day(rl.patient_id, date)
                if partner[0] in certain:
                    si.bundle_options = (True,)
                elif partner[0] in possible:
                    si.bundle_options = (False, True)
                    reasons.append(f"bundle partner {partner[0]!r} may have been delivered on {date} (unresolved description that day)")

        # (d) uplift -- the two kinds never share a service in this contract.
        daily = c.daily_aggregate_uplifts.get(service)
        nbd = c.non_business_day_uplifts.get(service)
        if daily is not None:
            si.uplift_kind, si.uplift_available, si.uplift_clause = "daily_aggregate", daily.uplift_fraction, daily.clause_id
            if date is None:
                si.uplift_options = (Decimal(0), daily.uplift_fraction)
                reasons.append("malformed date: Service Day aggregate unknown")
            else:
                agg = ctx.daily_quantity(rl.patient_id, service, date)
                lo, hi = agg.low > daily.exceeds_units, agg.high > daily.exceeds_units
                if lo:
                    si.uplift_options = (daily.uplift_fraction,)
                elif hi:
                    si.uplift_options = (Decimal(0), daily.uplift_fraction)
                    reasons.append(f"Service Day aggregate of {service!r} lies in {agg.as_list()}, straddling {daily.exceeds_units}")
        elif nbd is not None:
            si.uplift_kind, si.uplift_available, si.uplift_clause = "non_business_day", nbd.uplift_fraction, nbd.clause_id
            if date is None:
                si.uplift_options = (Decimal(0), nbd.uplift_fraction)
                reasons.append("malformed date: business-day status unknown")
            elif not is_business_day(date):
                si.uplift_options = (nbd.uplift_fraction,)

        # (e) cumulative discount -- whole term, all patients, prior-exclusive.
        tiers = c.volume_discounts.get(service) or []
        si.discount_available = tuple(t.discount_fraction for t in tiers)
        if tiers:
            si.discount_clause = tiers[0].clause_id

            def tier_for(total: int) -> Decimal:
                # Clause 3.5: the deeper discount applies, not compounded.  A
                # line is discounted whole; it is never split at a threshold.
                return max((t.discount_fraction for t in tiers if total > t.exceeds_units), default=Decimal(0))

            if date is None:
                si.discount_options = (Decimal(0),) + si.discount_available
                reasons.append("malformed date: position in the cumulative sequence unknown")
            else:
                prior = ctx.prior_cumulative(rl.line_id, service)
                undated = ctx.undated_possible.get(service, 0)
                si.prior_cumulative = Interval(prior.low, prior.high + undated)
                lo, hi = tier_for(prior.low), tier_for(prior.high + undated)
                if lo == hi:
                    si.discount_options = (lo,)
                else:
                    between = sorted({lo, hi} | {f for f in si.discount_available if lo < f < hi})
                    si.discount_options = (lo,) + tuple(f for f in between if f != lo)
                    reasons.append(
                        f"cumulative utilisation of {service!r} before this line lies in "
                        f"{si.prior_cumulative.as_list()}, straddling a discount threshold"
                    )
        si.uncertainty = tuple(reasons)
        return si

    def compute(
        self, rl: ResolvedLine, service: str, si: StageInputs, *,
        quantity: Optional[int] = None, bundle: Optional[bool] = None,
        uplift: Optional[Decimal] = None, discount: Optional[Decimal] = None,
    ) -> dict:
        """Clause 3.2, rounding half up after every step (3.1).  Returns the trace."""
        c = self.contract
        svc = c.services[service]
        qty = rl.line.quantity if quantity is None else quantity
        use_bundle = si.bundle_options[0] if bundle is None else bundle
        uf = si.uplift_options[0] if uplift is None else uplift
        df = si.discount_options[0] if discount is None else discount

        clauses = [svc.clause_id]
        rate = svc.base_rate_cents
        bundle_rate = None
        if use_bundle and si.bundle_partner:
            bundle_rate = c.bundle_partner(service)[1]
            rate = bundle_rate
            clauses.append(c.services[si.bundle_partner].clause_id)
        after_bundle = rate
        rate = multiply_cents(rate, c.facility_multiplier)
        after_facility = rate
        rate = multiply_cents(rate, c.plan_tier_multiplier)
        after_plan = rate
        rate = apply_percentage_change(rate, uf)
        after_uplift = rate
        if uf and si.uplift_clause:
            clauses.append(si.uplift_clause)
        rate = apply_percentage_change(rate, -df)
        if df and si.discount_clause:
            clauses.append(si.discount_clause)
        return {
            "service": service,
            "base_rate_cents": svc.base_rate_cents,
            "bundle_rate_cents": bundle_rate,
            "after_bundle_cents": after_bundle,
            "facility_multiplier": str(c.facility_multiplier),
            "after_facility_cents": after_facility,
            "plan_multiplier": str(c.plan_tier_multiplier),
            "after_plan_cents": after_plan,
            "uplift": {"kind": si.uplift_kind, "fraction": str(uf)} if uf else None,
            "after_uplift_cents": after_uplift,
            "volume_discount": (
                {"fraction": str(df),
                 "prior_cumulative": si.prior_cumulative.as_list() if si.prior_cumulative else None}
                if df else None
            ),
            "effective_unit_rate_cents": rate,
            "quantity": qty,
            "expected_line_total_cents": rate * qty,
            "source_clause_ids": list(dict.fromkeys(clauses + ["3.1", "3.2"])),
        }

    def plausible(self, rl: ResolvedLine, service: str, si: StageInputs, *, quantity=None) -> dict[int, int]:
        """``{unit rate: line total}`` over every reading the evidence allows."""
        out: dict[int, int] = {}
        for b in si.bundle_options:
            for u in si.uplift_options:
                for d in si.discount_options:
                    t = self.compute(rl, service, si, quantity=quantity, bundle=b, uplift=u, discount=d)
                    out.setdefault(t["effective_unit_rate_cents"], t["expected_line_total_cents"])
        return out

    def variants(self, rl: ResolvedLine, service: str, si: StageInputs) -> dict[str, tuple[int, ...]]:
        """Rates under single-stage deviations -- used only to *name* a mismatch."""
        def rate(**kw) -> int:
            return self.compute(rl, service, si, **kw)["effective_unit_rate_cents"]

        v: dict[str, tuple[int, ...]] = {}
        if si.bundle_partner:
            if si.bundle_options[0]:
                v["bundle_not_applied"] = (rate(bundle=False),)
            else:
                v["bundle_incorrectly_applied"] = (rate(bundle=True),)
        if si.uplift_options[0]:
            v["premium_omitted"] = (rate(uplift=Decimal(0)),)
        elif si.uplift_available:
            v["premium_incorrectly_applied"] = (rate(uplift=si.uplift_available),)
        if si.discount_options[0]:
            v["volume_discount_omitted"] = (rate(discount=Decimal(0)),)
        wrong = tuple(rate(discount=f) for f in si.discount_available if f != si.discount_options[0])
        if wrong:
            v["volume_discount_incorrectly_applied"] = wrong
        return v


# ==========================================================================
# 5. Line audit
# ==========================================================================

@dataclass
class LineOutcome:
    findings: list[Finding] = field(default_factory=list)
    advisories: list[Finding] = field(default_factory=list)
    payable: Optional[int] = None
    max_payable: Optional[int] = None
    reasons: list[str] = field(default_factory=list)
    trace: Optional[dict] = None
    #: The corrected amount depends on something not resolved -- an identity,
    #: or a threshold whose side depends on one.
    depends_on_unresolved: bool = False


class H2Auditor:
    def __init__(self, contract: H2Contract):
        self.contract = contract
        self.engine = PricingEngine(contract)
        self.term = (_dt.date.fromisoformat(contract.effective_from),
                     _dt.date.fromisoformat(contract.effective_to))

    # -- date and arithmetic checks ------------------------------------------

    def _line_checks(self, rl: ResolvedLine) -> list[Finding]:
        f: list[Finding] = []
        line, occ = rl.line, rl.occurrence
        if line.service_date is None:
            f.append(Finding("malformed_service_date",
                             f"service date {line.service_date_raw!r} is not a valid date",
                             line.line_id, blocks_reconstruction=True))
        else:
            if not (self.term[0] <= line.service_date <= self.term[1]):
                f.append(Finding("service_date_out_of_contract",
                                 f"service date {line.service_date} is outside the term "
                                 f"{self.term[0]} to {self.term[1]} (clause 1.2)", line.line_id))
            elif occ.invoice_date is not None and line.service_date > occ.invoice_date:
                # A date past the end of the term is usually also past the
                # invoice date; that is one defect, reported once.
                f.append(Finding("service_date_after_invoice_date",
                                 f"service date {line.service_date} is after the invoice date "
                                 f"{occ.invoice_date}", line.line_id))
        if line.unit_price_cents * line.quantity != line.line_total_cents:
            f.append(Finding("line_total_arithmetic",
                             f"line total {line.line_total_cents} != {line.unit_price_cents} x "
                             f"{line.quantity} (clause 3.3)", line.line_id))
        return f

    # -- cap allocation --------------------------------------------------------

    def _capped_quantity(self, rl: ResolvedLine, service: str, ctx: AuditContext) -> int:
        """The most this line could be paid for under the cap, allocating the
        patient's Service Day allowance in ascending line-identifier order."""
        cap = self.contract.daily_caps[service].max_units
        remaining = cap
        for other in sorted(ctx.cap_lines(rl.patient_id, service, rl.service_date), key=lambda r: r.line_id):
            take = max(0, min(other.line.quantity, remaining))
            if other.line_id == rl.line_id:
                return take
            remaining -= take
        return min(rl.line.quantity, cap)

    # -- exclusion windows ---------------------------------------------------

    def _exclusion(self, rl: ResolvedLine, ctx: AuditContext) -> tuple[Optional[Finding], list[str]]:
        """Clause 3.6: measured in either direction.  "Within N days" is read
        inclusively: a trigger exactly N days away is within the window."""
        date = rl.service_date
        reasons: list[str] = []
        for w in self.contract.exclusions_for(rl.service):
            certain = ctx.dates_for(rl.patient_id, w.other_service)
            hits = sorted(d for d in certain if abs((date - d).days) <= w.days)
            if hits:
                return Finding(
                    "exclusion_window_violation",
                    f"{rl.service!r} on {date} is within {w.days} days of {w.other_service!r} "
                    f"on {hits[0]} (clause {w.clause_id}, measured either direction per 3.6)",
                    rl.line_id,
                ), reasons
            maybe = ctx.dates_for(rl.patient_id, w.other_service, certain_only=False) - certain
            if any(abs((date - d).days) <= w.days for d in maybe):
                reasons.append(f"{w.other_service!r} may fall within {w.days} days (unresolved description); not asserted")
        return None, reasons

    # -- one line ------------------------------------------------------------

    def audit_line(self, rl: ResolvedLine, ctx: AuditContext) -> LineOutcome:
        out = LineOutcome(findings=self._line_checks(rl))
        line = rl.line

        if rl.status == UNKNOWN:
            out.findings.append(Finding(
                "unknown_service",
                f"description {line.description!r} does not identify any contracted service "
                f"(identity: {rl.source})", line.line_id, blocks_reconstruction=True))
            out.reasons.append(f"{line.line_id}: service not contracted, so no contract price exists")
            out.depends_on_unresolved = True
            return out

        if rl.status == AMBIGUOUS:
            return self._audit_ambiguous(rl, ctx, out)

        service = rl.service
        svc = self.contract.services[service]

        if rl.billed_basis != svc.unit_basis and not rl.used_unit_basis_for_identity:
            out.findings.append(Finding(
                "wrong_unit_basis",
                f"billed {rl.billed_basis!r}; clause {svc.clause_id} states "
                f"{svc.unit_basis_text!r} ({svc.unit_basis!r})", line.line_id))

        not_payable = False
        if line.service_date is not None:
            excl, excl_reasons = self._exclusion(rl, ctx)
            out.reasons.extend(f"{line.line_id}: {r}" for r in excl_reasons)
            if excl is not None:
                out.findings.append(excl)
                not_payable = True

        si = self.engine.stage_inputs(rl, service, ctx)
        out.reasons.extend(f"{line.line_id}: {r}" for r in si.uncertainty)
        out.depends_on_unresolved = not si.certain
        trace = self.engine.compute(rl, service, si)
        plausible = self.engine.plausible(rl, service, si)
        out.trace = trace

        capped_qty = line.quantity
        cap_breached = False
        cap = self.contract.daily_caps.get(service)
        if cap is not None and line.service_date is not None:
            agg = ctx.daily_quantity(rl.patient_id, service, line.service_date)
            if agg.low > cap.max_units:
                cap_breached = True
                capped_qty = self._capped_quantity(rl, service, ctx)
                out.findings.append(Finding(
                    "daily_cap_exceeded",
                    f"{agg.low} units of {service!r} for patient {rl.patient_id} on "
                    f"{line.service_date}; clause {cap.clause_id} caps this at {cap.max_units}",
                    line.line_id, blocks_reconstruction=True))
                out.reasons.append(
                    f"{line.line_id}: the cap proves the quantity wrong but not what was "
                    f"delivered; only a ceiling can be priced")
            elif agg.high > cap.max_units:
                out.reasons.append(f"{line.line_id}: Service Day quantity lies in {agg.as_list()}, straddling the cap")

        rate_ok = line.unit_price_cents in plausible
        if not rate_ok:
            out.findings.append(self._name_mismatch(rl, service, si, trace))

        def priced(qty: int) -> Optional[int]:
            if si.certain:
                return self.engine.compute(rl, service, si, quantity=qty)["expected_line_total_cents"]
            if rate_ok:
                return self.engine.plausible(rl, service, si, quantity=qty).get(line.unit_price_cents)
            return None

        if not_payable:
            out.payable = out.max_payable = 0
        else:
            out.max_payable = priced(capped_qty)
            out.payable = None if cap_breached else priced(line.quantity)
        return out

    def _name_mismatch(self, rl: ResolvedLine, service: str, si: StageInputs, trace: dict) -> Finding:
        billed = rl.line.unit_price_cents
        for category, rates in self.engine.variants(rl, service, si).items():
            if billed in rates:
                return Finding(category, f"{service!r} billed at {billed}; contract rate "
                               f"{trace['effective_unit_rate_cents']}; the billed figure is exactly "
                               f"the rate with this one adjustment wrong", rl.line_id)
        return Finding("unit_price_mismatch", f"{service!r} billed at {billed}; contract rate "
                       f"{trace['effective_unit_rate_cents']} (clauses {', '.join(trace['source_clause_ids'])})",
                       rl.line_id)

    def _audit_ambiguous(self, rl: ResolvedLine, ctx: AuditContext, out: LineOutcome) -> LineOutcome:
        """No service is chosen.  The weaker question the evidence can answer:
        is the billed rate right under *any* candidate reading?"""
        line = rl.line
        out.depends_on_unresolved = True
        out.reasons.append(
            f"{line.line_id}: {line.description!r} is unresolved between "
            f"{', '.join(rl.candidates)} ({rl.source})")
        if rl.verified_ambiguity:
            out.advisories.append(Finding(
                "ambiguous_service_description",
                f"{line.description!r} verified as not identifying a single service", line.line_id))

        bases = {self.contract.services[s].unit_basis for s in rl.candidates}
        if rl.candidates and rl.billed_basis not in bases:
            out.findings.append(Finding(
                "wrong_unit_basis",
                f"billed {rl.billed_basis!r}; no plausible reading ({', '.join(rl.candidates)}) "
                f"is billed on that basis", line.line_id))

        consistent: set[int] = set()
        for s in rl.candidates:
            si = self.engine.stage_inputs(rl, s, ctx)
            total = self.engine.plausible(rl, s, si).get(line.unit_price_cents)
            if total is not None:
                consistent.add(total)
        if consistent:
            out.payable = out.max_payable = consistent.pop() if len(consistent) == 1 else None
            return out
        out.findings.append(Finding(
            "unit_price_mismatch",
            f"billed {line.unit_price_cents} matches no contract rate under any reading of "
            f"{line.description!r} ({', '.join(rl.candidates)})", line.line_id))
        return out


# ==========================================================================
# 6. Invoice audit
# ==========================================================================

@dataclass
class OccurrenceAudit:
    """One physical invoice record.  Never merged with another occurrence."""

    occurrence: InvoiceOccurrence
    findings: list[Finding]
    advisories: list[Finding]
    expected_total_cents: Optional[int]
    max_payable_total_cents: Optional[int]
    depends_on_unresolved: bool
    reasons: list[str]
    traces: list[dict]
    lines: list[ResolvedLine]


@dataclass
class H2InvoiceResult:
    """The claim about one invoice identifier, plus what the CSV cannot hold.

    ``result`` is the submitted row: it describes the *represented*
    occurrence only.  ``occurrences`` keeps every physical record with all of
    its findings, so nothing an earlier record showed is lost.
    """

    result: AuditResult
    occurrences: list[OccurrenceAudit]
    advisories: list[Finding]
    #: A best-effort diagnostic total -- for example an unresolved line priced
    #: at the reading its billed rate matches.  Never submitted; the scored
    #: ``result.expected_total_cents`` is filled only when the correction is
    #: contractually defensible.
    provisional_expected_total_cents: Optional[int] = None

    @property
    def represented(self) -> OccurrenceAudit:
        return self.occurrences[-1]


def audit_occurrence(
    occ: InvoiceOccurrence, lines: list[ResolvedLine], ctx: AuditContext,
    auditor: H2Auditor, *, is_repeat: bool, first_occurrence: Optional[InvoiceOccurrence],
) -> OccurrenceAudit:
    c = auditor.contract
    findings: list[Finding] = []
    reasons: list[str] = []

    if occ.contract_number != c.contract_number:
        findings.append(Finding("contract_number_mismatch",
                                f"invoice quotes {occ.contract_number!r}; this agreement is "
                                f"{c.contract_number!r} (clause 13.11)"))
    if occ.facility_code != c.facility_code:
        findings.append(Finding("facility_mismatch",
                                f"facility {occ.facility_code!r}; services are delivered from "
                                f"{c.facility_code!r} (clause 1.3)"))
    if is_repeat and first_occurrence is not None:
        findings.append(Finding("duplicate_invoice_id",
                                f"invoice number {occ.invoice_id!r} was already used by the record "
                                f"dated {first_occurrence.invoice_date_raw} for patient "
                                f"{first_occurrence.patient_id} (clause 13.6)"))
    discharge = parse_iso_date(occ.discharge_date_raw)
    limit = c.invoice_rules.submission_days_after_discharge
    if discharge is None or occ.invoice_date is None:
        reasons.append("discharge or invoice date malformed: clause 13.1 cannot be checked")
    elif (occ.invoice_date - discharge).days > limit:
        findings.append(Finding("late_invoice_submission",
                                f"invoice dated {occ.invoice_date}, {(occ.invoice_date - discharge).days} "
                                f"days after discharge on {discharge}; clause 13.1 allows {limit}"))
    if sum(l.line_total_cents for l in occ.line_items) != occ.invoice_total_cents:
        findings.append(Finding("invoice_total_mismatch",
                                f"invoice total {occ.invoice_total_cents} != sum of line totals "
                                f"{sum(l.line_total_cents for l in occ.line_items)} (clause 3.3)"))

    advisories: list[Finding] = []
    payable: Optional[int] = 0
    ceiling: Optional[int] = 0
    unresolved = False
    traces = []
    for rl in lines:
        o = auditor.audit_line(rl, ctx)
        findings.extend(o.findings)
        advisories.extend(o.advisories)
        reasons.extend(o.reasons)
        unresolved = unresolved or o.depends_on_unresolved
        if o.trace is not None:
            traces.append({"line_id": rl.line_id, "identity_source": rl.source,
                           "used_unit_basis_for_identity": rl.used_unit_basis_for_identity, **o.trace})
        payable = None if (payable is None or o.payable is None) else payable + o.payable
        ceiling = None if (ceiling is None or o.max_payable is None) else ceiling + o.max_payable

    return OccurrenceAudit(occ, findings, advisories, payable, ceiling, unresolved, reasons, traces, lines)


def combine(invoice_id: str, audits: list[OccurrenceAudit]) -> H2InvoiceResult:
    """One submitted row per invoice number, describing one occurrence.

    The submission format has one row per invoice number, but a reused number
    names several physical invoices.  The row represents the **last**
    occurrence -- the record whose submission reused the number (clause
    13.6).  Its billed total, corrected total, pricing state and findings are
    the row's; ``duplicate_invoice_id`` is among them.  An earlier
    occurrence's own findings are *not* copied onto the row: they are about a
    different invoice, with different money.  They remain in
    ``self.occurrences``, in ``findings.csv`` and in the audit report.

    The three states are separate answers:

    * ``pricing_complete``  every line of the represented occurrence has an
      exact contract price from resolved identities and determinate inputs.
    * ``correction_reconstructable``  that exact total is also contractually
      defensible: no finding blocks reconstruction (a cap breach, a malformed
      date, an unknown service).
    * ``expected_total_cents`` is filled **only** when reconstructable.  A
      number in that column is a claim to know what the invoice should have
      totalled, and nothing weaker is put there.
    """
    subject = audits[-1]
    findings = list(subject.findings)
    if len(audits) > 1:
        assert any(f.category == "duplicate_invoice_id" for f in findings), \
            "the represented occurrence of a reused number must carry duplicate_invoice_id"
    exact = subject.expected_total_cents is not None and not subject.depends_on_unresolved
    reconstructable = exact and not any(f.blocks_reconstruction for f in findings)
    reasons = list(dict.fromkeys(subject.reasons))
    if len(audits) > 1:
        earlier = [a for a in audits[:-1]]
        reasons.append(
            "invoice number reused: this row represents occurrence "
            f"{subject.occurrence.occurrence_id}; earlier occurrence(s) "
            + ", ".join(f"{a.occurrence.occurrence_id} ({len(a.findings)} finding(s))" for a in earlier)
            + " are audited separately in findings.csv"
        )
    if subject.expected_total_cents is not None and not reconstructable:
        reasons.append(
            f"a provisional total of {subject.expected_total_cents} was computed but is not "
            "contractually defensible, so no corrected total is submitted"
        )
    result = AuditResult(
        invoice_id=invoice_id,
        occurrence_ids=[a.occurrence.occurrence_id for a in audits],
        billed_total_cents=subject.occurrence.invoice_total_cents,
        flagged=bool(findings),
        findings=findings,
        expected_total_cents=subject.expected_total_cents if reconstructable else None,
        # A ceiling is only a ceiling when it rests on resolved lines.
        maximum_contractually_payable_total_cents=(
            None if subject.depends_on_unresolved else subject.max_payable_total_cents),
        pricing_complete=exact,
        correction_reconstructable=reconstructable,
        uncertainty_reasons=reasons,
    )
    band, value, why = assess_confidence(result, [subject])
    result.confidence_band, result.confidence = band, value
    result.uncertainty_reasons = list(dict.fromkeys(result.uncertainty_reasons + why))
    return H2InvoiceResult(result, audits, list(subject.advisories), subject.expected_total_cents)


# ==========================================================================
# 7. Confidence
# ==========================================================================
#
# Evidence quality, not a probability.  Hospital 2 has no labels, so nothing
# here is calibrated and nothing here claims an accuracy.  Values are lower
# than Hospital 1's for the same band on purpose: the semantic stage is not
# verified end to end, and a confidently wrong claim is worse than a flagged
# uncertainty.

BAND_VALUES = {"high": 0.85, "medium": 0.65, "low": 0.40}


def assess_confidence(result: AuditResult, audits: list[OccurrenceAudit]) -> tuple[str, float, list[str]]:
    cats = set(result.error_categories)
    lines = [rl for a in audits for rl in a.lines]
    unresolved = any(rl.status == AMBIGUOUS for rl in lines)
    tiebreak = any(rl.used_unit_basis_for_identity for rl in lines)
    semantic = any(rl.source == "semantic_verified" for rl in lines)
    why: list[str] = []

    if result.flagged:
        if cats and cats <= STRUCTURAL - {"late_invoice_submission"}:
            why.append("every finding follows from the invoice's own fields, independent of service identity")
            return "high", BAND_VALUES["high"], why
        if "unknown_service" in cats or not result.pricing_complete:
            why.append("a finding rests on reading free text, or the corrected total cannot be reconstructed")
            return "low", BAND_VALUES["low"], why
        if cats & {"unit_price_mismatch", "wrong_unit_basis"} and unresolved:
            why.append("a price or basis finding sits on an invoice with unresolved service identities")
            return "low", BAND_VALUES["low"], why
        why.append("findings apply contract rules to resolved services; some depend on hospital-wide state")
        return "medium", BAND_VALUES["medium"], why

    if unresolved:
        why.append("not flagged, but some lines could not be identified, so not every rule could be checked")
        return "low", BAND_VALUES["low"], why
    if tiebreak or semantic or not result.correction_reconstructable:
        why.append("not flagged; identity rests partly on a unit-basis tie-break or a verified semantic decision")
        return "medium", BAND_VALUES["medium"], why
    why.append("not flagged; every line identified from text and priced exactly")
    return "high", BAND_VALUES["high"], why


# ==========================================================================
# 8. Pipeline and outputs
# ==========================================================================

class H2Pipeline:
    """Files -> results.  Offline: reads persisted mappings, calls nothing."""

    def __init__(self, mappings: Optional[dict] = None):
        self.contract = parse_contract()
        self.matcher = H2Matcher(self.contract)
        self.auditor = H2Auditor(self.contract)
        self.occurrences = load_occurrences(JSONL)
        self.mappings = mappings if mappings is not None else load_mappings(MAPPINGS_FILE)
        self.resolved = resolve_lines(self.occurrences, self.matcher, self.mappings)

    def run(self) -> list[H2InvoiceResult]:
        return audit_resolved(self.occurrences, self.resolved, self.auditor)


def audit_resolved(
    occurrences: list[InvoiceOccurrence], resolved: list[ResolvedLine], auditor: H2Auditor
) -> list[H2InvoiceResult]:
    """Audit every occurrence against one hospital-wide context.

    Occurrences sharing an invoice number are audited separately, in file
    order; the first establishes the number and each later one is a repeat.
    """
    ctx = AuditContext(resolved)
    by_occ: dict[str, list[ResolvedLine]] = defaultdict(list)
    for rl in resolved:
        by_occ[rl.occurrence.occurrence_id].append(rl)
    by_id: dict[str, list[InvoiceOccurrence]] = defaultdict(list)
    for occ in occurrences:
        by_id[occ.invoice_id].append(occ)

    results = []
    for invoice_id in sorted(by_id):
        occs = sorted(by_id[invoice_id], key=lambda o: o.occurrence_index)
        audits = [
            audit_occurrence(o, by_occ[o.occurrence_id], ctx, auditor,
                             is_repeat=i > 0, first_occurrence=occs[0] if i > 0 else None)
            for i, o in enumerate(occs)
        ]
        results.append(combine(invoice_id, audits))
    return results


def write_findings(results: list[H2InvoiceResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["invoice_id", "occurrence_id", "line_id", "category", "severity",
                    "evidence_basis", "clause_reference", "detail"])
        for r in results:
            for a in r.occurrences:
                for sev, items in (("error", a.findings), ("advisory", a.advisories)):
                    for f in items:
                        basis, ref = EVIDENCE_BASIS[f.category]
                        w.writerow([r.result.invoice_id, a.occurrence.occurrence_id, f.line_id or "",
                                    f.category, sev, basis, ref, f.detail])


def write_traces(results: list[H2InvoiceResult], path: Path) -> int:
    """Pricing traces for every line of every flagged invoice."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for r in results:
            if not r.result.flagged:
                continue
            for a in r.occurrences:
                for t in a.traces:
                    fh.write(json.dumps({"invoice_id": r.result.invoice_id,
                                         "occurrence_id": a.occurrence.occurrence_id, **t}) + "\n")
                    n += 1
    return n


def line_identity_counts(resolved: list[ResolvedLine], mappings: dict) -> dict[str, int]:
    """Line occurrences by the identity the audit actually used.

    ``unit_basis_provisional_line_occurrences`` are lines of semantic-pending
    clusters that the billed unit basis resolved for now: if the semantic
    stage later verifies a different reading, the verified one wins.
    """
    clusters = mappings["clusters"]
    pending = {cid for cid, r in clusters.items() if r["final"]["source"] == "unresolved"}
    return {
        "total_line_occurrences": len(resolved),
        "deterministic_matched_line_occurrences": sum(rl.source == "deterministic" and rl.status == MATCHED for rl in resolved),
        "semantic_verified_matched_line_occurrences": sum(rl.source == "semantic_verified" and rl.status == MATCHED for rl in resolved),
        "unit_basis_tiebreak_line_occurrences": sum(rl.used_unit_basis_for_identity for rl in resolved),
        "unit_basis_provisional_line_occurrences": sum(
            rl.used_unit_basis_for_identity and rl.cluster_id in pending for rl in resolved),
        "currently_ambiguous_line_occurrences": sum(rl.status == AMBIGUOUS for rl in resolved),
        "currently_unknown_line_occurrences": sum(rl.status == UNKNOWN for rl in resolved),
    }


def identity_counts(pipeline: "H2Pipeline") -> dict[str, int]:
    """Every identity count, cluster-level and line-level, with explicit names."""
    return {**semantic_counts(pipeline.mappings),
            **line_identity_counts(pipeline.resolved, pipeline.mappings)}


#: Explanations shown beside each count, so no report has to be read against
#: another to know what a number means.
COUNT_EXPLANATIONS = {
    "total_normalized_clusters": "distinct descriptions after normalisation (/SA-#### stripped)",
    "deterministic_matched_clusters": "identified from text alone",
    "deterministic_unknown_clusters": "a recognised word contradicts every candidate; decided from text, not semantic work",
    "semantic_required_clusters": "sent by the deterministic stage to the classifier + Jev",
    "semantic_required_line_occurrences": "invoice lines carrying those descriptions",
    "semantic_pending_clusters": "of those, still without a verified decision",
    "semantic_pending_line_occurrences": "invoice lines carrying the pending descriptions",
    "semantic_verified_matched_clusters": "classifier MATCHED, accepted by Jev",
    "semantic_verified_ambiguous_clusters": "classifier AMBIGUOUS, accepted by Jev (stays AMBIGUOUS)",
    "semantic_verified_unknown_clusters": "classifier UNKNOWN, accepted by Jev (stays UNKNOWN)",
    "classifier_ok_clusters": "classifier returned a valid decision",
    "classifier_failed_clusters": "classifier output invalid or provider unreachable after bounded retries",
    "jev_answered_clusters": "Jev verdict imported or received",
    "total_unresolved_or_unknown_clusters": "every cluster without a single verified service (listed in unresolved.json)",
    "total_line_occurrences": "all invoice lines",
    "deterministic_matched_line_occurrences": "lines identified from text alone",
    "semantic_verified_matched_line_occurrences": "lines identified by a verified semantic decision",
    "unit_basis_tiebreak_line_occurrences": "lines whose billed basis broke a textual tie (cannot carry wrong_unit_basis)",
    "unit_basis_provisional_line_occurrences": "of those, lines in semantic-pending clusters (a verified decision would override)",
    "currently_ambiguous_line_occurrences": "lines the audit carries as a set of possible services",
    "currently_unknown_line_occurrences": "lines naming no contracted service",
}


def format_counts(counts: dict[str, int]) -> list[str]:
    return [f"| `{k}` | {v} | {COUNT_EXPLANATIONS.get(k, '')} |" for k, v in counts.items()]


PREDICTION_COLUMNS = SUBMISSION_COLUMNS + UNCERTAINTY_COLUMNS + ("provisional_expected_total_cents",)


def _blank(value) -> object:
    return "" if value is None else value


def write_predictions(results: list[H2InvoiceResult], path: Path) -> None:
    """The submission columns, the uncertainty columns, and the diagnostic
    provisional total -- kept in its own column so it can never be mistaken
    for the corrected total.  ``None`` is written as an empty field."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PREDICTION_COLUMNS)
        for h in sorted(results, key=lambda h: h.result.invoice_id):
            r = h.result
            w.writerow([
                r.invoice_id, 1 if r.flagged else 0, "|".join(r.error_categories),
                _blank(r.expected_total_cents), r.billed_total_cents, f"{r.confidence:.2f}",
                r.confidence_band, int(r.pricing_complete), int(r.correction_reconstructable),
                _blank(r.maximum_contractually_payable_total_cents), "|".join(r.occurrence_ids),
                " ; ".join(r.uncertainty_reasons), _blank(h.provisional_expected_total_cents),
            ])


def audit_report(pipeline: H2Pipeline, results: list[H2InvoiceResult]) -> str:
    res = [r.result for r in results]
    row_cats = Counter(c for r in res for c in r.error_categories)
    occ_cats = Counter(c for h in results for a in h.occurrences for c in dict.fromkeys(f.category for f in a.findings))
    basis = Counter(EVIDENCE_BASIS[c][0] for r in res for c in r.error_categories)
    advis = Counter(f.category for h in results for a in h.occurrences for f in a.advisories)
    bands = Counter(r.confidence_band for r in res)
    counts = identity_counts(pipeline)
    reused = [h for h in results if len(h.occurrences) > 1]

    lines = [
        "# Hospital 2 - audit report",
        "",
        "Generated by `python -m src.main audit h2`. **There are no Hospital 2 labels.** "
        "Nothing below is an accuracy figure; it describes what the audit found and how "
        "much of it rests on unresolved evidence.",
        "",
        f"Contract `{pipeline.contract.contract_number}`, fingerprint `{pipeline.contract.fingerprint[:16]}`.",
        "",
        "## Coverage",
        "",
        "| | count |",
        "|---|---|",
        f"| invoice occurrences audited | {len(pipeline.occurrences)} |",
        f"| invoice numbers (submission rows) | {len(res)} |",
        f"| numbers used by more than one occurrence | {len(reused)} |",
        f"| line occurrences | {len(pipeline.resolved)} |",
        "",
        "## Service identity",
        "",
        "Two units are counted here and must not be mixed up. A **cluster** is one "
        "normalised description. A **line occurrence** is one invoice line. One "
        "cluster can cover hundreds of lines. The classifier is asked once per "
        "cluster; the audit prices every line.",
        "",
        "| count | value | meaning |",
        "|---|---|---|",
        *format_counts(counts),
        "",
        f"Semantic stage so far: classifier {pipeline.mappings.get('classifier_model')} "
        f"({counts['classifier_ok_clusters']} ok, {counts['classifier_failed_clusters']} failed); "
        f"Jev {pipeline.mappings.get('jev_model')} ({counts['jev_answered_clusters']} answered). "
        "Zero means the stage has not been run.",
        "",
        "## Results",
        "",
        "| | count |",
        "|---|---|",
        f"| flagged | {sum(r.flagged for r in res)} |",
        f"| not flagged | {sum(not r.flagged for r in res)} |",
        f"| pricing_complete = true | {sum(r.pricing_complete for r in res)} |",
        f"| correction_reconstructable = true | {sum(r.correction_reconstructable for r in res)} |",
        f"| expected_total_cents submitted | {sum(r.expected_total_cents is not None for r in res)} |",
        f"| expected_total_cents left blank | {sum(r.expected_total_cents is None for r in res)} |",
        f"| of which a provisional total exists (not submitted) | "
        f"{sum(r.expected_total_cents is None and h.provisional_expected_total_cents is not None for h, r in zip(results, res))} |",
        "",
        "`expected_total_cents` is filled only when the corrected total is contractually "
        "defensible. `pricing_complete` means every line of the represented occurrence "
        "was priced exactly from resolved identities; `correction_reconstructable` "
        "additionally means no finding blocks reconstruction.",
        "",
        "| confidence band | invoices |",
        "|---|---|",
        *[f"| {b} ({BAND_VALUES[b]}) | {bands.get(b, 0)} |" for b in ("high", "medium", "low")],
        "",
        "Confidence is an evidence band for review priority, not a calibrated probability.",
        "",
        "## Findings by category",
        "",
        "`rows` counts submitted rows carrying the category. `occurrences` counts physical "
        "invoice records carrying it. They differ only for reused invoice numbers, where "
        "the row represents the later record alone.",
        "",
        "| category | rows | occurrences | evidence basis | clause |",
        "|---|---|---|---|---|",
        *[f"| `{c}` | {row_cats.get(c, 0)} | {n} | {EVIDENCE_BASIS[c][0]} | {EVIDENCE_BASIS[c][1]} |"
          for c, n in occ_cats.most_common()],
        "",
        "| evidence basis | category occurrences on rows |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in basis.most_common()],
        "",
        "| advisory (never flags on its own) | occurrences |",
        "|---|---|",
        *([f"| `{c}` | {n} |" for c, n in advis.most_common()] or ["| none | 0 |"]),
        "",
        "## Reused invoice numbers",
        "",
        "Each row represents the later occurrence: its money, its pricing state and its "
        "findings (including `duplicate_invoice_id`). The earlier occurrence's findings are "
        "listed here and in `findings.csv`, and are not copied onto the row.",
        "",
        "| invoice number | represented occurrence | row categories | earlier occurrence findings |",
        "|---|---|---|---|",
        *[f"| {h.result.invoice_id} | {h.represented.occurrence.occurrence_id} | "
          f"{', '.join(h.result.error_categories)} | "
          + "; ".join(f"{a.occurrence.occurrence_id}: {', '.join(dict.fromkeys(f.category for f in a.findings)) or 'none'}"
                      for a in h.occurrences[:-1]) + " |"
          for h in reused],
        "",
        "## What limits this audit",
        "",
        f"- {counts['currently_ambiguous_line_occurrences']} line occurrences have no resolved "
        "service identity. Each is priced as a set of possible services and is flagged only "
        "if its price is wrong under every reading. No invoice containing one gets a "
        "submitted corrected total.",
        "- Those lines also widen cumulative utilisation, Service Day aggregates and bundle "
        "detection for every service they might be. Where that straddles a threshold, the "
        "affected lines accept any rate the plausible readings allow.",
        "- When the semantic stage verifies a mapping, the next `audit h2` prices those lines "
        "exactly, and invoices with no other blocker become reconstructable. No manual step "
        "is needed.",
        "- `artifacts/hospital_2/unresolved.json` lists every unresolved or unknown cluster by "
        "group; `outputs/hospital_2/decision_log.md` gives the reading behind each rule.",
        "",
    ]
    return "\n".join(lines)
