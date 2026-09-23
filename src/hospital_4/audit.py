"""Hospital 4: the deterministic audit.

Given the parsed contract and the invoices, decide for every invoice whether
it is wrong, why, and -- separately -- whether the correct total can be
reconstructed and what it is.  Every financial and contractual decision here
is made by Python.  Nothing calls a model, and nothing reads a label.

Contents
    1. Inputs, evidence bases and policy
    2. Line identity        text match + the per-line unit-basis tie-break
    3. Global context       hospital-wide indexes; unresolved lines as intervals
    4. Pricing              clause 4.1 stage by stage, integer cents, with a trace
    5. Duplicates           clause 11.3, across the whole data set
    6. Line audit
    7. Invoice audit        occurrences kept distinct; one row per invoice number
    8. Confidence           evidence bands -- NOT calibrated probabilities
    9. Pipeline
   10. Artifacts and outputs

Three readings run through the whole file:

* **Detection is not reconstruction.**  ``flagged``, ``pricing_complete`` and
  ``correction_reconstructable`` are separate answers.
* **An unresolved service still exists.**  A line whose identity the text
  does not settle is carried as the set of services it might be, so it widens
  Service Day aggregates, cumulative utilisation, bundle detection and
  exclusion history instead of disappearing from them.  A threshold whose
  side depends on it is left open, never decided.
* **Price never decides identity.**  The billed price is what is audited.
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

from ..shared.data import cross_check_against_csv, load_occurrences
from ..shared.models import AuditResult, Finding, InvoiceOccurrence, LineItem
from ..shared.money import apply_percentage_change, multiply_cents
from ..shared.submission import SUBMISSION_COLUMNS, UNCERTAINTY_COLUMNS
from .contract import H4Contract, parse_contract, rule_counts, write_contract_rules
from .matcher import (
    AMBIGUOUS, MATCHED, MATCHER_VERSION, UNKNOWN, ABBREVIATIONS, H4Matcher,
    canonical_key, cluster_descriptions, normalize_description, resolve_line_identity,
)

# ==========================================================================
# 1. Inputs, evidence bases and policy
# ==========================================================================

REPO_ROOT = Path(__file__).resolve().parents[2]
JSONL = REPO_ROOT / "invoices" / "hospital_4_invoices.jsonl"
INVOICES_CSV = REPO_ROOT / "invoices" / "hospital_4_invoices.csv"
LINES_CSV = REPO_ROOT / "invoices" / "hospital_4_line_items.csv"
ARTIFACTS = REPO_ROOT / "artifacts" / "hospital_4"
OUTPUTS = REPO_ROOT / "outputs" / "hospital_4"

#: What each finding rests on, and the clause it cites.
#:   contract    a clause of the agreement, applied to fully known inputs
#:   arithmetic  the invoice disagrees with itself
#:   data        a field is not valid data at all
#:   inferred    rests on reading free text or on a recorded implementation policy
EVIDENCE_BASIS = {
    "contract_number_mismatch": ("contract", "11.1"),
    "duplicate_invoice_id": ("contract", "11.1"),
    "facility_mismatch": ("contract", "2.2"),
    "malformed_service_date": ("data", "1.1"),
    "service_date_out_of_window": ("contract", "11.2, 2.1"),
    "service_date_after_invoice_date": ("contract", "11.2"),
    "line_total_arithmetic": ("arithmetic", "4.4"),
    "invoice_total_mismatch": ("arithmetic", "4.4"),
    "unknown_service": ("inferred", "3.1; identity read from free text"),
    "wrong_unit_basis": ("contract", "3.1, 1.3"),
    "unit_price_mismatch": ("contract", "3.1, 4.1, 4.2"),
    "bundle_not_applied": ("contract", "7.1, 4.1(a)"),
    "bundle_incorrectly_applied": ("contract", "7.1, 7.2, 4.1(a)"),
    "premium_omitted": ("contract", "5.1, 4.1(d)"),
    "premium_incorrectly_applied": ("contract", "5.1, 4.1(d)"),
    "volume_discount_omitted": ("contract", "8.3-8.5, 4.1(e)"),
    "volume_discount_incorrectly_applied": ("contract", "8.3-8.5, 4.1(e), 4.3"),
    "daily_cap_exceeded": ("contract", "6.1, 6.2"),
    "exclusion_window_violation": ("contract", "9.1"),
    "duplicate_service": ("contract", "11.3; first billing stands (implementation policy)"),
    "cross_invoice_duplicate": ("contract", "11.3; first billing stands (implementation policy)"),
}

#: Findings that follow from the invoice alone, without service identity.
STRUCTURAL = frozenset({
    "contract_number_mismatch", "duplicate_invoice_id", "facility_mismatch",
    "malformed_service_date", "service_date_out_of_window",
    "service_date_after_invoice_date", "line_total_arithmetic", "invoice_total_mismatch",
})


@dataclass(frozen=True)
class AuditPolicy:
    """Readings that the contract does not force.  Recorded in one place so the
    decision log and the code cannot drift apart."""

    #: Clause 6.1: "A quantity in excess of the limit is not payable."  Read
    #: literally: the payable quantity of the (single) payable line is
    #: min(limit, billed quantity), and the corrected total is reconstructable.
    #: The alternative (Hospital 1's reading: a breach proves the quantity is
    #: wrong without revealing the true one, so decline to reconstruct) is kept
    #: as ``False`` and documented as an unresolved ambiguity.
    cap_excess_priced_at_cap: bool = True

    #: Clause 9.1 "within N days": read inclusively (day N is inside).
    exclusion_window_inclusive: bool = True


# ==========================================================================
# 2. Line identity
# ==========================================================================

@dataclass(frozen=True)
class ResolvedLine:
    occurrence: InvoiceOccurrence
    line: LineItem
    canonical_key: str
    status: str
    service: Optional[str]
    candidates: tuple[str, ...]
    #: text | unit_basis_tiebreak | missing_discriminator | tied_candidates |
    #: unrecognised_token | contradiction | no_recognised_words
    method: str
    used_unit_basis_for_identity: bool

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
        # The raw token: "per_hour_per_item" is not in the shared enum.
        return self.line.unit_basis_raw

    @property
    def possible_services(self) -> tuple[str, ...]:
        if self.status == MATCHED:
            return (self.service,)  # type: ignore[return-value]
        if self.status == AMBIGUOUS:
            return self.candidates
        return ()


def resolve_lines(occurrences: Iterable[InvoiceOccurrence], matcher: H4Matcher) -> list[ResolvedLine]:
    out = []
    for occ in occurrences:
        for line in occ.line_items:
            det = matcher.match(line.description)
            ident = resolve_line_identity(det, line.unit_basis_raw)
            out.append(ResolvedLine(
                occurrence=occ, line=line, canonical_key=det.canonical_key, status=ident.status,
                service=ident.service, candidates=det.candidate_services, method=ident.method,
                used_unit_basis_for_identity=ident.used_unit_basis_for_identity,
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


def _cumulative_order(rl: ResolvedLine) -> tuple:
    """Clause 8.5: Service Date order, then ascending line identifier.  A
    malformed date cannot be placed; such lines sort last and widen every
    later line's upper bound instead (see ``prior_cumulative``)."""
    return (rl.service_date is None, rl.service_date or _dt.date.max, rl.line_id)


def _billing_order(rl: ResolvedLine) -> tuple:
    """Clause 11.3 forbids a repeat but does not say which record is the
    repeat.  Implementation policy: the first billing stands, by invoice date,
    then invoice identifier, then occurrence, then line identifier."""
    o = rl.occurrence
    return (o.invoice_date or _dt.date.max, o.invoice_id, o.occurrence_index, rl.line_id)


