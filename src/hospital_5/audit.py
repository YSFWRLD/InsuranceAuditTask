"""Hospital 5: the audit.

Given the parsed contract, the reviewed semantic decisions and the invoices,
decide for every invoice whether it is wrong, why, and -- separately --
whether the correct total can be proven and what it is.  Every financial and
contractual decision here is Python.  Nothing calls a model; nothing reads a
label.

Contents
    1. Evidence bases and policy
    2. Line identity        structural match + tie-break + reviewed decision
    3. Global context       repeats, day aggregates, presence, cumulative use
    4. Pricing              clause 3.1 stage by stage, integer cents
    5. Candidate evaluation one line read as one candidate service
    6. Line audit           findings, and the SET of possible corrected totals
    7. Invoice audit        occurrences kept distinct; one row per invoice number
    8. Confidence           evidence bands -- NOT calibrated probabilities
    9. Pipeline

The idea that separates this audit from Hospitals 2 and 4 is **financial
equivalence**.  A line is not "known" or "unknown"; it carries the set of
corrected totals that every still-possible reading of it produces -- over
candidate services, and over every hospital-wide input its reading depends on
(a bundle partner that may or may not be there, a Service Day aggregate that
straddles a threshold, a cumulative position that straddles a tier, a repeat
or an exclusion trigger that may or may not exist).  ``None`` in that set means
"some reading has no contract price" (an uncontracted service, a cap breach
whose true quantity is unknowable).  An invoice total is submitted exactly
when every line's set is one known value -- whether or not the service name
itself was settled.

Dependency-aware uncertainty follows from the same construction: an
unresolved line enters only the indexes of its own candidate services, as a
*possible* (not certain) contribution, so it can only widen the rules those
services take part in.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from ..shared.data import cross_check_against_csv, load_occurrences
from ..shared.models import AuditResult, Finding, InvoiceOccurrence, LineItem
from ..shared.money import apply_percentage_change, multiply_cents
from .contract import H5Contract, parse_contract
from .matcher import (
    AMBIGUOUS, MATCHED, UNKNOWN, H5Matcher, LineIdentity, MissingWordDecision, StructuralMatch,
    resolve_line_identity,
)
from .normalization import Lexicon

REPO_ROOT = Path(__file__).resolve().parents[2]
JSONL = REPO_ROOT / "invoices" / "hospital_5_invoices.jsonl"
INVOICES_CSV = REPO_ROOT / "invoices" / "hospital_5_invoices.csv"
LINES_CSV = REPO_ROOT / "invoices" / "hospital_5_line_items.csv"
ARTIFACTS = REPO_ROOT / "artifacts" / "hospital_5"
OUTPUTS = REPO_ROOT / "outputs" / "hospital_5"

# ==========================================================================
# 1. Evidence bases and policy
# ==========================================================================

#: What each finding rests on, and the clause it cites.
#:   contract    a clause of the agreement, applied to fully known inputs
#:   arithmetic  the invoice disagrees with itself
#:   data        a field is not valid data at all
#:   inferred    rests on reading free text
EVIDENCE_BASIS = {
    "contract_number_mismatch": ("contract", "10.1"),
    "duplicate_invoice_id": ("contract", "10.1"),
    "unknown_facility": ("contract", "1.2"),
    "unknown_plan_tier": ("contract", "1.3"),
    "malformed_service_date": ("data", "2.1"),
    "service_date_out_of_window": ("contract", "10.2, 1.1"),
    "service_date_after_invoice_date": ("contract", "10.2"),
    "line_total_arithmetic": ("arithmetic", "3.1"),
    "invoice_total_mismatch": ("arithmetic", "3.1; the invoice total is the sum of its lines"),
    "unknown_service": ("inferred", "4 (Table 1); identity read from free text"),
    "wrong_unit_basis": ("contract", "4 (Table 1), 2.3"),
    "unit_price_mismatch": ("contract", "3.1-3.4, Tables 1-3"),
    "bundle_not_applied": ("contract", "7.1, 3.1(a)"),
    "bundle_incorrectly_applied": ("contract", "7.1, 3.1(a)"),
    "premium_omitted": ("contract", "5.1 or 6, 2.2, 3.1(d)"),
    "premium_incorrectly_applied": ("contract", "5.1 or 6, 2.2, 3.1(d)"),
    "volume_discount_omitted": ("contract", "8, 8.1, 3.1(e)"),
    "volume_discount_incorrectly_applied": ("contract", "8, 8.1, 3.1(e)"),
    "daily_cap_exceeded": ("contract", "4 (Table 1, Daily cap)"),
    "exclusion_window_violation": ("contract", "9"),
    "duplicate_service": ("contract", "10.3; first billing stands (implementation policy)"),
    "cross_invoice_duplicate": ("contract", "10.3; first billing stands (implementation policy)"),
}

#: Findings that follow from the invoice alone, without service identity.
STRUCTURAL = frozenset({
    "contract_number_mismatch", "duplicate_invoice_id", "unknown_facility", "unknown_plan_tier",
    "malformed_service_date", "service_date_out_of_window", "service_date_after_invoice_date",
    "line_total_arithmetic", "invoice_total_mismatch",
})


@dataclass(frozen=True)
class AuditPolicy:
    """Readings the agreement does not force.  One place, so the decision log
    and the code cannot drift apart."""

    #: Table 1 gives a "Daily cap" and nothing else: no clause says the excess
    #: is not payable (compare Hospital 4's clause 6.1).  A breach proves the
    #: billed quantity wrong; it does not reveal the right one.  Hospital 1's
    #: labels (the same bare-cap wording) corrected breaches to the pre-breach
    #: quantity, not the cap.  So: flag, record the cap-priced amount as a
    #: ceiling, and do not reconstruct.  ``False`` prices the capped quantity.
    cap_breach_blocks_reconstruction: bool = True

    #: Section 8 "Discount on subsequent units" + clause 8.1 (line-level
    #: ordering) + clause 3.1 (one unit rate per line):
    #:   ``line_prior``  a line is discounted at the tier that cumulative
    #:                   utilisation *before* it has exceeded; a line that
    #:                   crosses a threshold is not split and is not
    #:                   discounted until the next line.
    #:   ``unit_split``  each unit is discounted at the tier its own position
    #:                   exceeds; a crossing line carries two rates.
    discount_mode: str = "line_prior"

    #: Section 9 "not billable within N days of": the excluded service is the
    #: left-hand one (directed); the window is measured either side of the
    #: trigger, and day N is inside.
    exclusion_both_directions: bool = True
    exclusion_window_inclusive: bool = True

    #: An uncontracted service has no contract price.  ``True`` would carry the
    #: billed line at qty x unit price (what Hospital 1's labels did); the
    #: default refuses, because nothing in the agreement proves that amount.
    unknown_service_line_carried_at_billed: bool = False

    #: ``False`` reproduces the Hospital 4 reconstruction rule for the
    #: contribution report: a line counts only if its identity is MATCHED and
    #: every input is settled, and a malformed date always blocks.
    financial_equivalence: bool = True

    def __post_init__(self):
        if self.discount_mode not in ("line_prior", "unit_split"):
            raise ValueError(f"unknown discount_mode {self.discount_mode!r}")


# ==========================================================================
# 2. Line identity
# ==========================================================================

@dataclass(frozen=True)
class ResolvedLine:
    occurrence: InvoiceOccurrence
    line: LineItem
    match: StructuralMatch
    identity: LineIdentity

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
        return self.line.unit_basis_raw

    @property
    def status(self) -> str:
        return self.identity.status

    @property
    def service(self) -> Optional[str]:
        return self.identity.service

    @property
    def possible_services(self) -> tuple[str, ...]:
        return self.identity.possible_services

    def certainly(self, service: str) -> bool:
        return self.identity.status == MATCHED and self.identity.service == service


def resolve_lines(occurrences: Iterable[InvoiceOccurrence], matcher: H5Matcher,
                  decisions: Optional[dict[tuple[str, str], MissingWordDecision]] = None) -> list[ResolvedLine]:
    decisions = decisions or {}
    out = []
    for occ in occurrences:
        for line in occ.line_items:
            m = matcher.match(line.description)
            ident = resolve_line_identity(m, line.unit_basis_raw, decisions.get((m.identity_key, line.unit_basis_raw)))
            out.append(ResolvedLine(occ, line, m, ident))
    return out


# ==========================================================================
# 3. Global context
# ==========================================================================

@dataclass(frozen=True)
class Interval:
    """A quantity known only to lie in ``[low, high]``."""

    low: int = 0
    high: int = 0

    def plus(self, low: int, high: int) -> "Interval":
        return Interval(self.low + low, self.high + high)

    def as_list(self) -> list[int]:
        return [self.low, self.high]


def billing_order(rl: ResolvedLine) -> tuple:
    """Clause 10.3 forbids a repeat but does not say which record is the
    repeat.  Implementation policy (as Hospital 4): the first billing stands,
    by invoice date, then invoice identifier, then occurrence, then line id."""
    o = rl.occurrence
    return (o.invoice_date or _dt.date.max, o.invoice_id, o.occurrence_index, rl.line_id)


def cumulative_order(rl: ResolvedLine) -> tuple:
    """Clause 8.1: Service Date order, then ascending line identifier."""
    return (rl.service_date or _dt.date.max, rl.line_id)


NO, MAYBE, YES = "no", "maybe", "yes"

_ISO_LIKE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DAY_FIRST = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _days_in(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def possible_dates(raw: str) -> Optional[frozenset]:
    """The calendar days a malformed Service Date can stand for, read only from
    the parts of the string that do parse.  ``None``: nothing parses, so the
    line could be on any day.

    ``2025-06-31``  June 2025 is legible, day 31 is not -> every day of June
                    2025, plus 1 July (the overflow reading of "June 31")
    ``31/02/2024``  day first; February 2024 legible -> every February day,
                    plus 2 March
    ``2024-13-05``  month 13 is not legible -> the 5th of every month of 2024,
                    plus 13 May (day and month swapped)

    No price, invoice or neighbouring line is consulted: this is a reading of
    the recorded string, and every rule that depends on the day is then
    evaluated over all of these days.
    """
    text = (raw or "").strip()
    m = _ISO_LIKE.match(text)
    if m:
        year, month, day = map(int, m.groups())
    else:
        m = _DAY_FIRST.match(text)
        if not m:
            return None
        day, month, year = map(int, m.groups())
    if not 1900 <= year <= 2100:
        return None
    out: set = set()
    if 1 <= month <= 12:
        last = _days_in(year, month)
        out.update(_dt.date(year, month, d) for d in range(1, last + 1))
        if day > last:
            out.add(_dt.date(year, month, last) + _dt.timedelta(days=day - last))
    elif 1 <= day <= 31:
        out.update(_dt.date(year, mm, day) for mm in range(1, 13) if day <= _days_in(year, mm))
        if 1 <= day <= 12 and 1 <= month <= _days_in(year, day):
            out.add(_dt.date(year, day, month))
    else:
        out.update(_dt.date(year, 1, 1) + _dt.timedelta(days=k) for k in range(366)
                   if (_dt.date(year, 1, 1) + _dt.timedelta(days=k)).year == year)
    return frozenset(out) or None


def line_dates(rl: ResolvedLine) -> Optional[frozenset]:
    if rl.service_date is not None:
        return frozenset({rl.service_date})
    return possible_dates(rl.line.service_date_raw)


class AuditContext:
    """Every cross-line index, built once from every physical line occurrence.

    Everything is keyed by *service*, and an unresolved line enters only the
    keys of its own candidates, as a possible contribution.  All indexes span
    invoices: Service Day aggregates (5.1, the cap), bundles (7.1),
    cumulative utilisation (8.1), exclusions (9) and repeats (10.3) concern
    the patient or the agreement, never one invoice.

    Every day-based question goes through one relation, ``same_day``: two
    lines certainly share a Service Day, might, or cannot.  A line with a
    malformed date carries the set of days its string can stand for
    (``possible_dates``), so it widens only the days it could be on.
    """

    def __init__(self, lines: Iterable[ResolvedLine]):
        self.lines = list(lines)
        self.by_id = {rl.line_id: rl for rl in self.lines}
        self.dates: dict[str, Optional[frozenset]] = {rl.line_id: line_dates(rl) for rl in self.lines}
        self._group: dict[tuple[str, str], list[ResolvedLine]] = defaultdict(list)
        self._by_service: dict[str, list[ResolvedLine]] = defaultdict(list)
        for rl in self.lines:
            for svc in rl.possible_services:
                self._group[(rl.patient_id, svc)].append(rl)
                self._by_service[svc].append(rl)
        self.repeat: dict[tuple[str, str], str] = {}
        self.repeat_of: dict[tuple[str, str], str] = {}
        self._build_repeats()
        self._prior: dict[tuple[str, str], Interval] = {}
        self._build_cumulative()

    # -- the day relation -----------------------------------------------------------

    def same_day(self, a: ResolvedLine, b: ResolvedLine) -> str:
        if a.service_date is not None and b.service_date is not None:
            return YES if a.service_date == b.service_date else NO
        da, db = self.dates[a.line_id], self.dates[b.line_id]
        if da is None or db is None:
            return MAYBE
        return MAYBE if da & db else NO

    def within_days(self, a: ResolvedLine, b: ResolvedLine, days: int, *, both_directions: bool,
                    inclusive: bool) -> str:
        """Is ``a``'s Service Day within ``days`` of ``b``'s (``a`` minus ``b``)?"""
        def ok(da: _dt.date, db: _dt.date) -> bool:
            delta = (da - db).days
            if not both_directions and delta < 0:
                return False
            return abs(delta) <= days if inclusive else abs(delta) < days

        if a.service_date is not None and b.service_date is not None:
            return YES if ok(a.service_date, b.service_date) else NO
        da, db = self.dates[a.line_id], self.dates[b.line_id]
        if da is None or db is None:
            return MAYBE
        return MAYBE if any(ok(x, y) for x in da for y in db) else NO

    def weekday_options(self, rl: ResolvedLine, non_business: tuple[int, ...]) -> frozenset[bool]:
        """Whether the line's Service Day is a non-Business Day, over its possible days."""
        ds = self.dates[rl.line_id]
        if ds is None:
            return frozenset({False, True})
        return frozenset(d.weekday() in non_business for d in ds)

    # -- repeats (10.3) ---------------------------------------------------------------

    def _build_repeats(self) -> None:
        for (_, svc), lines in self._group.items():
            ordered = sorted(lines, key=billing_order)
            for i, rl in enumerate(ordered):
                state, first = NO, None
                for e in ordered[:i]:
                    rel = self.same_day(rl, e)
                    if rel == YES and e.certainly(svc):
                        state, first = YES, e
                        break
                    if rel != NO:
                        state = MAYBE
                self.repeat[(rl.line_id, svc)] = state
                if first is not None:
                    self.repeat_of[(rl.line_id, svc)] = first.line_id

    def repeat_state(self, rl: ResolvedLine, svc: str) -> str:
        return self.repeat.get((rl.line_id, svc), NO)

    def contribution(self, rl: ResolvedLine, svc: str) -> tuple[int, int]:
        """(certain, possible) units this line adds to ``svc``'s aggregates.

        A certain repeat adds nothing: a service billed twice was delivered
        once.  An unresolved line adds only to the possible bound."""
        state = self.repeat_state(rl, svc)
        low = rl.line.quantity if (rl.certainly(svc) and state == NO) else 0
        high = rl.line.quantity if state != YES else 0
        return low, high

    # -- Service Day aggregates (5.1, cap) -----------------------------------------------

    def day_quantity_if(self, rl: ResolvedLine, svc: str) -> Interval:
        """The patient's Service Day aggregate of ``svc`` in every reading in
        which *this line is the payable billing of svc* that day.

        Clause 5.1 aggregates "across all line items and all invoices", but
        clause 10.3 allows one billing of a Service per Patient per Service
        Day, and the first billing stands.  So in any reading where this line
        is payable, every other same-day line of ``svc`` is billed later and
        is a repeat -- which adds no delivered units -- and any line billed
        earlier would have made *this* line the repeat (a reading priced at 0
        through the repeat rule, not here).  The aggregate a premium or a cap
        is assessed against is therefore this line's own quantity.  Counting
        a possible same-day line on top would price readings that cannot
        occur together.
        """
        own = rl.line.quantity if self.repeat_state(rl, svc) != YES else 0
        return Interval(own, own)

    # -- presence (7.1) -----------------------------------------------------------------

    def presence(self, rl: ResolvedLine, svc: str) -> str:
        """Whether ``svc`` is delivered to the patient on this line's Service Day."""
        best = NO
        for e in self._group[(rl.patient_id, svc)]:
            if e is rl:
                continue
            rel = self.same_day(rl, e)
            if rel == YES and e.certainly(svc):
                return YES
            if rel != NO:
                best = MAYBE
        return best

    # -- exclusion triggers (9) ---------------------------------------------------------

    def triggers(self, rl: ResolvedLine, svc: str, days: int, *, both_directions: bool,
                 inclusive: bool) -> tuple[list, list]:
        """(certain trigger lines, possible trigger lines) of ``svc`` for this line."""
        certain, possible = [], []
        for e in self._group[(rl.patient_id, svc)]:
            if e is rl:
                continue
            rel = self.within_days(rl, e, days, both_directions=both_directions, inclusive=inclusive)
            if rel == YES and e.certainly(svc):
                certain.append(e)
            elif rel != NO:
                possible.append(e)
        return certain, possible

    # -- cumulative utilisation (8.1) -----------------------------------------------------

    def _before(self, a: ResolvedLine, b: ResolvedLine) -> str:
        """Is ``a`` counted before ``b`` in clause 8.1 order?"""
        if a.service_date is not None and b.service_date is not None:
            return YES if cumulative_order(a) < cumulative_order(b) else NO
        da, db = self.dates[a.line_id], self.dates[b.line_id]
        if da is None or db is None:
            return MAYBE
        if (max(da), a.line_id) < (min(db), b.line_id):
            return YES
        if (min(da), a.line_id) > (max(db), b.line_id):
            return NO
        return MAYBE

    def _build_cumulative(self) -> None:
        for svc, lines in self._by_service.items():
            dated = sorted((l for l in lines if l.service_date is not None), key=cumulative_order)
            floating = [l for l in lines if l.service_date is None]

            def with_floating(target: ResolvedLine, lo: int, hi: int) -> Interval:
                for f in floating:
                    if f is target:
                        continue
                    rel = self._before(f, target)
                    f_lo, f_hi = self.contribution(f, svc)
                    if rel == YES:
                        lo, hi = lo + f_lo, hi + f_hi
                    elif rel == MAYBE:
                        hi += f_hi
                return Interval(lo, hi)

            lo = hi = 0
            for rl in dated:
                # Snapshot before adding the line: utilisation *prior to* it.
                self._prior[(rl.line_id, svc)] = with_floating(rl, lo, hi)
                c_lo, c_hi = self.contribution(rl, svc)
                lo, hi = lo + c_lo, hi + c_hi
            for f in floating:
                f_lo = f_hi = 0
                for other in dated:
                    rel = self._before(other, f)
                    o_lo, o_hi = self.contribution(other, svc)
                    if rel == YES:
                        f_lo, f_hi = f_lo + o_lo, f_hi + o_hi
                    elif rel == MAYBE:
                        f_hi += o_hi
                self._prior[(f.line_id, svc)] = with_floating(f, f_lo, f_hi)

    def prior_cumulative(self, rl: ResolvedLine, svc: str) -> Interval:
        """Utilisation of ``svc`` before this line, over all patients."""
        return self._prior.get((rl.line_id, svc), Interval())