class AuditContext:
    """Every cross-line index, built once from every physical line occurrence.

    All indexes span invoice boundaries: Service Day aggregates (5.1, 6.2),
    bundles (7.1), cumulative utilisation (8.3-8.5), exclusion windows (9.1)
    and repeats (11.3) are about the patient or the agreement, never about one
    invoice.  An earlier occurrence of a reused invoice number stays in every
    index even though the submitted row represents the later one.
    """

    def __init__(self, resolved: Iterable[ResolvedLine]):
        self.lines = sorted(resolved, key=_cumulative_order)
        self._daily: dict[tuple, Interval] = defaultdict(Interval)
        self._day_certain: dict[tuple, set] = defaultdict(set)
        self._day_possible: dict[tuple, set] = defaultdict(set)
        self._dates_certain: dict[tuple, set] = defaultdict(set)
        self._dates_possible: dict[tuple, set] = defaultdict(set)
        self._same_day: dict[tuple, list[ResolvedLine]] = defaultdict(list)
        self._possible_same_day: dict[tuple, list[ResolvedLine]] = defaultdict(list)
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
                if rl.service != svc:
                    self._possible_same_day[(rl.patient_id, svc, date)].append(rl)
            if rl.service is not None:
                self._day_certain[(rl.patient_id, date)].add(rl.service)
                self._dates_certain[(rl.patient_id, rl.service)].add(date)
                self._same_day[(rl.patient_id, rl.service, date)].append(rl)
            # Advance only after snapshotting: clause 8.4 excludes the line
            # being priced from its own cumulative utilisation.
            for svc in rl.possible_services:
                run = running[svc]
                run.high += rl.line.quantity
                if rl.service == svc:
                    run.low += rl.line.quantity

    def daily_quantity(self, patient: str, service: str, date: _dt.date) -> Interval:
        return self._daily.get((patient, service, date), Interval())

    def prior_cumulative(self, line_id: str, service: str) -> Interval:
        """Prior-exclusive utilisation of ``service`` before ``line_id``.  Lines
        with a malformed date could sit anywhere in the sequence, so their
        possible units widen the upper bound."""
        prior = self._prior.get(line_id, {}).get(service, Interval())
        return Interval(prior.low, prior.high + self.undated_possible.get(service, 0))

    def services_on_day(self, patient: str, date: _dt.date) -> tuple[set, set]:
        return self._day_certain.get((patient, date), set()), self._day_possible.get((patient, date), set())

    def dates_for(self, patient: str, service: str, *, certain_only: bool = True) -> set:
        index = self._dates_certain if certain_only else self._dates_possible
        return index.get((patient, service), set())

    def same_day_lines(self, patient: str, service: str, date: _dt.date) -> list[ResolvedLine]:
        """Lines certainly billing ``service`` for the patient on the day."""
        return self._same_day.get((patient, service, date), [])

    def possible_same_day_lines(self, patient: str, service: str, date: _dt.date) -> list[ResolvedLine]:
        """Unresolved lines that might be ``service`` for the patient on the day."""
        return self._possible_same_day.get((patient, service, date), [])


# ==========================================================================
# 4. Pricing
# ==========================================================================

@dataclass
class StageInputs:
    """What each clause 4.1 stage should do, as a set of plausible values.

    One value normally.  Several where something outside the line is not
    settled: an unresolved description elsewhere on the patient's day may or
    may not complete a bundle or push the Service Day aggregate over a
    threshold; unresolved or undated lines earlier in the term may or may not
    count towards cumulative utilisation; a malformed date hides the day.  The
    first value is the reading supported by resolved lines alone.
    """

    bundle_options: tuple[bool, ...] = (False,)
    bundle_partner: Optional[str] = None
    bundle_clause: Optional[str] = None
    premium_options: tuple[Decimal, ...] = (Decimal(0),)
    premium_available: Decimal = Decimal(0)
    premium_threshold: Optional[int] = None
    day_quantity: Optional[Interval] = None
    discount_options: tuple[Decimal, ...] = (Decimal(0),)
    discount_available: tuple[Decimal, ...] = ()
    prior_cumulative: Optional[Interval] = None
    uncertainty: tuple[str, ...] = ()

    @property
    def certain(self) -> bool:
        return len(self.bundle_options) == len(self.premium_options) == len(self.discount_options) == 1

    def state(self, options: tuple, applies) -> str:
        if len(options) > 1:
            return "uncertain"
        return "applies" if applies(options[0]) else "does_not_apply"


class PricingEngine:
    def __init__(self, contract: H4Contract):
        self.contract = contract

    def stage_inputs(self, rl: ResolvedLine, service: str, ctx: AuditContext) -> StageInputs:
        c = self.contract
        si = StageInputs()
        reasons: list[str] = []
        date = rl.service_date

        # (a) bundle -- the patient's Service Day across every invoice (7.1).
        partner = c.bundle_partner(service)
        if partner is not None:
            si.bundle_partner, si.bundle_clause = partner[0], partner[2]
            if date is None:
                si.bundle_options = (False, True)
                reasons.append("malformed date: bundle partner presence unknown")
            else:
                certain, possible = ctx.services_on_day(rl.patient_id, date)
                if partner[0] in certain:
                    si.bundle_options = (True,)
                elif partner[0] in possible:
                    si.bundle_options = (False, True)
                    reasons.append(f"bundle partner {partner[0]!r} may have been delivered on {date} "
                                   "(an unresolved description that day)")

        # (d) threshold premium -- the Service Day aggregate, not the line (5.1).
        prem = c.threshold_premiums.get(service)
        if prem is not None:
            si.premium_available, si.premium_threshold = prem.uplift_fraction, prem.exceeds_units
            if date is None:
                si.premium_options = (Decimal(0), prem.uplift_fraction)
                reasons.append("malformed date: Service Day aggregate unknown")
            else:
                agg = ctx.daily_quantity(rl.patient_id, service, date)
                si.day_quantity = agg
                if agg.low > prem.exceeds_units:
                    si.premium_options = (prem.uplift_fraction,)
                elif agg.high > prem.exceeds_units:
                    si.premium_options = (Decimal(0), prem.uplift_fraction)
                    reasons.append(f"Service Day aggregate of {service!r} lies in {agg.as_list()}, "
                                   f"straddling the premium threshold {prem.exceeds_units}")
        nbd = c.non_business_day_uplifts.get(service)
        if nbd is not None:  # Section 10 lists none; kept so a future uplift is not ignored.
            raise NotImplementedError("Section 10 uplifts are listed as None in this agreement")

        # (e) cumulative discount -- whole term, all patients, prior-exclusive (8.3-8.5).
        tiers = c.volume_discounts.get(service) or []
        si.discount_available = tuple(t.discount_fraction for t in tiers)
        if tiers:
            def tier_for(total: int) -> Decimal:
                # 8.3 / 4.3: the deeper discount applies, uncompounded; a line
                # is discounted whole and never split at a threshold (8.4).
                return max((t.discount_fraction for t in tiers if total > t.exceeds_units), default=Decimal(0))

            if date is None:
                si.discount_options = (Decimal(0),) + si.discount_available
                reasons.append("malformed date: position in the cumulative sequence unknown")
            else:
                prior = ctx.prior_cumulative(rl.line_id, service)
                si.prior_cumulative = prior
                lo, hi = tier_for(prior.low), tier_for(prior.high)
                if lo == hi:
                    si.discount_options = (lo,)
                else:
                    between = sorted({lo, hi} | {f for f in si.discount_available if lo < f < hi})
                    si.discount_options = (lo,) + tuple(f for f in between if f != lo)
                    reasons.append(f"cumulative utilisation of {service!r} before this line lies in "
                                   f"{prior.as_list()}, straddling a discount threshold")
        si.uncertainty = tuple(reasons)
        return si

    def compute(self, service: str, si: StageInputs, quantity: int, *, bundle: Optional[bool] = None,
                premium: Optional[Decimal] = None, discount: Optional[Decimal] = None) -> dict:
        """Clause 4.1 in order, rounding half up after every step (4.2)."""
        c = self.contract
        svc = c.services[service]
        use_bundle = si.bundle_options[0] if bundle is None else bundle
        pf = si.premium_options[0] if premium is None else premium
        df = si.discount_options[0] if discount is None else discount
        clauses = ["3.1"]
        rate = svc.base_rate_cents
        if use_bundle and si.bundle_partner:
            rate = c.bundle_partner(service)[1]
            clauses.append("7.1")
        after_bundle = rate
        rate = multiply_cents(rate, c.facility_multiplier)
        after_facility = rate
        rate = multiply_cents(rate, c.plan_tier_multiplier)
        after_plan = rate
        rate = apply_percentage_change(rate, pf)
        after_premium = rate
        if pf:
            clauses.append("5.1")
        rate = apply_percentage_change(rate, -df)
        if df:
            clauses.append("8.3")
        return {
            "service": service,
            "base_rate_cents": svc.base_rate_cents,
            "bundle_applied": bool(use_bundle and si.bundle_partner),
            "after_bundle_cents": after_bundle,
            "facility_multiplier": str(c.facility_multiplier),
            "after_facility_cents": after_facility,
            "plan_multiplier": str(c.plan_tier_multiplier),
            "after_plan_cents": after_plan,
            "premium_fraction": str(pf),
            "after_premium_cents": after_premium,
            "discount_fraction": str(df),
            "effective_unit_rate_cents": rate,
            "payable_quantity": quantity,
            "expected_line_total_cents": rate * quantity,
            "clauses": clauses + ["4.1", "4.2"],
        }

    def plausible(self, service: str, si: StageInputs, quantity: int) -> dict[int, int]:
        """``{unit rate: line total}`` over every reading the evidence leaves open."""
        out: dict[int, int] = {}
        for b in si.bundle_options:
            for p in si.premium_options:
                for d in si.discount_options:
                    t = self.compute(service, si, quantity, bundle=b, premium=p, discount=d)
                    out.setdefault(t["effective_unit_rate_cents"], t["expected_line_total_cents"])
        return out

    def variants(self, service: str, si: StageInputs, quantity: int) -> dict[str, tuple[int, ...]]:
        """Rates under single-stage deviations -- used only to *name* a mismatch."""
        def rate(**kw) -> int:
            return self.compute(service, si, quantity, **kw)["effective_unit_rate_cents"]

        v: dict[str, tuple[int, ...]] = {}
        if si.bundle_partner:
            v["bundle_not_applied" if si.bundle_options[0] else "bundle_incorrectly_applied"] = (
                rate(bundle=not si.bundle_options[0]),)
        if si.premium_options[0]:
            v["premium_omitted"] = (rate(premium=Decimal(0)),)
        elif si.premium_available:
            v["premium_incorrectly_applied"] = (rate(premium=si.premium_available),)
        if si.discount_options[0]:
            v["volume_discount_omitted"] = (rate(discount=Decimal(0)),)
        wrong = tuple(rate(discount=f) for f in si.discount_available if f != si.discount_options[0])
        if wrong:
            v["volume_discount_incorrectly_applied"] = wrong
        return v