# ==========================================================================
# 4. Pricing
# ==========================================================================

class PricingEngine:
    """Clause 3.1 in order, rounding half up after every step (3.2, 3.4)."""

    def __init__(self, contract: H5Contract):
        self.contract = contract

    def stages(self, service: str, facility: str, tier: str, *, bundle: bool, premium: Decimal,
               discount: Decimal) -> dict:
        c = self.contract
        svc = c.services[service]
        rate = svc.base_rate_cents
        if bundle:
            partner = c.bundle_partner(service)
            if partner is None:
                raise ValueError(f"{service!r} has no bundle")
            rate = partner[1]
        after_bundle = rate
        f_mult = c.facility_multipliers[service][facility]
        rate = multiply_cents(rate, f_mult)          # rounded even when the multiplier is 1 (3.4)
        after_facility = rate
        t_mult = c.tier_multipliers[service][tier]
        rate = multiply_cents(rate, t_mult)
        after_tier = rate
        rate = apply_percentage_change(rate, premium)
        after_premium = rate
        rate = apply_percentage_change(rate, -discount)
        return {"base": svc.base_rate_cents, "bundle": after_bundle, "facility_multiplier": str(f_mult),
                "facility": after_facility, "tier_multiplier": str(t_mult), "tier": after_tier,
                "premium_fraction": str(premium), "premium": after_premium, "discount_fraction": str(discount),
                "discount": rate}

    def unit_rate(self, service: str, facility: str, tier: str, *, bundle: bool, premium: Decimal,
                  discount: Decimal) -> int:
        return self.stages(service, facility, tier, bundle=bundle, premium=premium, discount=discount)["discount"]


def tier_fraction(tiers, total: int) -> Decimal:
    """8.1: where two thresholds are met, the deeper discount applies."""
    return max((t.discount_fraction for t in tiers if total > t.exceeds_units), default=Decimal(0))


def discount_options(tiers, prior: Interval) -> tuple[Decimal, ...]:
    """Every tier a line can be priced at when prior utilisation lies in ``prior``
    (``line_prior`` mode).  Sorted; the first is the shallowest."""
    if not tiers:
        return (Decimal(0),)
    opts = {tier_fraction(tiers, prior.low)}
    opts |= {t.discount_fraction for t in tiers if prior.low <= t.exceeds_units < prior.high}
    opts |= {tier_fraction(tiers, prior.high)}
    return tuple(sorted(opts))


def split_line_total(unit_rate_at, tiers, prior: int, quantity: int) -> int:
    """``unit_split`` mode: unit k of the line is the (prior + k)-th unit of
    utilisation and takes the tier that position exceeds."""
    return sum(unit_rate_at(tier_fraction(tiers, prior + k)) for k in range(1, quantity + 1))


# ==========================================================================
# 5. Candidate evaluation
# ==========================================================================

@dataclass
class CandidateEval:
    """One line read as one candidate service."""

    service: str
    #: Possible corrected line totals.  ``None`` = some reading has no
    #: contract price (a cap breach whose true quantity is unknowable).
    values: set = field(default_factory=set)
    #: Unit rates the provider could correctly have billed.
    plausible_rates: set = field(default_factory=set)
    #: The rate under the reading supported by certain evidence alone.
    reference_rate: int = 0
    reference_stages: dict = field(default_factory=dict)
    #: Findings that hold under every reading of *this* candidate.
    findings: list[Finding] = field(default_factory=list)
    #: Why ``values`` is not a single number.
    uncertainty: list[str] = field(default_factory=list)
    #: Tags for the blank-reason report.
    uncertainty_tags: set = field(default_factory=set)
    #: Cap-priced amount, where a cap breach blocks reconstruction.
    ceiling: Optional[int] = None
    trace: dict = field(default_factory=dict)