# ==========================================================================
# 5. Duplicates (clause 11.3)
# ==========================================================================

@dataclass
class DuplicateState:
    certain: dict[str, Finding]
    #: line id -> the unresolved lines that might be an earlier billing of it
    possible: dict[str, list[str]]
    #: line id -> the line id its billing repeats
    first_of: dict[str, str]


def find_duplicates(ctx: AuditContext) -> DuplicateState:
    certain: dict[str, Finding] = {}
    possible: dict[str, list[str]] = {}
    first_of: dict[str, str] = {}
    groups: dict[tuple, list[ResolvedLine]] = defaultdict(list)
    for rl in ctx.lines:
        if rl.service is not None and rl.service_date is not None:
            groups[(rl.patient_id, rl.service, rl.service_date)].append(rl)
    for (patient, svc, date), lines in groups.items():
        ordered = sorted(lines, key=_billing_order)
        first = ordered[0]
        for rl in ordered[1:]:
            same = rl.occurrence.occurrence_id == first.occurrence.occurrence_id
            first_of[rl.line_id] = first.line_id
            certain[rl.line_id] = Finding(
                "duplicate_service" if same else "cross_invoice_duplicate",
                f"{svc!r} already billed for patient {patient} on {date} (line {first.line_id} of "
                f"invoice {first.occurrence.invoice_id}); clause 11.3 forbids a repeat",
                rl.line_id)
        # A resolved line might itself be the repeat of an unresolved one billed earlier.
        for rl in ordered[:1]:
            earlier = [p.line_id for p in ctx.possible_same_day_lines(patient, svc, date)
                       if _billing_order(p) < _billing_order(rl)]
            if earlier:
                possible[rl.line_id] = earlier
    return DuplicateState(certain, possible, first_of)


# ==========================================================================
# 6. Line audit
# ==========================================================================

@dataclass
class LineOutcome:
    findings: list[Finding] = field(default_factory=list)
    #: The corrected line total, where the contract determines it.
    payable: Optional[int] = None
    #: The most the payer could owe for the line, where that is known.
    max_payable: Optional[int] = None
    #: A diagnostic amount, never submitted (see ``H4InvoiceResult``).
    provisional: Optional[int] = None
    reasons: list[str] = field(default_factory=list)
    trace: dict = field(default_factory=dict)
    #: The corrected amount depends on something not resolved.
    depends_on_unresolved: bool = False