class H5Auditor:
    def __init__(self, contract: H5Contract, policy: Optional[AuditPolicy] = None):
        self.contract = contract
        self.policy = policy or AuditPolicy()
        self.engine = PricingEngine(contract)
        self.term = (_dt.date.fromisoformat(contract.effective_from), _dt.date.fromisoformat(contract.effective_to))

    # -- stage inputs --------------------------------------------------------------

    def _bundle_options(self, rl: ResolvedLine, svc: str, ctx: AuditContext, ev: CandidateEval) -> tuple[bool, ...]:
        partner = self.contract.bundle_partner(svc)
        if partner is None:
            return (False,)
        state = ctx.presence(rl, partner[0])
        ev.trace["bundle"] = {"partner": partner[0], "partner_present": state}
        if state == YES:
            return (True,)
        if state == MAYBE:
            ev.uncertainty.append(f"bundle partner {partner[0]!r} may have been delivered the same day "
                                  "(an unresolved description or a malformed date)")
            ev.uncertainty_tags.add("bundle_partner_uncertain")
            return (False, True)
        return (False,)

    def _premium_options(self, rl: ResolvedLine, svc: str, ctx: AuditContext, ev: CandidateEval) -> tuple[Decimal, ...]:
        c = self.contract
        prem = c.threshold_premiums.get(svc)
        if prem is not None:
            agg = ctx.day_quantity_if(rl, svc)
            ev.trace["premium"] = {"kind": "threshold", "more_than": prem.exceeds_units,
                                   "uplift": str(prem.uplift_fraction), "day_quantity": agg.as_list()}
            if agg.low > prem.exceeds_units:
                return (prem.uplift_fraction,)
            if agg.high > prem.exceeds_units:
                ev.uncertainty.append(f"Service Day aggregate of {svc!r} lies in {agg.as_list()}, straddling "
                                      f"the premium threshold {prem.exceeds_units}")
                ev.uncertainty_tags.add("premium_threshold_uncertain")
                return (Decimal(0), prem.uplift_fraction)
            return (Decimal(0),)
        nbd = c.non_business_day_uplifts.get(svc)
        if nbd is not None:
            ev.trace["premium"] = {"kind": "non_business_day", "uplift": str(nbd.uplift_fraction)}
            weekend = ctx.weekday_options(rl, c.non_business_weekdays)
            if len(weekend) > 1:
                ev.uncertainty.append(f"malformed Service Date {rl.line.service_date_raw!r}: its possible days include "
                                      f"both Business Days and weekend days, so the Section 6 uplift for {svc!r} "
                                      "is undetermined")
                ev.uncertainty_tags.add("malformed_date_affects_uplift")
                return (Decimal(0), nbd.uplift_fraction)
            (is_weekend,) = weekend
            ev.trace["premium"]["non_business_day"] = is_weekend
            return (nbd.uplift_fraction,) if is_weekend else (Decimal(0),)
        return (Decimal(0),)

    def _discount_options(self, rl: ResolvedLine, svc: str, ctx: AuditContext, ev: CandidateEval) -> tuple:
        tiers = self.contract.volume_discounts.get(svc) or []
        if not tiers:
            return (Decimal(0),), None
        prior = ctx.prior_cumulative(rl, svc)
        ev.trace["discount"] = {"tiers": [[t.exceeds_units, str(t.discount_fraction)] for t in tiers],
                                "prior_cumulative": prior.as_list(), "mode": self.policy.discount_mode}
        opts = discount_options(tiers, prior)
        if len(opts) > 1 or (self.policy.discount_mode == "unit_split" and prior.low != prior.high
                             and any(prior.low <= t.exceeds_units < prior.high + rl.line.quantity for t in tiers)):
            ev.uncertainty.append(f"cumulative utilisation of {svc!r} before this line lies in {prior.as_list()}, "
                                  "straddling a discount threshold")
            ev.uncertainty_tags.add("discount_position_uncertain")
        return opts, prior

    def _payable_quantities(self, rl: ResolvedLine, svc: str, ctx: AuditContext, ev: CandidateEval) -> tuple[int, ...]:
        """Quantities that may be payable: 0 if a repeat (10.3) or excluded (9)."""
        qty = rl.line.quantity
        zero_certain, zero_possible = False, False
        state = ctx.repeat_state(rl, svc)
        ev.trace["repeat"] = state
        if state == YES:
            first = ctx.repeat_of[(rl.line_id, svc)]
            first_rl = ctx.by_id[first]
            same = first_rl.occurrence.occurrence_id == rl.occurrence.occurrence_id
            ev.findings.append(Finding(
                "duplicate_service" if same else "cross_invoice_duplicate",
                f"{svc!r} already billed for patient {rl.patient_id} on {rl.service_date} (line {first} of invoice "
                f"{first_rl.occurrence.invoice_id}); clause 10.3 forbids a repeat", rl.line_id))
            zero_certain = True
        elif state == MAYBE:
            ev.uncertainty.append(f"an earlier line that may also be {svc!r} for the patient that day (unresolved "
                                  "description or malformed date) would make this line a repeat under clause 10.3")
            ev.uncertainty_tags.add("possible_repeat")
            zero_possible = True
        excl_certain, excl_possible = self._exclusion(rl, svc, ctx, ev)
        zero_certain = zero_certain or excl_certain
        zero_possible = zero_possible or excl_possible
        if zero_certain:
            return (0,)
        return (qty, 0) if zero_possible else (qty,)

    def _exclusion(self, rl: ResolvedLine, svc: str, ctx: AuditContext, ev: CandidateEval) -> tuple[bool, bool]:
        """Section 9: ``svc`` is not billable within N days of the other service.

        Directed: only the left-hand service loses payment.  Both directions of
        time, inclusive of day N (policy).  A certain trigger proves a
        violation; a possible one is recorded as uncertainty."""
        certain_hit = possible_hit = False
        relations = []
        for w in self.contract.exclusions_for(svc):
            certain, possible = ctx.triggers(rl, w.other_service, w.days,
                                             both_directions=self.policy.exclusion_both_directions,
                                             inclusive=self.policy.exclusion_window_inclusive)
            if not certain and not possible:
                continue
            relations.append({"excluded_by": w.other_service, "window_days": w.days,
                              "certain": [e.line_id for e in certain], "possible": [e.line_id for e in possible]})
            if certain and not certain_hit:
                certain_hit = True
                t = min(certain, key=lambda e: (abs((rl.service_date - e.service_date).days), e.line_id))
                ev.findings.append(Finding(
                    "exclusion_window_violation",
                    f"{svc!r} on {rl.service_date} is within {w.days} days of {w.other_service!r} on {t.service_date} "
                    f"(line {t.line_id}, {abs((rl.service_date - t.service_date).days)} day(s) apart; Section 9)",
                    rl.line_id))
            elif possible and not certain:
                possible_hit = True
                ev.uncertainty.append(f"{w.other_service!r} may have been delivered within {w.days} days (lines "
                                      f"{', '.join(e.line_id for e in possible)}); a violation is not asserted")
                ev.uncertainty_tags.add("possible_exclusion")
        if relations:
            ev.trace["exclusions"] = relations
        return certain_hit, possible_hit and not certain_hit

    # -- one candidate ----------------------------------------------------------------

    def evaluate(self, rl: ResolvedLine, svc: str, ctx: AuditContext) -> CandidateEval:
        occ, line = rl.occurrence, rl.line
        ev = CandidateEval(svc)
        bundles = self._bundle_options(rl, svc, ctx, ev)
        premiums = self._premium_options(rl, svc, ctx, ev)
        discounts, prior = self._discount_options(rl, svc, ctx, ev)
        quantities = self._payable_quantities(rl, svc, ctx, ev)
        tiers = self.contract.volume_discounts.get(svc) or []

        def rate(b, p, d) -> int:
            return self.engine.unit_rate(svc, occ.facility_code, occ.plan_tier, bundle=b, premium=p, discount=d)

        ev.reference_stages = self.engine.stages(svc, occ.facility_code, occ.plan_tier, bundle=bundles[0],
                                                 premium=premiums[0], discount=discounts[0])
        ev.reference_rate = ev.reference_stages["discount"]
        for b in bundles:
            for p in premiums:
                for d in discounts:
                    ev.plausible_rates.add(rate(b, p, d))

        # Line totals over every reading.
        cap = self.contract.daily_caps.get(svc)
        cap_state = NO
        if cap is not None and any(q > 0 for q in quantities):
            agg = ctx.day_quantity_if(rl, svc)
            ev.trace["cap"] = {"max_units": cap.max_units, "day_quantity": agg.as_list()}
            if agg.low > cap.max_units:
                cap_state = YES
                ev.findings.append(Finding(
                    "daily_cap_exceeded",
                    f"{agg.low} units of {svc!r} for patient {rl.patient_id} on {rl.service_date or 'an unknown day'}; "
                    f"Table 1 caps this at {cap.max_units} per day", rl.line_id,
                    blocks_reconstruction=self.policy.cap_breach_blocks_reconstruction))
            elif agg.high > cap.max_units:
                cap_state = MAYBE
                ev.uncertainty.append(f"Service Day quantity lies in {agg.as_list()}, straddling the cap "
                                      f"{cap.max_units}; a breach is not asserted")
                ev.uncertainty_tags.add("possible_cap_breach")

        for q in quantities:
            if q == 0:
                ev.values.add(0)
                continue
            payable_q = q
            if cap_state != NO:
                capped = min(cap.max_units, q)
                if self.policy.cap_breach_blocks_reconstruction:
                    ev.values.add(None)
                    ev.uncertainty_tags.add("cap_breach" if cap_state == YES else "possible_cap_breach")
                    ev.ceiling = max(rate(b, p, d) for b in bundles for p in premiums for d in discounts) * capped
                    if cap_state == YES:
                        ev.uncertainty.append(f"the cap proves the quantity wrong but not what it should be; only "
                                              f"the capped amount is a ceiling (policy)")
                        continue
                else:
                    payable_q = capped
            for b in bundles:
                for p in premiums:
                    if self.policy.discount_mode == "unit_split" and tiers:
                        for pr in range(prior.low, prior.high + 1):
                            ev.values.add(split_line_total(lambda f: rate(b, p, f), tiers, pr, payable_q))
                    else:
                        for d in discounts:
                            ev.values.add(rate(b, p, d) * payable_q)
        ev.trace.update({"service": svc, "stages_cents": {k: v for k, v in ev.reference_stages.items()
                                                          if not k.endswith(("_multiplier", "_fraction"))},
                         "multipliers": [ev.reference_stages["facility_multiplier"],
                                         ev.reference_stages["tier_multiplier"]],
                         "payable_quantities": list(quantities),
                         "plausible_unit_rates_cents": sorted(ev.plausible_rates),
                         "possible_line_totals_cents": sorted(ev.values, key=lambda v: (v is None, v or 0))})
        return ev

    def variants(self, rl: ResolvedLine, svc: str, ctx: AuditContext) -> dict[str, set[int]]:
        """Rates under single-stage deviations from the certain reading -- used
        only to *name* a price mismatch, never to decide one."""
        occ = rl.occurrence
        scratch = CandidateEval(svc)
        b0 = self._bundle_options(rl, svc, ctx, scratch)
        p0 = self._premium_options(rl, svc, ctx, scratch)
        d0, _ = self._discount_options(rl, svc, ctx, scratch)
        c = self.contract

        def rate(b, p, d) -> int:
            return self.engine.unit_rate(svc, occ.facility_code, occ.plan_tier, bundle=b, premium=p, discount=d)

        out: dict[str, set[int]] = defaultdict(set)
        if c.bundle_partner(svc):
            for b in b0:
                out["bundle_not_applied" if b else "bundle_incorrectly_applied"].add(rate(not b, p0[0], d0[0]))
        available = (c.threshold_premiums.get(svc) or c.non_business_day_uplifts.get(svc))
        if available is not None:
            for p in p0:
                if p:
                    out["premium_omitted"].add(rate(b0[0], Decimal(0), d0[0]))
                else:
                    out["premium_incorrectly_applied"].add(rate(b0[0], available.uplift_fraction, d0[0]))
        tiers = c.volume_discounts.get(svc) or []
        for d in d0:
            if d:
                out["volume_discount_omitted"].add(rate(b0[0], p0[0], Decimal(0)))
            for t in tiers:
                if t.discount_fraction not in d0:
                    out["volume_discount_incorrectly_applied"].add(rate(b0[0], p0[0], t.discount_fraction))
        return out


# ==========================================================================
# 6. Line audit
# ==========================================================================

@dataclass
class LineOutcome:
    findings: list[Finding] = field(default_factory=list)
    #: Possible corrected line totals over every still-possible reading.
    values: set = field(default_factory=set)
    #: The most the payer could owe, where every reading is bounded.
    ceiling: Optional[int] = None
    #: A diagnostic figure never submitted (e.g. an unknown line at billed).
    provisional: Optional[int] = None
    reasons: list[str] = field(default_factory=list)
    tags: set = field(default_factory=set)
    trace: dict = field(default_factory=dict)
    #: identity_resolved | semantic_ambiguous_but_financially_resolved |
    #: financially_ambiguous | unknown_service
    financial_status: str = ""
    #: Every possible service has one settled unit rate.
    pricing_complete: bool = False
    #: Ambiguous lines only: do all candidate services, each evaluated in the
    #: real context, produce the same single corrected total?  (Diagnostic:
    #: it matters only once the candidate set is closed to contracted services.)
    candidates_agree: Optional[bool] = None

    @property
    def exact(self) -> Optional[int]:
        return next(iter(self.values)) if len(self.values) == 1 and None not in self.values else None


class LineAuditor(H5Auditor):
    # -- identity-independent checks -------------------------------------------------

    def line_checks(self, rl: ResolvedLine) -> list[Finding]:
        f: list[Finding] = []
        line, occ = rl.line, rl.occurrence
        if line.service_date is None:
            # Not blocking: the date affects the amount only through the rules
            # that read it, and those widen their own outcomes.
            f.append(Finding("malformed_service_date", f"service date {line.service_date_raw!r} is not a valid date",
                             line.line_id))
        elif not (self.term[0] <= line.service_date <= self.term[1]):
            f.append(Finding("service_date_out_of_window",
                             f"service date {line.service_date} is outside the term {self.term[0]} to {self.term[1]} "
                             "(clause 10.2)", line.line_id))
        elif occ.invoice_date is not None and line.service_date > occ.invoice_date:
            # A date past the end of the term is usually also past the invoice
            # date: one defect, reported once, as the more specific term breach.
            f.append(Finding("service_date_after_invoice_date",
                             f"service date {line.service_date} is after the invoice date {occ.invoice_date} "
                             "(clause 10.2)", line.line_id))
        if line.unit_price_cents * line.quantity != line.line_total_cents:
            f.append(Finding("line_total_arithmetic",
                             f"line total {line.line_total_cents} != {line.unit_price_cents} x {line.quantity} "
                             "(clause 3.1)", line.line_id))
        return f

    # -- one line -------------------------------------------------------------------

    def audit_line(self, rl: ResolvedLine, ctx: AuditContext) -> LineOutcome:
        line = rl.line
        out = LineOutcome(findings=self.line_checks(rl))
        ident = rl.identity
        out.trace = {
            "line_id": line.line_id,
            "raw_description": line.description,
            "identity_key": rl.match.identity_key,
            "identity": {"status": ident.status, "service": ident.service, "method": ident.method,
                         "candidates": list(ident.candidates),
                         "used_unit_basis_for_identity": ident.used_unit_basis_for_identity,
                         "open_world": ident.open_world, "unread_tokens": list(rl.match.unread_tokens),
                         "contextual": rl.match.contextual},
            "billed": {"service_date": line.service_date_raw, "quantity": line.quantity,
                       "unit_basis": rl.billed_basis, "unit_price_cents": line.unit_price_cents,
                       "line_total_cents": line.line_total_cents},
        }
        if ident.status == UNKNOWN:
            self._audit_unknown(rl, out)
        elif ident.status == MATCHED:
            self._audit_matched(rl, ctx, out)
        else:
            self._audit_ambiguous(rl, ctx, out)
        if not self.policy.financial_equivalence:
            # Hospital 4's rule, for the contribution report only.
            settled = ident.status == MATCHED and out.exact is not None and line.service_date is not None
            if not settled:
                out.values = {None}
                out.tags.add("no_financial_equivalence")
        return self._finish(out)

    def _audit_unknown(self, rl: ResolvedLine, out: LineOutcome) -> None:
        line = rl.line
        out.findings.append(Finding(
            "unknown_service", f"description {line.description!r} does not identify any contracted service "
                               f"({rl.identity.method})", line.line_id, blocks_reconstruction=True))
        carried = line.unit_price_cents * line.quantity
        out.provisional = carried
        out.values = {carried} if self.policy.unknown_service_line_carried_at_billed else {None}
        out.financial_status = "unknown_service"
        out.tags.add("unknown_service")
        out.reasons.append(f"{line.line_id}: service not contracted, so no contract price exists")
        out.trace["pricing"] = None

    def _audit_matched(self, rl: ResolvedLine, ctx: AuditContext, out: LineOutcome) -> None:
        line, svc = rl.line, rl.service
        spec = self.contract.services[svc]
        if rl.billed_basis != spec.unit_basis and not rl.identity.used_unit_basis_for_identity:
            out.findings.append(Finding(
                "wrong_unit_basis", f"billed {rl.billed_basis!r}; Table 1 states {spec.unit_basis_text!r} "
                                    f"({spec.unit_basis}) for {svc!r}", line.line_id))
        ev = self.evaluate(rl, svc, ctx)
        out.findings.extend(ev.findings)
        # A certain repeat pays nothing, and the rate it "should" carry is
        # undefined: the day it repeats was delivered once, by another line.
        # Its price is therefore not audited; the repeat finding says it all.
        if line.unit_price_cents not in ev.plausible_rates and ev.trace.get("repeat") != YES:
            out.findings.append(self._name_mismatch(rl, svc, ctx, ev))
        out.values = set(ev.values)
        out.ceiling = ev.ceiling
        out.tags |= ev.uncertainty_tags
        out.reasons.extend(f"{line.line_id}: {r}" for r in ev.uncertainty)
        out.pricing_complete = len(ev.plausible_rates) == 1
        out.financial_status = "identity_resolved"
        out.trace["pricing"] = ev.trace

    def _audit_ambiguous(self, rl: ResolvedLine, ctx: AuditContext, out: LineOutcome) -> None:
        """No service is chosen.  Each candidate is evaluated in the real
        context; the line's possible totals are the union."""
        line, ident = rl.line, rl.identity
        cands = ident.candidates
        out.reasons.append(f"{line.line_id}: {line.description!r} is unresolved ({ident.method}) between "
                           f"{', '.join(cands)}" + (" or an uncontracted service" if ident.open_world else ""))
        bases = {self.contract.services[s].unit_basis for s in cands}
        if rl.billed_basis not in bases:
            out.findings.append(Finding(
                "wrong_unit_basis", f"billed {rl.billed_basis!r}; no plausible reading ({', '.join(cands)}) is "
                                    "billed on that basis", line.line_id))
        evals = {s: self.evaluate(rl, s, ctx) for s in cands}
        for ev in evals.values():
            out.values |= ev.values
        sets = {frozenset(ev.values) for ev in evals.values()}
        if len(cands) > 1:
            only = next(iter(sets))
            out.candidates_agree = len(sets) == 1 and len(only) == 1 and None not in only
        # Only reasons every candidate reading shares explain the line; a
        # reason that holds for one candidate alone (a cap breach "if it were
        # that service") is a consequence of the identity question, not a
        # cause of its own.
        out.tags |= set.intersection(*(ev.uncertainty_tags for ev in evals.values()))
        if ident.open_world:
            out.values.add(None)
            out.tags.add("open_world_identity")
        elif len({frozenset(ev.values) for ev in evals.values()}) > 1 or (
                (len(out.values) > 1 or None in out.values) and not out.tags):
            out.tags.add("candidates_price_differently")
        # A finding every candidate reading agrees on can be asserted without
        # knowing which candidate it is -- but only in a closed world.
        if not ident.open_world:
            shared = set.intersection(*({f.category for f in ev.findings} for ev in evals.values()))
            for cat in sorted(shared):
                first = next(f for f in evals[cands[0]].findings if f.category == cat)
                out.findings.append(Finding(cat, first.detail + f" (under every candidate reading: {', '.join(cands)})",
                                            line.line_id, first.blocks_reconstruction))
        rates = set().union(*(ev.plausible_rates for ev in evals.values()))
        if line.unit_price_cents not in rates:
            out.findings.append(Finding(
                "unit_price_mismatch", f"billed {line.unit_price_cents} matches no contract rate under any reading of "
                                       f"{line.description!r} ({', '.join(cands)})", line.line_id))
        ceilings = [ev.ceiling for ev in evals.values() if ev.ceiling is not None]
        out.ceiling = max(ceilings) if ceilings and not ident.open_world else None
        out.pricing_complete = not ident.open_world and len(rates) == 1
        out.financial_status = ("semantic_ambiguous_but_financially_resolved" if out.exact is not None
                                else "financially_ambiguous")
        out.trace["pricing"] = {"candidates": {s: ev.trace for s, ev in evals.items()}}
        for s, ev in evals.items():
            out.reasons.extend(f"{line.line_id} as {s}: {r}" for r in ev.uncertainty)

    def _finish(self, out: LineOutcome) -> LineOutcome:
        if out.financial_status in ("identity_resolved",) and out.exact is None:
            out.financial_status = "financially_ambiguous"
        out.trace["findings"] = [f.category for f in out.findings]
        out.trace["possible_line_totals_cents"] = sorted(out.values, key=lambda v: (v is None, v or 0))
        out.trace["expected_line_total_cents"] = out.exact
        out.trace["financial_status"] = out.financial_status
        out.trace["uncertainty"] = list(out.reasons)
        out.trace["uncertainty_tags"] = sorted(out.tags)
        return out

    def _name_mismatch(self, rl: ResolvedLine, svc: str, ctx: AuditContext, ev: CandidateEval) -> Finding:
        billed = rl.line.unit_price_cents
        for category, rates in sorted(self.variants(rl, svc, ctx).items()):
            if billed in rates:
                return Finding(category, f"{svc!r} billed at {billed}; contract rate {ev.reference_rate}; the billed "
                                         "figure is exactly the rate with this one adjustment wrong", rl.line_id)
        return Finding("unit_price_mismatch", f"{svc!r} billed at {billed}; contract rate {ev.reference_rate} "
                                              f"(stages {ev.reference_stages})", rl.line_id)