class H4Auditor:
    def __init__(self, contract: H4Contract, policy: Optional[AuditPolicy] = None):
        self.contract = contract
        self.policy = policy or AuditPolicy()
        self.engine = PricingEngine(contract)
        self.term = (_dt.date.fromisoformat(contract.effective_from),
                     _dt.date.fromisoformat(contract.effective_to))

    # -- identity-independent checks -----------------------------------------

    def line_checks(self, rl: ResolvedLine) -> list[Finding]:
        f: list[Finding] = []
        line, occ = rl.line, rl.occurrence
        if line.service_date is None:
            f.append(Finding("malformed_service_date", f"service date {line.service_date_raw!r} is not a valid date",
                             line.line_id, blocks_reconstruction=True))
        elif not (self.term[0] <= line.service_date <= self.term[1]):
            f.append(Finding("service_date_out_of_window",
                             f"service date {line.service_date} is outside the term {self.term[0]} to "
                             f"{self.term[1]} (clause 11.2)", line.line_id))
        elif occ.invoice_date is not None and line.service_date > occ.invoice_date:
            # A date past the end of the term is usually also past the invoice
            # date: one defect, reported once, as the more specific term breach.
            f.append(Finding("service_date_after_invoice_date",
                             f"service date {line.service_date} is after the invoice date {occ.invoice_date} "
                             "(clause 11.2)", line.line_id))
        if line.unit_price_cents * line.quantity != line.line_total_cents:
            f.append(Finding("line_total_arithmetic",
                             f"line total {line.line_total_cents} != {line.unit_price_cents} x {line.quantity} "
                             "(clause 4.4)", line.line_id))
        return f

    # -- exclusion windows -----------------------------------------------------

    def exclusion(self, rl: ResolvedLine, ctx: AuditContext) -> tuple[Optional[Finding], list[dict], list[str]]:
        """Clause 9.1: same patient, either direction, "within N days" inclusive.

        Only a *certain* trigger proves a violation.  A trigger that is merely
        possible (an unresolved description) is recorded as uncertainty.
        """
        date = rl.service_date
        relations: list[dict] = []
        reasons: list[str] = []
        finding = None

        def within(delta: int, days: int) -> bool:
            return delta <= days if self.policy.exclusion_window_inclusive else delta < days

        for w in self.contract.exclusions_for(rl.service):
            certain = sorted(d for d in ctx.dates_for(rl.patient_id, w.other_service)
                             if within(abs((date - d).days), w.days))
            maybe = sorted(d for d in ctx.dates_for(rl.patient_id, w.other_service, certain_only=False)
                           if within(abs((date - d).days), w.days) and d not in certain)
            relations.append({"excluded_by": w.other_service, "window_days": w.days,
                              "certain_trigger_dates": [str(d) for d in certain],
                              "possible_trigger_dates": [str(d) for d in maybe]})
            if certain and finding is None:
                finding = Finding(
                    "exclusion_window_violation",
                    f"{rl.service!r} on {date} is within {w.days} days of {w.other_service!r} on {certain[0]} "
                    f"({abs((date - certain[0]).days)} day(s) apart; clause 9.1, either direction)", rl.line_id)
            elif maybe and not certain:
                reasons.append(f"{w.other_service!r} may have been delivered within {w.days} days "
                               "(an unresolved description); a violation is not asserted")
        return finding, relations, reasons

    # -- one line --------------------------------------------------------------

    def audit_line(self, rl: ResolvedLine, ctx: AuditContext, dups: DuplicateState) -> LineOutcome:
        line = rl.line
        out = LineOutcome(findings=self.line_checks(rl))
        out.trace = {
            "line_id": line.line_id,
            "raw_description": line.description,
            "normalized_description": normalize_description(line.description),
            "canonical_key": rl.canonical_key,
            "identity": {"status": rl.status, "service": rl.service, "method": rl.method,
                         "candidates": list(rl.candidates),
                         "used_unit_basis_for_identity": rl.used_unit_basis_for_identity},
            "billed": {"service_date": line.service_date_raw, "quantity": line.quantity,
                       "unit_basis": rl.billed_basis, "unit_price_cents": line.unit_price_cents,
                       "line_total_cents": line.line_total_cents},
        }
        if line.service_date is None:
            out.reasons.append(f"{line.line_id}: malformed service date, so the Service Day rules cannot "
                               "be evaluated with certainty")

        if rl.status == UNKNOWN:
            out.findings.append(Finding(
                "unknown_service",
                f"description {line.description!r} does not identify any contracted service "
                f"({rl.method})", line.line_id, blocks_reconstruction=True))
            out.reasons.append(f"{line.line_id}: service not contracted, so no contract price exists")
            out.depends_on_unresolved = True
            out.provisional = line.unit_price_cents * line.quantity
            out.trace["pricing"] = None
            return self._finish(out)

        if rl.status == AMBIGUOUS:
            return self._finish(self._audit_ambiguous(rl, ctx, out))

        service = rl.service
        svc = self.contract.services[service]
        if rl.billed_basis != svc.unit_basis and not rl.used_unit_basis_for_identity:
            out.findings.append(Finding(
                "wrong_unit_basis", f"billed {rl.billed_basis!r}; clause 3.1 states {svc.unit_basis_text!r} "
                f"({svc.unit_basis}) for {service!r}", line.line_id))

        # -- repeats and exclusions: the line is not payable ---------------
        not_payable = False
        dup = dups.certain.get(line.line_id)
        out.trace["duplicate"] = {"repeat_of": dups.first_of.get(line.line_id),
                                  "possible_earlier_unresolved": dups.possible.get(line.line_id, [])}
        if dup is not None:
            out.findings.append(dup)
            not_payable = True
        elif line.line_id in dups.possible:
            out.reasons.append(f"{line.line_id}: an unresolved line billed earlier the same day might be the "
                               f"same service, which would make this line a repeat under clause 11.3")
            out.depends_on_unresolved = True
        exclusion_relations: list[dict] = []
        if line.service_date is not None:
            excl, exclusion_relations, excl_reasons = self.exclusion(rl, ctx)
            if excl is not None:
                out.findings.append(excl)
                not_payable = True
            elif excl_reasons:
                out.reasons.extend(f"{line.line_id}: {r}" for r in excl_reasons)
                out.depends_on_unresolved = True
        out.trace["exclusions"] = exclusion_relations

        # -- clause 4.1 pricing ---------------------------------------------
        si = self.engine.stage_inputs(rl, service, ctx)
        out.reasons.extend(f"{line.line_id}: {r}" for r in si.uncertainty)
        if not si.certain:
            out.depends_on_unresolved = True

        # -- daily limit (6.1, 6.2) -------------------------------------------
        payable_qty = line.quantity
        cap = self.contract.daily_caps.get(service)
        cap_trace = None
        cap_blocks = False
        if cap is not None:
            cap_trace = {"max_units": cap.max_units, "day_quantity": None, "breached": None}
            if line.service_date is not None:
                agg = ctx.daily_quantity(rl.patient_id, service, line.service_date)
                cap_trace["day_quantity"] = agg.as_list()
                if agg.low > cap.max_units:
                    cap_trace["breached"] = True
                    cap_blocks = not self.policy.cap_excess_priced_at_cap
                    out.findings.append(Finding(
                        "daily_cap_exceeded",
                        f"{agg.low} units of {service!r} billed for patient {rl.patient_id} on "
                        f"{line.service_date}; clause 6.1 limits this to {cap.max_units}",
                        line.line_id, blocks_reconstruction=cap_blocks))
                    # 11.3 leaves at most one payable line per patient, service
                    # and day; the others are repeats and pay nothing.  So the
                    # limit applies to this line alone -- no allocation between
                    # competing lines is invented.
                    payable_qty = min(cap.max_units, line.quantity)
                    if cap_blocks:
                        out.reasons.append(f"{line.line_id}: the limit proves the quantity wrong; only the "
                                           "capped amount is a ceiling (policy: decline to reconstruct)")
                    elif payable_qty < line.quantity:
                        out.reasons.append(f"{line.line_id}: {line.quantity - payable_qty} unit(s) above the "
                                           f"clause 6.1 limit are not payable; payable quantity {payable_qty}")
                elif agg.high > cap.max_units:
                    cap_trace["breached"] = "uncertain"
                    out.reasons.append(f"{line.line_id}: Service Day quantity lies in {agg.as_list()}, "
                                       f"straddling the limit {cap.max_units}; a breach is not asserted")
                else:
                    cap_trace["breached"] = False
        if not_payable:
            payable_qty = 0

        trace = self.engine.compute(service, si, payable_qty)
        plausible_rates = self.engine.plausible(service, si, line.quantity)
        rate_ok = line.unit_price_cents in plausible_rates
        if not rate_ok:
            out.findings.append(self._name_mismatch(rl, service, si, trace))

        if si.certain:
            out.payable = trace["expected_line_total_cents"]
        elif rate_ok:
            # Several readings survive but the provider billed one of them, so
            # that reading is used -- as a provisional figure only.
            rate = line.unit_price_cents
            out.payable = rate * payable_qty
        else:
            out.payable = None
        out.max_payable = out.payable
        out.provisional = out.payable
        if cap_blocks:
            out.payable = None

        out.trace["pricing"] = {
            **trace,
            "bundle": {"partner": si.bundle_partner,
                       "state": si.state(si.bundle_options, bool) if si.bundle_partner else "not_applicable"},
            "patient_day_quantity": si.day_quantity.as_list() if si.day_quantity else None,
            "premium": ({"threshold_more_than": si.premium_threshold, "uplift": str(si.premium_available),
                         "state": si.state(si.premium_options, bool)} if si.premium_threshold is not None else None),
            "prior_cumulative": si.prior_cumulative.as_list() if si.prior_cumulative else None,
            "discount": ({"tiers": [str(f) for f in si.discount_available],
                          "state": si.state(si.discount_options, bool)} if si.discount_available else None),
            "plausible_unit_rates_cents": sorted(plausible_rates),
            "cap": cap_trace,
            "not_payable": not_payable,
        }
        return self._finish(out)

    def _finish(self, out: LineOutcome) -> LineOutcome:
        out.trace["findings"] = [f.category for f in out.findings]
        out.trace["clauses"] = sorted({EVIDENCE_BASIS[f.category][1] for f in out.findings})
        out.trace["expected_line_total_cents"] = out.payable
        out.trace["depends_on_unresolved"] = out.depends_on_unresolved
        out.trace["uncertainty"] = list(out.reasons)
        return out

    def _name_mismatch(self, rl: ResolvedLine, service: str, si: StageInputs, trace: dict) -> Finding:
        billed = rl.line.unit_price_cents
        for category, rates in self.engine.variants(service, si, rl.line.quantity).items():
            if billed in rates:
                return Finding(category, f"{service!r} billed at {billed}; contract rate "
                               f"{trace['effective_unit_rate_cents']}; the billed figure is exactly the rate "
                               "with this one adjustment wrong", rl.line_id)
        return Finding("unit_price_mismatch", f"{service!r} billed at {billed}; contract rate "
                       f"{trace['effective_unit_rate_cents']} (clauses {', '.join(trace['clauses'])})", rl.line_id)

    def _audit_ambiguous(self, rl: ResolvedLine, ctx: AuditContext, out: LineOutcome) -> LineOutcome:
        """No service is chosen.  The weaker question the evidence can answer:
        is the billed rate right under *any* candidate reading?"""
        line = rl.line
        out.depends_on_unresolved = True
        if len(rl.candidates) == 1:
            out.reasons.append(f"{line.line_id}: {line.description!r} is unresolved ({rl.method}): the only "
                               f"contracted service it fits is {rl.candidates[0]}, but the text does not rule "
                               "out an uncontracted service")
        else:
            out.reasons.append(f"{line.line_id}: {line.description!r} is unresolved ({rl.method}) between "
                               f"{', '.join(rl.candidates)}")
        bases = {self.contract.services[s].unit_basis for s in rl.candidates}
        if rl.billed_basis not in bases:
            out.findings.append(Finding(
                "wrong_unit_basis", f"billed {rl.billed_basis!r}; no plausible reading "
                f"({', '.join(rl.candidates)}) is billed on that basis", line.line_id))

        per_candidate = {}
        consistent: set[int] = set()
        for s in rl.candidates:
            si = self.engine.stage_inputs(rl, s, ctx)
            rates = self.engine.plausible(s, si, line.quantity)
            per_candidate[s] = sorted(rates)
            if line.unit_price_cents in rates:
                consistent.add(rates[line.unit_price_cents])
        out.trace["pricing"] = {"candidate_unit_rates_cents": per_candidate}
        if consistent:
            out.provisional = consistent.pop() if len(consistent) == 1 else None
            return out
        if len(rl.candidates) == 1:
            s = rl.candidates[0]
            si = self.engine.stage_inputs(rl, s, ctx)
            f = self._name_mismatch(rl, s, si, self.engine.compute(s, si, line.quantity))
            f.detail += f" (the only contracted reading of {line.description!r}; identity not certain)"
            out.findings.append(f)
        else:
            out.findings.append(Finding(
                "unit_price_mismatch", f"billed {line.unit_price_cents} matches no contract rate under any "
                f"reading of {line.description!r} ({', '.join(rl.candidates)})", line.line_id))
        return out