# ==========================================================================
# 7. Invoice audit
# ==========================================================================

@dataclass
class OccurrenceAudit:
    """One physical invoice record.  Never merged with another occurrence."""

    occurrence: InvoiceOccurrence
    findings: list[Finding]
    lines: list[ResolvedLine]
    outcomes: list[LineOutcome]

    @property
    def expected_total_cents(self) -> Optional[int]:
        if not self.occurrence_priceable:
            return None
        values = [o.exact for o in self.outcomes]
        return None if any(v is None for v in values) else sum(values)

    @property
    def occurrence_priceable(self) -> bool:
        return not any(f.category in ("unknown_facility", "unknown_plan_tier") for f in self.findings)

    @property
    def pricing_complete(self) -> bool:
        return self.occurrence_priceable and all(o.pricing_complete for o in self.outcomes)

    @property
    def ceiling_total_cents(self) -> Optional[int]:
        total = 0
        for o in self.outcomes:
            if o.exact is not None:
                total += o.exact
            elif o.ceiling is not None and None in o.values and len(o.values - {None}) <= 1:
                total += max([o.ceiling] + [v for v in o.values if v is not None])
            else:
                return None
        return total

    @property
    def provisional_total_cents(self) -> Optional[int]:
        total = 0
        for o in self.outcomes:
            v = o.exact if o.exact is not None else o.provisional
            if v is None:
                return None
            total += v
        return total

    @property
    def traces(self) -> list[dict]:
        return [o.trace for o in self.outcomes]


def audit_occurrence(occ: InvoiceOccurrence, lines: list[ResolvedLine], ctx: AuditContext, auditor: LineAuditor,
                     *, first: Optional[InvoiceOccurrence]) -> OccurrenceAudit:
    c = auditor.contract
    findings: list[Finding] = []
    if occ.contract_number != c.contract_number:
        findings.append(Finding("contract_number_mismatch", f"invoice quotes {occ.contract_number!r}; this agreement "
                                f"is {c.contract_number!r} (clause 10.1)"))
    if occ.facility_code not in c.facilities:
        findings.append(Finding("unknown_facility", f"facility {occ.facility_code!r} is not a Provider facility "
                                "(clause 1.2), so Table 2 has no column for it", blocks_reconstruction=True))
    if occ.plan_tier not in c.plan_tiers:
        findings.append(Finding("unknown_plan_tier", f"plan tier {occ.plan_tier!r} is not a plan tier of this "
                                "agreement (clause 1.3)", blocks_reconstruction=True))
    if first is not None:
        findings.append(Finding("duplicate_invoice_id", f"invoice number {occ.invoice_id!r} was already used by the "
                                f"record dated {first.invoice_date_raw} for patient {first.patient_id} (clause 10.1)"))
    summed = sum(l.line_total_cents for l in occ.line_items)
    if summed != occ.invoice_total_cents:
        findings.append(Finding("invoice_total_mismatch", f"invoice total {occ.invoice_total_cents} != sum of line "
                                f"totals {summed}"))
    outcomes = []
    for rl in lines:
        if occ.facility_code not in c.facilities or occ.plan_tier not in c.plan_tiers:
            o = LineOutcome(findings=auditor.line_checks(rl), values={None})
            o.tags.add("no_rate_column")
            o.trace = {"line_id": rl.line_id, "raw_description": rl.line.description, "findings": []}
        else:
            o = auditor.audit_line(rl, ctx)
        findings.extend(o.findings)
        outcomes.append(o)
    return OccurrenceAudit(occ, findings, lines, outcomes)


@dataclass
class H5InvoiceResult:
    """The claim about one invoice number, plus what the submission cannot hold.

    ``result`` is the submitted row and describes the *represented* (latest)
    occurrence only; ``occurrences`` keeps every physical record."""

    result: AuditResult
    occurrences: list[OccurrenceAudit]
    provisional_expected_total_cents: Optional[int] = None
    blank_reasons: list[str] = field(default_factory=list)

    @property
    def represented(self) -> OccurrenceAudit:
        return self.occurrences[-1]

    def line_status_counts(self) -> Counter:
        return Counter(o.financial_status for o in self.represented.outcomes)


#: Line uncertainty tag -> the blank reason reported for it, most specific first.
BLANK_REASONS = (
    ("unknown_service", "a line names a service this agreement does not contract (no contract price)"),
    ("cap_breach", "a daily cap is breached: the billed quantity is wrong but the right one is unknowable"),
    ("open_world_identity", "a line's description fits a contracted service only by omitting a word, and the "
                            "review did not rule out an uncontracted service"),
    ("candidates_price_differently", "a line's candidate services lead to different corrected amounts"),
    ("possible_cap_breach", "a Service Day quantity may breach a daily cap (depends on an unresolved line)"),
    ("possible_repeat", "a line may repeat an earlier billing (depends on an unresolved line or a malformed date)"),
    ("possible_exclusion", "an exclusion trigger may exist (depends on an unresolved line or a malformed date)"),
    ("bundle_partner_uncertain", "a bundle partner may or may not have been delivered the same day"),
    ("premium_threshold_uncertain", "a Service Day aggregate straddles a premium threshold"),
    ("discount_position_uncertain", "cumulative utilisation before a line straddles a discount tier"),
    ("malformed_date_affects_uplift", "a malformed date decides whether a weekend uplift applies"),
    ("no_rate_column", "the invoice's facility or plan tier is not in the agreement"),
    ("no_financial_equivalence", "(ablation) identity or an input not settled"),
)