# ==========================================================================
# 7. Invoice audit
# ==========================================================================

@dataclass
class OccurrenceAudit:
    """One physical invoice record.  Never merged with another occurrence."""

    occurrence: InvoiceOccurrence
    findings: list[Finding]
    expected_total_cents: Optional[int]
    max_payable_total_cents: Optional[int]
    provisional_total_cents: Optional[int]
    depends_on_unresolved: bool
    reasons: list[str]
    traces: list[dict]
    lines: list[ResolvedLine]


@dataclass
class H4InvoiceResult:
    """The claim about one invoice number, plus what the submission cannot hold.

    ``result`` is the submitted row and describes the *represented* (latest)
    occurrence only.  ``occurrences`` keeps every physical record.
    ``provisional_expected_total_cents`` is a diagnostic -- for example an
    unknown line carried at its billed amount -- and is never submitted.
    """

    result: AuditResult
    occurrences: list[OccurrenceAudit]
    provisional_expected_total_cents: Optional[int] = None

    @property
    def represented(self) -> OccurrenceAudit:
        return self.occurrences[-1]


def audit_occurrence(occ: InvoiceOccurrence, lines: list[ResolvedLine], ctx: AuditContext,
                     auditor: H4Auditor, dups: DuplicateState, *, first: Optional[InvoiceOccurrence]) -> OccurrenceAudit:
    c = auditor.contract
    findings: list[Finding] = []
    reasons: list[str] = []
    if occ.contract_number != c.contract_number:
        findings.append(Finding("contract_number_mismatch", f"invoice quotes {occ.contract_number!r}; this "
                                f"agreement is {c.contract_number!r} (clause 11.1)"))
    if occ.facility_code != c.facility_code:
        findings.append(Finding("facility_mismatch", f"facility {occ.facility_code!r}; services are delivered "
                                f"from {c.facility_code!r} (clause 2.2)"))
    if first is not None:
        findings.append(Finding("duplicate_invoice_id", f"invoice number {occ.invoice_id!r} was already used by the "
                                f"record dated {first.invoice_date_raw} for patient {first.patient_id} (clause 11.1)"))
    summed = sum(l.line_total_cents for l in occ.line_items)
    if summed != occ.invoice_total_cents:
        findings.append(Finding("invoice_total_mismatch", f"invoice total {occ.invoice_total_cents} != sum of line "
                                f"totals {summed} (clause 4.4)"))

    payable: Optional[int] = 0
    ceiling: Optional[int] = 0
    provisional: Optional[int] = 0
    unresolved = False
    traces = []
    for rl in lines:
        o = auditor.audit_line(rl, ctx, dups)
        findings.extend(o.findings)
        reasons.extend(o.reasons)
        unresolved = unresolved or o.depends_on_unresolved
        traces.append(o.trace)
        payable = None if (payable is None or o.payable is None) else payable + o.payable
        ceiling = None if (ceiling is None or o.max_payable is None) else ceiling + o.max_payable
        provisional = None if (provisional is None or o.provisional is None) else provisional + o.provisional
    return OccurrenceAudit(occ, findings, payable, ceiling, provisional, unresolved, reasons, traces, lines)


def combine(invoice_id: str, audits: list[OccurrenceAudit]) -> H4InvoiceResult:
    """One submitted row per invoice number, describing its latest occurrence.

    ``pricing_complete``: every line of the represented occurrence has an
    exact contract price from resolved identities and settled inputs.
    ``correction_reconstructable``: additionally, no finding blocks
    reconstruction (a malformed date, an unknown service).
    ``expected_total_cents`` is filled only when reconstructable.
    """
    subject = audits[-1]
    findings = list(subject.findings)
    exact = subject.expected_total_cents is not None and not subject.depends_on_unresolved
    reconstructable = exact and not any(f.blocks_reconstruction for f in findings)
    reasons = list(dict.fromkeys(subject.reasons))
    if len(audits) > 1:
        reasons.append("invoice number reused: this row represents occurrence "
                       f"{subject.occurrence.occurrence_id}; earlier occurrence(s) "
                       + ", ".join(f"{a.occurrence.occurrence_id} ({len(a.findings)} finding(s))" for a in audits[:-1])
                       + " are audited separately in findings.csv")
    if subject.provisional_total_cents is not None and not reconstructable:
        reasons.append(f"a provisional total of {subject.provisional_total_cents} was computed but is not "
                       "contractually defensible, so no corrected total is submitted")
    result = AuditResult(
        invoice_id=invoice_id,
        occurrence_ids=[a.occurrence.occurrence_id for a in audits],
        billed_total_cents=subject.occurrence.invoice_total_cents,
        flagged=bool(findings),
        findings=findings,
        expected_total_cents=subject.expected_total_cents if reconstructable else None,
        maximum_contractually_payable_total_cents=(
            None if subject.depends_on_unresolved else subject.max_payable_total_cents),
        pricing_complete=exact,
        correction_reconstructable=reconstructable,
        uncertainty_reasons=reasons,
    )
    band, value, why = assess_confidence(result, subject)
    result.confidence_band, result.confidence = band, value
    result.uncertainty_reasons = list(dict.fromkeys(result.uncertainty_reasons + why))
    return H4InvoiceResult(result, audits, subject.provisional_total_cents)