def blank_reasons_for(audit: OccurrenceAudit) -> list[str]:
    tags = set().union(*(o.tags for o in audit.outcomes if o.exact is None)) if audit.outcomes else set()
    reasons = [text for tag, text in BLANK_REASONS if tag in tags]
    return reasons or (["unexplained"] if audit.expected_total_cents is None else [])


def combine(invoice_id: str, audits: list[OccurrenceAudit]) -> H5InvoiceResult:
    subject = audits[-1]
    findings = list(subject.findings)
    expected = subject.expected_total_cents
    reasons = list(dict.fromkeys(r for o in subject.outcomes for r in o.reasons))
    if len(audits) > 1:
        reasons.append("invoice number reused: this row represents occurrence "
                       f"{subject.occurrence.occurrence_id}; earlier occurrence(s) "
                       + ", ".join(f"{a.occurrence.occurrence_id} ({len(a.findings)} finding(s))" for a in audits[:-1])
                       + " are audited separately in findings.csv")
    blank = blank_reasons_for(subject) if expected is None else []
    result = AuditResult(
        invoice_id=invoice_id,
        occurrence_ids=[a.occurrence.occurrence_id for a in audits],
        billed_total_cents=subject.occurrence.invoice_total_cents,
        flagged=bool(findings),
        findings=findings,
        expected_total_cents=expected,
        maximum_contractually_payable_total_cents=expected if expected is not None else subject.ceiling_total_cents,
        pricing_complete=subject.pricing_complete,
        correction_reconstructable=expected is not None,
        uncertainty_reasons=reasons + [f"blank: {b}" for b in blank],
    )
    band, value, why = assess_confidence(result, subject)
    result.confidence_band, result.confidence = band, value
    result.uncertainty_reasons = list(dict.fromkeys(result.uncertainty_reasons + why))
    provisional = subject.provisional_total_cents if expected is None else None
    return H5InvoiceResult(result, audits, provisional, blank)


# ==========================================================================
# 8. Confidence
# ==========================================================================
#
# Evidence strength for review priority, not a probability.  Hospital 5 has no
# labels, so nothing here is calibrated and nothing claims an accuracy.  Jev's
# own probabilities are semantic-review evidence about one description; they
# are not this number.

BAND_VALUES = {"high": 0.85, "medium": 0.65, "low": 0.40}

#: Findings whose correctness depends on hospital-wide state or on a recorded
#: implementation policy rather than on the line alone.
_STATEFUL = frozenset({
    "bundle_not_applied", "bundle_incorrectly_applied", "premium_omitted", "premium_incorrectly_applied",
    "volume_discount_omitted", "volume_discount_incorrectly_applied", "daily_cap_exceeded",
    "exclusion_window_violation", "duplicate_service", "cross_invoice_duplicate",
})

_REVIEWED_IDENTITY = frozenset({"unit_basis_tiebreak", "unit_basis_tiebreak_after_review", "jev_missing_word",
                                 "jev_ambiguous_contracted"})


def assess_confidence(result: AuditResult, subject: OccurrenceAudit) -> tuple[str, float, list[str]]:
    cats = set(result.error_categories)
    ambiguous = any(rl.status == AMBIGUOUS for rl in subject.lines)
    reviewed = any(rl.identity.method in _REVIEWED_IDENTITY for rl in subject.lines)
    why: list[str] = []
    if result.flagged:
        if cats <= STRUCTURAL:
            why.append("every finding follows from the invoice's own fields, independent of service identity")
            return "high", BAND_VALUES["high"], why
        if "unknown_service" in cats or not result.correction_reconstructable:
            why.append("a finding rests on reading free text, or the corrected total cannot be proven")
            return "low", BAND_VALUES["low"], why
        if cats & _STATEFUL or reviewed or ambiguous:
            why.append("findings depend on hospital-wide state, a recorded policy, a reviewed or tie-broken "
                       "identity, or a candidate set priced by financial equivalence")
            return "medium", BAND_VALUES["medium"], why
        why.append("findings apply contract rules to services identified from text, priced exactly")
        return "high", BAND_VALUES["high"], why
    if not result.correction_reconstructable:
        why.append("not flagged, but some line's corrected amount could not be proven, so not every rule could "
                   "be checked")
        return "low", BAND_VALUES["low"], why
    if ambiguous or reviewed:
        why.append("not flagged; every amount is proven, but some identity rests on review, a tie-break, or "
                   "financial equivalence across candidates")
        return "medium", BAND_VALUES["medium"], why
    why.append("not flagged; every line identified from text and priced exactly")
    return "high", BAND_VALUES["high"], why


# ==========================================================================
# 9. Pipeline
# ==========================================================================

def audit_resolved(occurrences: list[InvoiceOccurrence], resolved: list[ResolvedLine],
                   auditor: LineAuditor) -> list[H5InvoiceResult]:
    """Audit every occurrence against one hospital-wide context.  Occurrences
    sharing an invoice number are audited separately, in file order; the first
    establishes the number and each later one is a repeat (clause 10.1)."""
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
        audits = [audit_occurrence(o, by_occ[o.occurrence_id], ctx, auditor, first=occs[0] if i else None)
                  for i, o in enumerate(occs)]
        results.append(combine(invoice_id, audits))
    return results


class H5Pipeline:
    """Files + reviewed semantics -> results.  Offline and deterministic.

    By default the lexicon and the missing-word decisions come from the
    committed Jev reviews, checked for staleness (``workflow.load_semantics``).
    Tests and the contribution report pass their own.
    """

    def __init__(self, *, lexicon: Optional[Lexicon] = None,
                 decisions: Optional[dict[tuple[str, str], MissingWordDecision]] = None,
                 policy: Optional[AuditPolicy] = None, semantics=None, check_csv: bool = True):
        self.contract = parse_contract()
        self.occurrences = load_occurrences(JSONL)
        if check_csv:
            problems = cross_check_against_csv(self.occurrences, INVOICES_CSV, LINES_CSV)
            if problems:
                raise ValueError("hospital_5 JSONL and CSV disagree:\n  " + "\n  ".join(problems[:20]))
        if lexicon is None:
            from .workflow import load_semantics
            semantics = semantics or load_semantics(self.contract, self.occurrences, strict=True)
            lexicon = semantics.lexicon
            decisions = semantics.missing_word_decisions if decisions is None else decisions
        self.semantics = semantics
        self.lexicon = lexicon
        self.decisions = decisions or {}
        self.matcher = H5Matcher(self.contract, lexicon)
        self.auditor = LineAuditor(self.contract, policy)
        self.resolved = resolve_lines(self.occurrences, self.matcher, self.decisions)

    def run(self) -> list[H5InvoiceResult]:
        return audit_resolved(self.occurrences, self.resolved, self.auditor)