# ==========================================================================
# 8. Confidence
# ==========================================================================
#
# Evidence strength for review priority, not a probability.  Hospital 4 has no
# labels, so nothing here is calibrated and nothing claims an accuracy.

BAND_VALUES = {"high": 0.85, "medium": 0.65, "low": 0.40}

#: Findings whose correctness depends on hospital-wide state or on a recorded
#: implementation policy rather than on the line alone.
_STATEFUL = frozenset({
    "bundle_not_applied", "bundle_incorrectly_applied", "premium_omitted", "premium_incorrectly_applied",
    "volume_discount_omitted", "volume_discount_incorrectly_applied", "daily_cap_exceeded",
    "exclusion_window_violation", "duplicate_service", "cross_invoice_duplicate",
})


def assess_confidence(result: AuditResult, subject: OccurrenceAudit) -> tuple[str, float, list[str]]:
    cats = set(result.error_categories)
    unresolved = any(rl.status == AMBIGUOUS for rl in subject.lines)
    tiebreak = any(rl.used_unit_basis_for_identity for rl in subject.lines)
    why: list[str] = []
    if result.flagged:
        if cats <= STRUCTURAL:
            why.append("every finding follows from the invoice's own fields, independent of service identity")
            return "high", BAND_VALUES["high"], why
        if "unknown_service" in cats or not result.pricing_complete:
            why.append("a finding rests on reading free text, or the corrected total cannot be reconstructed")
            return "low", BAND_VALUES["low"], why
        if cats & _STATEFUL or tiebreak:
            why.append("findings apply contract rules to resolved services but depend on hospital-wide state, "
                       "a recorded policy or a unit-basis tie-break")
            return "medium", BAND_VALUES["medium"], why
        why.append("findings apply contract rules to services identified from text, priced exactly")
        return "high", BAND_VALUES["high"], why
    if unresolved:
        why.append("not flagged, but some lines could not be identified, so not every rule could be checked")
        return "low", BAND_VALUES["low"], why
    if tiebreak or not result.correction_reconstructable:
        why.append("not flagged; identity rests partly on a unit-basis tie-break, or the total is not reconstructable")
        return "medium", BAND_VALUES["medium"], why
    why.append("not flagged; every line identified from text and priced exactly")
    return "high", BAND_VALUES["high"], why


# ==========================================================================
# 9. Pipeline
# ==========================================================================

class H4Pipeline:
    """Files -> results.  Offline and deterministic: calls nothing."""

    def __init__(self, policy: Optional[AuditPolicy] = None, *, check_csv: bool = True):
        self.contract = parse_contract()
        self.matcher = H4Matcher(self.contract)
        self.auditor = H4Auditor(self.contract, policy)
        self.occurrences = load_occurrences(JSONL)
        if check_csv:
            problems = cross_check_against_csv(self.occurrences, INVOICES_CSV, LINES_CSV)
            if problems:
                raise ValueError("hospital_4 JSONL and CSV disagree:\n  " + "\n  ".join(problems[:20]))
        self.resolved = resolve_lines(self.occurrences, self.matcher)

    def run(self) -> list[H4InvoiceResult]:
        return audit_resolved(self.occurrences, self.resolved, self.auditor)


def audit_resolved(occurrences: list[InvoiceOccurrence], resolved: list[ResolvedLine],
                   auditor: H4Auditor) -> list[H4InvoiceResult]:
    """Audit every occurrence against one hospital-wide context.  Occurrences
    sharing an invoice number are audited separately, in file order; the first
    establishes the number and each later one is a repeat (clause 11.1)."""
    ctx = AuditContext(resolved)
    dups = find_duplicates(ctx)
    by_occ: dict[str, list[ResolvedLine]] = defaultdict(list)
    for rl in resolved:
        by_occ[rl.occurrence.occurrence_id].append(rl)
    by_id: dict[str, list[InvoiceOccurrence]] = defaultdict(list)
    for occ in occurrences:
        by_id[occ.invoice_id].append(occ)
    results = []
    for invoice_id in sorted(by_id):
        occs = sorted(by_id[invoice_id], key=lambda o: o.occurrence_index)
        audits = [audit_occurrence(o, by_occ[o.occurrence_id], ctx, auditor, dups,
                                   first=occs[0] if i else None) for i, o in enumerate(occs)]
        results.append(combine(invoice_id, audits))
    return results


# ==========================================================================
# 10. Artifacts and outputs
# ==========================================================================

def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _provenance(pipeline: H4Pipeline) -> dict:
    return {"contract_fingerprint": pipeline.contract.fingerprint, "matcher_version": MATCHER_VERSION}


def cluster_records(pipeline: H4Pipeline) -> list[dict]:
    """One record per canonical description cluster, sorted by key."""
    lines_by_key: dict[str, list[ResolvedLine]] = defaultdict(list)
    for rl in pipeline.resolved:
        lines_by_key[rl.canonical_key].append(rl)
    clusters = cluster_descriptions((rl.line.description for rl in pipeline.resolved), pipeline.matcher)
    records = []
    for key in sorted(clusters):
        c, det, rls = clusters[key], clusters[key]["deterministic"], lines_by_key[key]
        records.append({
            "canonical_key": key,
            "raw_examples": c["raw_descriptions"][:10],
            "distinct_raw_descriptions": len(c["raw_descriptions"]),
            "normalized_descriptions": c["normalized_descriptions"][:10],
            "line_occurrences": len(rls),
            "invoice_count": len({rl.occurrence.invoice_id for rl in rls}),
            "deterministic_status": det.status,
            "method": det.method,
            "selected_service": det.service,
            "candidates": [
                {"service": x.service, "unit_basis": x.unit_basis, "qualifier_evidenced": x.qualifier_evidenced,
                 "specialty_evidenced": x.specialty_evidenced,
                 "concept_words_evidenced": list(x.concept_words_evidenced), "missing_words": list(x.missing_words)}
                for x in det.candidates],
            "evidence": det.evidence,
            "unit_basis_tiebreak_line_occurrences": sum(rl.used_unit_basis_for_identity for rl in rls),
            "unit_basis_tiebreak_services": dict(Counter(rl.service for rl in rls if rl.used_unit_basis_for_identity)),
            "ambiguity_reason": _AMBIGUITY_REASONS.get(det.method),
        })
    return records


_AMBIGUITY_REASONS = {
    "missing_discriminator": "only one contracted service fits, but the text omits its qualifier, specialty "
                             "or concept, so an uncontracted service of the same shape cannot be ruled out",
    "tied_candidates": "several contracted services fit the text equally",
    "unrecognised_token": "a word in the description could not be read",
    "contradiction": "a recognised word contradicts every contracted service",
    "no_recognised_words": "no word in the description is recognised",
}


def summary_counts(pipeline: H4Pipeline, records: list[dict]) -> dict[str, int]:
    resolved = pipeline.resolved
    by_status = Counter(r["deterministic_status"] for r in records)
    return {
        "distinct_raw_descriptions": len({rl.line.description for rl in resolved}),
        "distinct_normalized_clusters": len(records),
        "matched_clusters": by_status.get(MATCHED, 0),
        "ambiguous_clusters": by_status.get(AMBIGUOUS, 0),
        "unknown_clusters": by_status.get(UNKNOWN, 0),
        "total_line_occurrences": len(resolved),
        "matched_line_occurrences": sum(rl.status == MATCHED for rl in resolved),
        "matched_by_text_line_occurrences": sum(rl.status == MATCHED and not rl.used_unit_basis_for_identity
                                                for rl in resolved),
        "unit_basis_tiebreak_line_occurrences": sum(rl.used_unit_basis_for_identity for rl in resolved),
        "ambiguous_line_occurrences": sum(rl.status == AMBIGUOUS for rl in resolved),
        "unknown_line_occurrences": sum(rl.status == UNKNOWN for rl in resolved),
        "invoice_numbers_with_ambiguous_line": len({rl.occurrence.invoice_id for rl in resolved
                                                   if rl.status == AMBIGUOUS}),
        "invoice_numbers_with_unknown_line": len({rl.occurrence.invoice_id for rl in resolved
                                                 if rl.status == UNKNOWN}),
    }


def write_identity_artifacts(pipeline: H4Pipeline, directory: Path = ARTIFACTS) -> dict[str, int]:
    """description_clusters.json, service_mappings.json, unresolved.json."""
    records = cluster_records(pipeline)
    counts = summary_counts(pipeline, records)
    prov = _provenance(pipeline)
    _write_json(directory / "description_clusters.json", {
        **prov, "abbreviations": ABBREVIATIONS, "counts": counts, "clusters": records})
    _write_json(directory / "service_mappings.json", {
        **prov,
        "note": "Deterministic decisions only. Hospital 4 has no semantic (LLM) stage. A cluster's status is the "
                "text decision; a line of a tied cluster may still be resolved by the unit-basis tie-break.",
        "mappings": {r["canonical_key"]: {"status": r["deterministic_status"], "service": r["selected_service"],
                                          "method": r["method"],
                                          "candidates": [x["service"] for x in r["candidates"]],
                                          "unit_basis_tiebreak_services": r["unit_basis_tiebreak_services"]}
                     for r in records}})
    unresolved = []
    for r in records:
        if r["deterministic_status"] == MATCHED:
            continue
        families = sorted({f for x in r["candidates"] for f in pipeline.contract.rules_touching(x["service"])})
        still = r["line_occurrences"] - r["unit_basis_tiebreak_line_occurrences"]
        unresolved.append({
            "canonical_key": r["canonical_key"], "status": r["deterministic_status"], "method": r["method"],
            "reason": r["ambiguity_reason"], "raw_examples": r["raw_examples"][:5],
            "line_occurrences": r["line_occurrences"],
            "line_occurrences_still_unresolved_after_tiebreak": still,
            "invoice_count": r["invoice_count"],
            "candidates": [x["service"] for x in r["candidates"]],
            "candidate_rule_families": families,
        })
    unresolved.sort(key=lambda u: (-u["line_occurrences_still_unresolved_after_tiebreak"], -u["invoice_count"],
                                   -len(u["candidate_rule_families"]), u["canonical_key"]))
    _write_json(directory / "unresolved.json", {
        **prov,
        "ranking": "line occurrences still unresolved after the unit-basis tie-break, then affected invoices, "
                   "then how many rule families the candidates touch",
        "count": len(unresolved), "unresolved": unresolved})
    return counts


PREDICTION_COLUMNS = SUBMISSION_COLUMNS + UNCERTAINTY_COLUMNS + ("provisional_expected_total_cents",)


def _blank(value) -> object:
    return "" if value is None else value


def write_predictions(results: list[H4InvoiceResult], path: Path) -> None:
    """Submission columns, uncertainty columns, and the diagnostic provisional total."""
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


def write_findings(results: list[H4InvoiceResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["invoice_id", "occurrence_id", "line_id", "category", "evidence_basis", "clause_reference",
                    "blocks_reconstruction", "detail"])
        for h in results:
            for a in h.occurrences:
                for f in a.findings:
                    basis, ref = EVIDENCE_BASIS[f.category]
                    w.writerow([h.result.invoice_id, a.occurrence.occurrence_id, f.line_id or "", f.category,
                                basis, ref, int(f.blocks_reconstruction), f.detail])


def _drop_empty(obj):
    if isinstance(obj, dict):
        out = {k: _drop_empty(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {}, False, "")}
    if isinstance(obj, list):
        return [_drop_empty(v) for v in obj]
    return obj


def compact_trace(t: dict) -> dict:
    """The trace as written to ``pricing_traces.jsonl``.

    Same content as the in-memory trace, without repetition: the clause 4.1
    stages become one ``stages_cents`` object (base -> bundle -> facility ->
    plan -> premium -> discount), exclusion windows with no trigger nearby are
    omitted, and empty, null or false fields are dropped (absent means none /
    false).  The facility and plan-tier multipliers are 1 for every line
    (clause 2.2); the stages still show both steps.
    """
    t = dict(t)
    t.pop("normalized_description", None)
    ident = dict(t["identity"])
    if ident["status"] == MATCHED and ident["candidates"] == [ident["service"]]:
        ident.pop("candidates")
    t["identity"] = ident
    t["exclusions"] = [e for e in t.get("exclusions", []) if e["certain_trigger_dates"] or e["possible_trigger_dates"]]
    p = t.get("pricing")
    if p and "base_rate_cents" in p:
        p = dict(p)
        p["stages_cents"] = {"base": p.pop("base_rate_cents"), "bundle": p.pop("after_bundle_cents"),
                             "facility": p.pop("after_facility_cents"), "plan": p.pop("after_plan_cents"),
                             "premium": p.pop("after_premium_cents"), "discount": p.pop("effective_unit_rate_cents")}
        for k in ("facility_multiplier", "plan_multiplier", "service", "expected_line_total_cents"):
            p.pop(k, None)
        if p.get("plausible_unit_rates_cents") == [p["stages_cents"]["discount"]]:
            p.pop("plausible_unit_rates_cents")
        if p.get("bundle", {}).get("state") == "not_applicable":
            p.pop("bundle")
        for k in ("premium_fraction", "discount_fraction"):
            if p.get(k) == "0":
                p.pop(k)
        t["pricing"] = p
    return _drop_empty(t)


def write_traces(results: list[H4InvoiceResult], path: Path) -> int:
    """One trace per line of every occurrence -- successful, incomplete or unknown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for h in results:
            for a in h.occurrences:
                for t in a.traces:
                    fh.write(json.dumps({"invoice_id": h.result.invoice_id,
                                         "occurrence_id": a.occurrence.occurrence_id, **compact_trace(t)},
                                        separators=(",", ":")) + "\n")
                    n += 1
    return n


def _money(v) -> str:
    return "—" if v is None else f"{v:,}"


def _line_block(t: dict) -> list[str]:
    ident, billed, p = t["identity"], t["billed"], t.get("pricing") or {}
    rows = [
        ("description", f"`{t['raw_description']}` → `{t['canonical_key']}`"),
        ("identity", f"{ident['status']} ({ident['method']})"
                     + (f": **{ident['service']}**" if ident["service"] else "")),
        ("candidates", ", ".join(ident["candidates"]) or "none"),
        ("unit basis used for identity", "yes" if ident["used_unit_basis_for_identity"] else "no"),
        ("billed", f"{billed['quantity']} × {billed['unit_basis']} @ {_money(billed['unit_price_cents'])} = "
                   f"{_money(billed['line_total_cents'])} on {billed['service_date']}"),
    ]
    if "base_rate_cents" in p:
        rows += [
            ("base rate", _money(p["base_rate_cents"])),
            ("bundle", f"{p['bundle']['state']}" + (f" (partner {p['bundle']['partner']})" if p["bundle"]["partner"] else "")),
            ("facility / plan multipliers", f"{p['facility_multiplier']} / {p['plan_multiplier']}"),
            ("patient-day quantity", str(p["patient_day_quantity"]) if p["patient_day_quantity"] else "—"),
            ("premium", f"{p['premium']['state']} (> {p['premium']['threshold_more_than']}, "
                        f"+{p['premium']['uplift']})" if p["premium"] else "none contracted"),
            ("prior cumulative utilisation", str(p["prior_cumulative"]) if p["prior_cumulative"] else "—"),
            ("discount", f"{p['discount']['state']} (tiers {', '.join(p['discount']['tiers'])})"
                         if p["discount"] else "none contracted"),
            ("cap", f"{p['cap']['max_units']} per day; day quantity {p['cap']['day_quantity']}; "
                    f"breached {p['cap']['breached']}" if p["cap"] else "none contracted"),
            ("stages: bundle → facility → plan → premium → discount",
             f"{_money(p['after_bundle_cents'])} → {_money(p['after_facility_cents'])} → "
                       f"{_money(p['after_plan_cents'])} → {_money(p['after_premium_cents'])} → "
                       f"{_money(p['effective_unit_rate_cents'])}"),
            ("payable quantity", str(p["payable_quantity"])),
        ]
    elif p.get("candidate_unit_rates_cents"):
        rows.append(("candidate unit rates", "; ".join(f"{s}: {', '.join(map(_money, r))}"
                                                       for s, r in p["candidate_unit_rates_cents"].items())))
    if t.get("exclusions"):
        rel = [f"{e['excluded_by']} ±{e['window_days']}d: certain {e['certain_trigger_dates'] or '—'}, "
               f"possible {e['possible_trigger_dates'] or '—'}" for e in t["exclusions"]]
        rows.append(("exclusions", "; ".join(rel)))
    dup = t.get("duplicate") or {}
    if dup.get("repeat_of") or dup.get("possible_earlier_unresolved"):
        rows.append(("repeat", f"repeats {dup.get('repeat_of') or '—'}; possible earlier unresolved "
                               f"{dup.get('possible_earlier_unresolved') or '—'}"))
    rows += [
        ("expected line total", _money(t["expected_line_total_cents"])),
        ("findings", ", ".join(f"`{f}`" for f in t["findings"]) or "none"),
        ("clauses", "; ".join(t["clauses"]) or "—"),
    ]
    if t["uncertainty"]:
        rows.append(("notes and uncertainty", " ; ".join(t["uncertainty"])))
    return [f"**{t['line_id']}**", "", "| | |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows], ""]


def audit_report(pipeline: H4Pipeline, results: list[H4InvoiceResult], counts: dict[str, int]) -> str:
    res = [h.result for h in results]
    row_cats = Counter(c for r in res for c in r.error_categories)
    occ_cats = Counter(c for h in results for a in h.occurrences for c in dict.fromkeys(f.category for f in a.findings))
    bands = Counter(r.confidence_band for r in res)
    reused = [h for h in results if len(h.occurrences) > 1]
    c = pipeline.contract
    out = [
        "# Hospital 4 - audit report",
        "",
        "Generated by `python -m src.main audit h4`. **There are no Hospital 4 labels.** Nothing below is an "
        "accuracy figure; it describes what the audit found and how much of it rests on unresolved evidence. "
        "No model or external API is used anywhere in the Hospital 4 pipeline.",
        "",
        f"Contract `{c.contract_number}` ({c.effective_from} to {c.effective_to}), fingerprint "
        f"`{c.fingerprint[:16]}`, matcher `{MATCHER_VERSION}`.",
        "",
        "## Contract rules parsed",
        "",
        "| family | count |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in rule_counts(c).items()],
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
        "## Service identity (deterministic only)",
        "",
        "A **cluster** is one canonical description (abbreviations expanded, word order and `/CW-####` "
        "references removed). A **line occurrence** is one invoice line.",
        "",
        "| count | value |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in counts.items()],
        "",
        "`artifacts/hospital_4/unresolved.json` ranks every non-matched cluster by impact.",
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
        "| confidence band | invoices |",
        "|---|---|",
        *[f"| {b} ({BAND_VALUES[b]}) | {bands.get(b, 0)} |" for b in ("high", "medium", "low")],
        "",
        "Confidence is an evidence band for review priority, not a calibrated probability.",
        "",
        "## Findings by category",
        "",
        "`rows` counts submitted rows carrying the category; `occurrences` counts physical invoice records.",
        "",
        "| category | rows | occurrences | evidence basis | clause |",
        "|---|---|---|---|---|",
        *[f"| `{k}` | {row_cats.get(k, 0)} | {n} | {EVIDENCE_BASIS[k][0]} | {EVIDENCE_BASIS[k][1]} |"
          for k, n in sorted(occ_cats.items(), key=lambda kv: (-kv[1], kv[0]))],
        "",
        "## Reused invoice numbers",
        "",
        "Each row represents the later occurrence. The earlier occurrence stays in every hospital-wide index "
        "and its findings are in `findings.csv`; they are not copied onto the row.",
        "",
        "| invoice number | represented occurrence | row categories | earlier occurrence findings |",
        "|---|---|---|---|",
        *[f"| {h.result.invoice_id} | {h.represented.occurrence.occurrence_id} | "
          f"{', '.join(h.result.error_categories) or 'none'} | "
          + "; ".join(f"{a.occurrence.occurrence_id}: {', '.join(dict.fromkeys(f.category for f in a.findings)) or 'none'}"
                      for a in h.occurrences[:-1]) + " |" for h in reused],
        "",
        "## Flagged invoices, line by line",
        "",
        "For each flagged invoice: the row, then every line that carries a finding or an uncertainty. Every "
        "amount is in cents. The full stage-by-stage trace of every line, flagged or not, is in "
        "`artifacts/hospital_4/pricing_traces.jsonl`.",
        "",
    ]
    for h in results:
        r = h.result
        if not r.flagged:
            continue
        a = h.represented
        out += [
            f"### {r.invoice_id}",
            "",
            f"Occurrence `{a.occurrence.occurrence_id}`, patient {a.occurrence.patient_id}, invoice date "
            f"{a.occurrence.invoice_date_raw}, contract `{a.occurrence.contract_number}`. Billed "
            f"{_money(r.billed_total_cents)}; expected {_money(r.expected_total_cents)} (provisional "
            f"{_money(h.provisional_expected_total_cents)}); pricing_complete {int(r.pricing_complete)}, "
            f"reconstructable {int(r.correction_reconstructable)}; confidence {r.confidence:.2f} ({r.confidence_band}).",
            "",
            "Categories: " + ", ".join(f"`{x}`" for x in r.error_categories),
            "",
        ]
        invoice_level = [f for f in a.findings if f.line_id is None]
        if invoice_level:
            out += [*[f"- `{f.category}` ({EVIDENCE_BASIS[f.category][1]}): {f.detail}" for f in invoice_level], ""]
        for t in a.traces:
            if t["findings"] or t["uncertainty"]:
                out += _line_block(t)
    return "\n".join(out).rstrip("\n") + "\n"


def write_outputs(pipeline: H4Pipeline, results: list[H4InvoiceResult], *, template: Path,
                  outputs: Path = OUTPUTS, artifacts: Path = ARTIFACTS) -> dict:
    """Every Hospital 4 artifact and output, deterministically."""
    from ..submission import write_hospital_submission

    write_contract_rules(pipeline.contract, artifacts / "contract_rules.json")
    counts = write_identity_artifacts(pipeline, artifacts)
    n_traces = write_traces(results, artifacts / "pricing_traces.jsonl")
    write_predictions(results, outputs / "predictions.csv")
    write_findings(results, outputs / "findings.csv")
    rows = write_hospital_submission([h.result for h in results], outputs / "submission.csv", template=template)
    (outputs / "audit_report.md").write_text(audit_report(pipeline, results, counts), encoding="utf-8")
    return {"counts": counts, "traces": n_traces, "rows": rows}
