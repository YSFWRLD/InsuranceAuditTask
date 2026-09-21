"""Hospital 1: the audit.

Given the parsed contract and the matched invoice lines, decide for every
invoice whether it is wrong, why, and -- separately -- whether the correct
total can be reconstructed and what it is.

Contents
    1. Inputs              where the Hospital 1 files live
    2. Global context      hospital-wide indexes; ambiguity carried as intervals
    3. Pricing             clause 3.2, stage by stage, in integer cents
    4. Stateless checks    contract number, dates, arithmetic, unit basis
    5. Auditor             caps, duplicates, exclusions, rates, reconstruction
    6. Confidence          evidence bands -- NOT calibrated probabilities
    7. Pipeline            files -> results; the label-free entry point

Nothing in this module reads a label file.  ``tests/hospital_1/
test_evaluation.py`` enforces that structurally.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from ..shared.data import load_occurrences
from ..shared.models import (
    AuditResult,
    Finding,
    InvoiceOccurrence,
    LineItem,
    MatchStatus,
    ServiceMatch,
)
from ..shared.money import apply_percentage_change, multiply_cents
from .contract import CONTRACT_PATH, ContractRules, parse_contract
from .matcher import ServiceMatcher


# ==========================================================================
# 1. Inputs
# ==========================================================================

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = CONTRACT_PATH
JSONL = REPO_ROOT / "invoices" / "hospital_1_invoices.jsonl"
INVOICES_CSV = REPO_ROOT / "invoices" / "hospital_1_invoices.csv"
LINES_CSV = REPO_ROOT / "invoices" / "hospital_1_line_items.csv"


# ==========================================================================
# 2. Global context
# ==========================================================================
#
# Hospital-wide state that individual lines cannot be priced without.
#
# Several Hospital 1 rules are stateful: a threshold premium depends on the
# patient's whole service day, a volume discount on utilisation across the entire
# contract term and every patient, an exclusion window on what was billed weeks
# earlier, a duplicate on what was billed on another invoice entirely.  So the
# engine indexes everything once, up front, and prices against those indexes.
#
# Ambiguity is carried through as an interval rather than collapsed.  If 53 lines
# might or might not be "Preoperative Immunologic Endoscopic Procedure", then the
# cumulative utilisation of that service before any given line is not a number,
# it is a range.  Where the range straddles a discount threshold the engine says
# so instead of picking an end of it.

@dataclass(frozen=True)
class ResolvedLine:
    """A line item together with everything derived from it before pricing."""

    occurrence: InvoiceOccurrence
    line: LineItem
    match: ServiceMatch

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
    def service(self) -> Optional[str]:
        """The single service this line certainly is, if there is one."""
        return self.match.service if self.match.status is MatchStatus.MATCHED else None

    @property
    def possible_services(self) -> tuple[str, ...]:
        """Every service this line might be, including the certain case."""
        if self.match.status is MatchStatus.MATCHED:
            return (self.match.service,)  # type: ignore[return-value]
        if self.match.status is MatchStatus.AMBIGUOUS:
            return self.match.candidate_services
        return ()


@dataclass
class Interval:
    """A quantity known only to lie in ``[low, high]``."""

    low: int = 0
    high: int = 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"[{self.low}, {self.high}]"


def _sort_key(rl: ResolvedLine) -> tuple:
    """Clause 7.2 ordering: service date, then ascending line identifier.

    Lines whose service date is malformed cannot be placed in the sequence.
    They sort last and are excluded from cumulative counting; the audit records
    that exclusion as an uncertainty rather than pretending the count is exact.
    """
    date = rl.service_date
    return (date is None, date or _dt.date.max, rl.line_id)


class AuditContext:
    """All cross-line indexes, built once for the whole hospital."""

    def __init__(
        self,
        rules: ContractRules,
        resolved: Iterable[ResolvedLine],
        *,
        as_of: Optional[_dt.date] = None,
    ):
        """``as_of`` restricts history to lines on or before that service date.

        It exists for the prospective/temporal evaluation, where earlier
        invoices may inform later ones but never the reverse.  ``None`` means
        the full retrospective view.
        """
        self.rules = rules
        self.as_of = as_of
        self.lines: list[ResolvedLine] = sorted(resolved, key=_sort_key)

        # -- daily aggregates: (patient, service, date) -> Interval ----------
        self._daily: dict[tuple[str, str, _dt.date], Interval] = defaultdict(Interval)

        # -- services present for a patient on a day (for bundles) ----------
        self._day_certain: dict[tuple[str, _dt.date], set[str]] = defaultdict(set)
        self._day_possible: dict[tuple[str, _dt.date], set[str]] = defaultdict(set)

        # -- patient/service dates (for exclusion windows and duplicates) ---
        # Kept split: a date only *possibly* carrying a service (because the
        # description was ambiguous) must never by itself prove a violation.
        self._dates_certain: dict[tuple[str, str], set[_dt.date]] = defaultdict(set)
        self._dates_possible: dict[tuple[str, str], set[_dt.date]] = defaultdict(set)

        # -- occurrences of (patient, service, date) ------------------------
        self._same_service_day: dict[tuple[str, str, _dt.date], list[ResolvedLine]] = (
            defaultdict(list)
        )

        for rl in self.lines:
            date = rl.service_date
            if date is None:
                continue
            if as_of is not None and date > as_of:
                continue
            certain = rl.service
            for svc in rl.possible_services:
                key = (rl.patient_id, svc, date)
                agg = self._daily[key]
                agg.high += rl.line.quantity
                if certain == svc:
                    agg.low += rl.line.quantity
                self._day_possible[(rl.patient_id, date)].add(svc)
                self._dates_possible[(rl.patient_id, svc)].add(date)
            if certain is not None:
                self._dates_certain[(rl.patient_id, certain)].add(date)
                self._day_certain[(rl.patient_id, date)].add(certain)
                self._same_service_day[(rl.patient_id, certain, date)].append(rl)

        # -- cumulative utilisation, prior-exclusive ------------------------
        # Clause 2.4: "up to but excluding the line item being priced".
        self._prior_cumulative: dict[str, dict[str, Interval]] = defaultdict(dict)
        running: dict[str, Interval] = defaultdict(Interval)
        for rl in self.lines:
            if rl.service_date is None:
                continue
            if as_of is not None and rl.service_date > as_of:
                continue
            for svc in set(rl.possible_services):
                snapshot = running[svc]
                self._prior_cumulative[rl.line_id][svc] = Interval(
                    snapshot.low, snapshot.high
                )
            # Advance only after snapshotting, so a line never discounts itself.
            for svc in set(rl.possible_services):
                run = running[svc]
                run.high += rl.line.quantity
                if rl.service is not None and rl.service == svc:
                    run.low += rl.line.quantity

    # -- accessors ----------------------------------------------------------

    def daily_quantity(self, patient: str, service: str, date: _dt.date) -> Interval:
        return self._daily.get((patient, service, date), Interval(0, 0))

    def prior_cumulative(self, line_id: str, service: str) -> Interval:
        return self._prior_cumulative.get(line_id, {}).get(service, Interval(0, 0))

    def services_on_day(self, patient: str, date: _dt.date) -> tuple[set[str], set[str]]:
        """``(certainly present, possibly present)`` for bundle detection."""
        return (
            self._day_certain.get((patient, date), set()),
            self._day_possible.get((patient, date), set()),
        )

    def dates_for(
        self, patient: str, service: str, *, certain_only: bool = True
    ) -> set[_dt.date]:
        index = self._dates_certain if certain_only else self._dates_possible
        return index.get((patient, service), set())

    def lines_for_service_day(
        self, patient: str, service: str, date: _dt.date
    ) -> list[ResolvedLine]:
        return self._same_service_day.get((patient, service, date), [])


# ==========================================================================
# 3. Pricing
# ==========================================================================
#
# Deterministic pricing, in integer cents, following clause 3.2 exactly.
#
#     (a) substitution of a bundled rate
#     (b) the facility multiplier
#     (c) the plan-tier multiplier
#     (d) any premium or uplift
#     (e) any cumulative volume discount
#         then multiply by the billed quantity
#
# Clause 3.1: "Rounding is applied after each individual step of the
# calculation, not once at the end."  So every stage rounds to a whole cent,
# half away from zero, and the next stage starts from that rounded figure.
#
# Hospital 1's facility and plan-tier multipliers are both 1.0.  The stages are
# implemented anyway -- they cost nothing, they keep the trace honest about what
# the contract says is happening, and they are the stages most likely to differ
# under another hospital's agreement.
#
# The engine also prices *counterfactuals*: the same line with one stage
# suppressed or forced.  That is how a wrong unit price is attributed to a
# particular contractual mistake ("the weekend uplift was left off") instead of
# being reported as an undifferentiated mismatch.

@dataclass
class PriceTraceStep:
    label: str
    unit_rate_cents: int


@dataclass
class LinePrice:
    """The contractually correct price of one line, where it is computable."""

    line_id: str
    service: Optional[str]
    expected_unit_rate_cents: Optional[int]
    expected_line_total_cents: Optional[int]
    billed_quantity: int
    priced_quantity: int
    trace: list[PriceTraceStep] = field(default_factory=list)
    complete: bool = True
    uncertainty_reasons: list[str] = field(default_factory=list)

    def trace_text(self) -> str:
        return "\n".join(f"{s.label}: {s.unit_rate_cents}" for s in self.trace)


def is_business_day(date: _dt.date) -> bool:
    """Clause 2.2: any service day other than a Saturday or a Sunday."""
    return date.weekday() < 5


@dataclass
class PricingOptions:
    """Switches used by the ablation harness.  All default to on."""

    bundles: bool = True
    premiums: bool = True
    volume_discounts: bool = True


@dataclass
class StageInputs:
    """What the contract says each adjustment stage should do to this line.

    Each stage carries a tuple of *plausible* values rather than a single one.
    Normally the tuple has one element.  It has more when something outside
    this line is genuinely unknown -- an ambiguous description elsewhere on the
    same service day may or may not have pushed the patient past a premium
    threshold, may or may not have completed a bundle, may or may not count
    towards cumulative utilisation.  The first element is the reading the
    engine would assert if it had to choose; the rest are the readings it
    cannot rule out.
    """

    bundle_options: tuple[bool, ...] = (False,)
    bundle_partner: Optional[str] = None

    premium_options: tuple[Decimal, ...] = (Decimal(0),)
    premium_label: str = "no premium"
    #: The uplift this service *could* carry, whether or not it is due here.
    premium_available: Decimal = Decimal(0)
    premium_kind: Optional[str] = None      # "threshold" | "non_business_day"

    discount_options: tuple[Decimal, ...] = (Decimal(0),)
    #: Every discount fraction in this service's schedule.
    discount_available: tuple[Decimal, ...] = ()

    uncertainty: tuple[str, ...] = ()

    @property
    def bundled(self) -> bool:
        return self.bundle_options[0]

    @property
    def premium_fraction(self) -> Decimal:
        return self.premium_options[0]

    @property
    def discount_fraction(self) -> Decimal:
        return self.discount_options[0]

    @property
    def certain(self) -> bool:
        return (
            len(self.bundle_options) == 1
            and len(self.premium_options) == 1
            and len(self.discount_options) == 1
        )


class PricingEngine:
    def __init__(self, rules: ContractRules, options: Optional[PricingOptions] = None):
        self.rules = rules
        self.options = options or PricingOptions()

    # -- stage inputs -------------------------------------------------------

    def _facility_multiplier(self, facility_code: str) -> Decimal:
        m = self.rules.facility_multipliers
        return m.get(facility_code, m.get("*", Decimal(1)))

    def _plan_tier_multiplier(self, plan_tier: str) -> Decimal:
        m = self.rules.plan_tier_multipliers
        return m.get(plan_tier, m.get("*", Decimal(1)))


    def stage_inputs(
        self, rl: ResolvedLine, service: str, ctx: AuditContext
    ) -> StageInputs:
        si = StageInputs()
        reasons: list[str] = []
        date = rl.service_date

        # -- (a) bundled-rate substitution ---------------------------------
        partner = self.rules.bundle_partner(service)
        if partner is not None and self.options.bundles and date is not None:
            partner_name, _ = partner
            si.bundle_partner = partner_name
            certain_set, possible_set = ctx.services_on_day(rl.patient_id, date)
            if partner_name in certain_set:
                si.bundle_options = (True,)
            elif partner_name in possible_set:
                # The partner may or may not have been delivered that day.
                si.bundle_options = (True, False)
                reasons.append(
                    f"bundle partner {partner_name!r} may or may not have been "
                    f"delivered on {date}; an ambiguous description shares that day"
                )

        # -- (d) premium / uplift -------------------------------------------
        premium = self.rules.threshold_premiums.get(service)
        uplift = self.rules.non_business_day_uplifts.get(service)
        if premium is not None:
            si.premium_kind = "threshold"
            si.premium_available = premium.uplift_fraction
        elif uplift is not None:
            si.premium_kind = "non_business_day"
            si.premium_available = uplift.uplift_fraction

        if self.options.premiums and date is not None:
            if premium is not None:
                daily = ctx.daily_quantity(rl.patient_id, service, date)
                low_applies = daily.low > premium.exceeds_units
                high_applies = daily.high > premium.exceeds_units
                if low_applies:
                    si.premium_options = (premium.uplift_fraction,)
                    si.premium_label = _pct_label(
                        "threshold premium", premium.uplift_fraction
                    )
                elif high_applies:
                    # Clause 5.1 assesses the premium against the aggregate
                    # daily quantity, and part of that aggregate is a line we
                    # could not identify.  Both readings stay open.
                    si.premium_options = (Decimal(0), premium.uplift_fraction)
                    reasons.append(
                        f"aggregate daily quantity of {service!r} on {date} is only "
                        f"known to lie in {daily}, which straddles the "
                        f"{premium.exceeds_units}-unit premium threshold"
                    )
            elif uplift is not None and not is_business_day(date):
                si.premium_options = (uplift.uplift_fraction,)
                si.premium_label = _pct_label(
                    "non-business-day uplift", uplift.uplift_fraction
                )
        elif date is None and si.premium_available != 0:
            si.premium_options = (Decimal(0), si.premium_available)
            reasons.append(
                "service date is malformed, so the premium stage cannot be evaluated"
            )

        # -- (e) cumulative volume discount ----------------------------------
        tiers = self.rules.volume_discounts.get(service) or []
        si.discount_available = tuple(t.discount_fraction for t in tiers)
        if tiers and self.options.volume_discounts:
            if date is None:
                si.discount_options = (Decimal(0),) + si.discount_available
                reasons.append(
                    "service date is malformed, so this line's position in the "
                    "cumulative sequence is unknown"
                )
            else:
                prior = ctx.prior_cumulative(rl.line_id, service)

                def tier_for(total: int) -> Decimal:
                    # Clause 7.2: where two thresholds are met, the deeper
                    # discount applies.
                    best = Decimal(0)
                    for t in tiers:
                        if total > t.exceeds_units:
                            best = max(best, t.discount_fraction)
                    return best

                low, high = tier_for(prior.low), tier_for(prior.high)
                if low == high:
                    si.discount_options = (low,)
                else:
                    between = sorted(
                        {low, high} | {f for f in si.discount_available if low < f < high}
                    )
                    # Primary reading first: the tier the certain lines alone
                    # support.  The rest are readings we cannot rule out.
                    si.discount_options = (low,) + tuple(f for f in between if f != low)
                    reasons.append(
                        f"cumulative utilisation of {service!r} before this line is "
                        f"only known to lie in {prior}, which straddles a discount "
                        f"threshold"
                    )

        si.uncertainty = tuple(reasons)
        return si

    # -- computation --------------------------------------------------------

    def compute(
        self,
        rl: ResolvedLine,
        service: str,
        si: StageInputs,
        *,
        quantity: Optional[int] = None,
        bundle: Optional[bool] = None,
        premium_fraction: Optional[Decimal] = None,
        discount_fraction: Optional[Decimal] = None,
    ) -> LinePrice:
        """Run the five stages.  Any stage may be overridden for a counterfactual."""
        rule = self.rules.services[service]
        qty = rl.line.quantity if quantity is None else quantity
        trace: list[PriceTraceStep] = []

        rate = rule.base_rate_cents
        trace.append(PriceTraceStep("base rate", rate))

        # (a) bundled-rate substitution
        use_bundle = si.bundled if bundle is None else bundle
        if use_bundle:
            partner = self.rules.bundle_partner(service)
            if partner is not None:
                rate = partner[1]
                trace.append(PriceTraceStep(f"bundled rate (with {partner[0]})", rate))

        # (b) facility multiplier
        fac = self._facility_multiplier(rl.occurrence.facility_code)
        rate = multiply_cents(rate, fac)
        trace.append(PriceTraceStep(f"facility multiplier x{_plain(fac)}", rate))

        # (c) plan-tier multiplier
        tier = self._plan_tier_multiplier(rl.occurrence.plan_tier)
        rate = multiply_cents(rate, tier)
        trace.append(PriceTraceStep(f"plan-tier multiplier x{_plain(tier)}", rate))

        # (d) premium / uplift
        pf = si.premium_fraction if premium_fraction is None else premium_fraction
        rate = apply_percentage_change(rate, pf)
        label = si.premium_label if premium_fraction is None else _pct_label("premium", pf)
        trace.append(PriceTraceStep(label, rate))

        # (e) cumulative volume discount
        df = si.discount_fraction if discount_fraction is None else discount_fraction
        rate = apply_percentage_change(rate, -df)
        trace.append(
            PriceTraceStep(
                _pct_label("volume discount", -df) if df else "no volume discount", rate
            )
        )

        total = rate * qty
        trace.append(PriceTraceStep(f"x quantity {qty}", total))

        return LinePrice(
            line_id=rl.line_id,
            service=service,
            expected_unit_rate_cents=rate,
            expected_line_total_cents=total,
            billed_quantity=rl.line.quantity,
            priced_quantity=qty,
            trace=trace,
            complete=si.certain,
            uncertainty_reasons=list(si.uncertainty),
        )

    def price_line(
        self,
        rl: ResolvedLine,
        service: str,
        ctx: AuditContext,
        *,
        quantity: Optional[int] = None,
    ) -> LinePrice:
        return self.compute(
            rl, service, self.stage_inputs(rl, service, ctx), quantity=quantity
        )

    # -- plausible readings --------------------------------------------------

    def plausible_prices(
        self,
        rl: ResolvedLine,
        service: str,
        si: StageInputs,
        *,
        quantity: Optional[int] = None,
    ) -> dict[int, int]:
        """``{unit rate: line total}`` over every reading the evidence allows.

        With everything known this has exactly one entry and equals the
        contract price.  Where a stage is genuinely undetermined it has
        several, and a billed rate matching any of them is not evidence of an
        error.
        """
        out: dict[int, int] = {}
        for b in si.bundle_options:
            for pf in si.premium_options:
                for df in si.discount_options:
                    lp = self.compute(
                        rl, service, si,
                        quantity=quantity,
                        bundle=b,
                        premium_fraction=pf,
                        discount_fraction=df,
                    )
                    out.setdefault(
                        lp.expected_unit_rate_cents, lp.expected_line_total_cents
                    )
        return out

    # -- counterfactuals ----------------------------------------------------

    def rate_variants(
        self, rl: ResolvedLine, service: str, si: StageInputs
    ) -> dict[str, tuple[int, ...]]:
        """Unit rates under single-stage deviations from the contract.

        Used only to *explain* a mismatch that has already been established by
        comparing the billed rate against every plausible correct rate.  Never
        used to pick a service.
        """
        variants: dict[str, tuple[int, ...]] = {}

        def rate(**kw) -> int:
            return self.compute(rl, service, si, **kw).expected_unit_rate_cents  # type: ignore[return-value]

        if si.bundle_partner is not None:
            if si.bundled:
                variants["bundle_not_applied"] = (rate(bundle=False),)
            else:
                variants["bundle_incorrectly_applied"] = (rate(bundle=True),)

        if si.premium_fraction != 0:
            variants["premium_omitted"] = (rate(premium_fraction=Decimal(0)),)
        elif si.premium_available != 0:
            variants["premium_incorrectly_applied"] = (
                rate(premium_fraction=si.premium_available),
            )

        if si.discount_fraction != 0:
            variants["volume_discount_omitted"] = (rate(discount_fraction=Decimal(0)),)
        # Every *other* tier in this service's schedule, not just the first:
        # billing the 20% rate where 12% was due and billing the 12% rate where
        # none was due are both "the wrong tier was applied".
        wrong_tiers = tuple(
            rate(discount_fraction=f)
            for f in si.discount_available
            if f != si.discount_fraction
        )
        if wrong_tiers:
            variants["volume_discount_incorrectly_applied"] = wrong_tiers
        return variants


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _pct_label(prefix: str, fraction: Decimal) -> str:
    if fraction == 0:
        return f"no {prefix}"
    pct = (fraction * 100).normalize()
    sign = "+" if fraction > 0 else ""
    return f"{prefix} {sign}{format(pct, 'f')}%"


# ==========================================================================
# 4. Stateless checks
# ==========================================================================
#
# Stateless, line- and invoice-level checks.
#
# These are the checks that need nothing but the row in front of them and the
# contract header.  Everything stateful (caps, premiums, discounts, duplicates,
# exclusion windows) is in section 5 because it needs the hospital-wide
# context.

def check_contract_number(
    occ: InvoiceOccurrence, rules: ContractRules
) -> list[Finding]:
    """Clause 11.1: each invoice shall quote the contract number."""
    if occ.contract_number != rules.contract_number:
        return [
            Finding(
                category="contract_number_mismatch",
                detail=(
                    f"invoice quotes contract {occ.contract_number!r}; this "
                    f"agreement is {rules.contract_number!r}"
                ),
            )
        ]
    return []


def check_service_dates(
    occ: InvoiceOccurrence, rules: ContractRules
) -> list[Finding]:
    """Clause 11.3, plus basic well-formedness of the date itself."""
    findings: list[Finding] = []
    for line in occ.line_items:
        if line.service_date is None:
            findings.append(
                Finding(
                    category="malformed_service_date",
                    detail=f"service date {line.service_date_raw!r} is not a valid date",
                    line_id=line.line_id,
                    blocks_reconstruction=True,
                )
            )
            continue
        out_of_term = not (
            rules.effective_from <= line.service_date <= rules.effective_to
        )
        if out_of_term:
            findings.append(
                Finding(
                    category="service_date_out_of_window",
                    detail=(
                        f"service date {line.service_date} falls outside the term "
                        f"{rules.effective_from} to {rules.effective_to}"
                    ),
                    line_id=line.line_id,
                )
            )
        # Clause 11.3 states both requirements in one sentence. A date pushed
        # past the end of the term is usually also past the invoice date; that
        # is one defect in the line, not two, and the term breach is the more
        # specific statement of it.
        if out_of_term:
            continue
        if occ.invoice_date is not None and line.service_date > occ.invoice_date:
            findings.append(
                Finding(
                    category="service_date_after_invoice_date",
                    detail=(
                        f"service date {line.service_date} is after the invoice date "
                        f"{occ.invoice_date}"
                    ),
                    line_id=line.line_id,
                )
            )
    return findings


def check_line_arithmetic(occ: InvoiceOccurrence) -> list[Finding]:
    """Clause 3.3: the line total is the unit rate times the billed quantity.

    This is an internal-consistency check on the invoice as submitted.  It says
    nothing about whether the unit price itself is right.
    """
    findings: list[Finding] = []
    for line in occ.line_items:
        expected = line.unit_price_cents * line.quantity
        if line.line_total_cents != expected:
            findings.append(
                Finding(
                    category="line_total_arithmetic",
                    detail=(
                        f"line total {line.line_total_cents} != unit price "
                        f"{line.unit_price_cents} x quantity {line.quantity} "
                        f"(= {expected})"
                    ),
                    line_id=line.line_id,
                )
            )
    return findings


def check_invoice_total(occ: InvoiceOccurrence) -> list[Finding]:
    """Clause 3.3: the invoice total is the sum of its line totals."""
    summed = sum(line.line_total_cents for line in occ.line_items)
    if occ.invoice_total_cents != summed:
        return [
            Finding(
                category="invoice_total_mismatch",
                detail=(
                    f"invoice total {occ.invoice_total_cents} != sum of line totals "
                    f"{summed}"
                ),
            )
        ]
    return []


def check_unit_basis(rl: ResolvedLine, rules: ContractRules) -> list[Finding]:
    """Section 4: each service is billed on one stated basis.

    Suppressed when the matcher used the billed basis to choose between tied
    services.  In that case the basis is the evidence that produced the
    service, and calling it wrong would be counting that evidence twice with
    opposite signs.
    """
    if rl.match.used_unit_basis_for_matching:
        return []
    billed = rl.line.unit_basis_as_billed
    if billed is None:
        return [
            Finding(
                category="wrong_unit_basis",
                detail=f"unit basis {rl.line.unit_basis_raw!r} is not a recognised basis",
                line_id=rl.line_id,
            )
        ]
    candidates = rl.possible_services
    if not candidates:
        return []
    # For an ambiguous line, the basis is only wrong if *no* plausible reading
    # of the description supports it.
    if any(rules.services[s].unit_basis is billed for s in candidates):
        return []
    expected = sorted({rules.services[s].unit_basis.value for s in candidates})
    return [
        Finding(
            category="wrong_unit_basis",
            detail=(
                f"billed {billed.value!r}; "
                + (
                    f"{candidates[0]!r} is billed {expected[0]!r}"
                    if len(candidates) == 1
                    else f"no plausible service ({', '.join(candidates)}) is billed {billed.value!r}"
                )
            ),
            line_id=rl.line_id,
        )
    ]


def check_service_identified(rl: ResolvedLine) -> list[Finding]:
    """A description that matches nothing in the rate schedule."""
    if rl.match.status is MatchStatus.UNKNOWN:
        return [
            Finding(
                category="unknown_service",
                detail=(
                    f"description {rl.line.description!r} does not identify any "
                    f"contracted service (best candidate scored "
                    f"{rl.match.match_score:.2f})"
                ),
                line_id=rl.line_id,
                blocks_reconstruction=True,
            )
        ]
    return []


def check_duplicate_invoice_id(
    occurrences: Iterable[InvoiceOccurrence],
) -> list[Finding]:
    """Clause 11.2: an invoice identifier may not be reused."""
    occurrences = list(occurrences)
    if len(occurrences) <= 1:
        return []
    return [
        Finding(
            category="duplicate_invoice_id",
            detail=(
                f"invoice identifier {occurrences[0].invoice_id!r} is used by "
                f"{len(occurrences)} separate invoice records "
                f"(dates {', '.join(o.invoice_date_raw for o in occurrences)})"
            ),
        )
    ]


# ==========================================================================
# 5. Auditor
# ==========================================================================
#
# The audit itself: stateful contract checks and monetary reconstruction.
#
# Two questions are answered separately and must not be conflated:
#
#     Is this invoice wrong?            -> ``AuditResult.flagged``
#     What should it have totalled?     -> ``AuditResult.expected_total_cents``
#
# The second can fail while the first succeeds.  A malformed service date proves
# the invoice is wrong and simultaneously destroys our ability to place that line
# in the cumulative sequence, so we flag confidently and decline to price.

@dataclass
class AuditPolicy:
    """Readings of the contract that are defensible but not forced by its text.

    Each of these is a genuine ambiguity, recorded here in one place so that
    the decision log and the code cannot drift apart.
    """

    #: Section 10 does not say "to the same Patient".  Read per patient: an
    #: exclusion window between two clinical services is a statement about a
    #: course of treatment, and a cross-patient reading would make the rule
    #: depend on unrelated people's care.
    exclusion_windows_are_per_patient: bool = True

    #: "Not billable within 7 days" read inclusively: a service 7 days later is
    #: within the window.  The alternative reading (< 7) differs only on the
    #: boundary day.
    exclusion_window_inclusive: bool = True

    #: Clause 11.4 forbids billing a service twice for the same patient and
    #: service date.  The first billing stands; later ones are not payable.
    duplicate_repeat_is_not_payable: bool = True

    #: Section 8 caps "billable units".  Pricing the capped quantity gives the
    #: *most* the payer could owe.  It does not give the corrected invoice: a
    #: quantity of 11 against a cap of 8 proves the figure is wrong without
    #: revealing what was actually delivered -- it could have been anything
    #: from 1 to 11.  So a cap breach is reported as detected-but-not-
    #: reconstructable, and the capped price is carried in
    #: ``maximum_contractually_payable_total_cents`` instead.
    cap_breach_blocks_reconstruction: bool = True

    #: A description matching no contracted service cannot be priced from this
    #: agreement at all.  We do not know that it is worth nothing, so the
    #: billed amount is carried through and the reconstruction is marked
    #: incomplete rather than silently zeroed.
    unknown_service_is_not_payable: bool = False


@dataclass
class LineOutcome:
    """What the audit concluded about one line.

    ``payable`` is the corrected line total where the contract determines it.
    ``max_payable`` is the most the payer could owe, which is defined even in
    some cases where ``payable`` is not -- a quantity beyond a daily cap has a
    known ceiling and an unknown truth.
    """

    findings: list[Finding] = field(default_factory=list)
    payable: Optional[int] = None
    max_payable: Optional[int] = None
    reasons: list[str] = field(default_factory=list)


class Auditor:
    def __init__(
        self,
        rules: ContractRules,
        matcher: ServiceMatcher,
        engine: PricingEngine,
        policy: Optional[AuditPolicy] = None,
        *,
        enable_duplicate_detection: bool = True,
        enable_date_validation: bool = True,
    ):
        self.rules = rules
        self.matcher = matcher
        self.engine = engine
        self.policy = policy or AuditPolicy()
        if not self.policy.exclusion_windows_are_per_patient:
            # The rejected reading is recorded, not implemented.  Silently
            # ignoring the switch would be worse than refusing it.
            raise NotImplementedError(
                "a cross-patient reading of Section 10 is not implemented; see "
                "AuditPolicy.exclusion_windows_are_per_patient"
            )
        self.enable_duplicate_detection = enable_duplicate_detection
        self.enable_date_validation = enable_date_validation

    # ------------------------------------------------------------------
    # cap allocation
    # ------------------------------------------------------------------

    def _cap_allocation(self, ctx: AuditContext) -> dict[str, int]:
        """Payable units per line under the Section 8 daily caps.

        The cap is per patient per service day, so it is shared across every
        line -- and every invoice -- touching that day.  Units are allocated in
        ascending line-identifier order, the same tie-break the contract uses
        in clause 7.2 for the cumulative sequence.
        """
        allowed: dict[str, int] = {}
        groups: dict[tuple[str, str, _dt.date], list[ResolvedLine]] = defaultdict(list)
        for rl in ctx.lines:
            svc = rl.service
            if svc is None or rl.service_date is None:
                continue
            if svc in self.rules.daily_caps:
                groups[(rl.patient_id, svc, rl.service_date)].append(rl)
        for (_, svc, _), lines in groups.items():
            cap = self.rules.daily_caps[svc].max_units
            remaining = cap
            for rl in sorted(lines, key=lambda r: r.line_id):
                take = max(0, min(rl.line.quantity, remaining))
                allowed[rl.line_id] = take
                remaining -= take
        return allowed

    # ------------------------------------------------------------------
    # duplicates
    # ------------------------------------------------------------------

    def _duplicate_lines(self, ctx: AuditContext) -> dict[str, Finding]:
        """Clause 11.4, evaluated across the whole data set.

        The earliest billing of a (patient, service, service date) stands; each
        later one is a duplicate.  "Earliest" is by invoice date, then invoice
        id, then line id, so the result does not depend on file order.
        """
        findings: dict[str, Finding] = {}
        if not self.enable_duplicate_detection:
            return findings

        groups: dict[tuple[str, str, _dt.date], list[ResolvedLine]] = defaultdict(list)
        for rl in ctx.lines:
            svc = rl.service
            if svc is None or rl.service_date is None:
                continue
            groups[(rl.patient_id, svc, rl.service_date)].append(rl)

        for (patient, svc, date), lines in groups.items():
            if len(lines) < 2:
                continue
            ordered = sorted(
                lines,
                key=lambda r: (
                    r.occurrence.invoice_date or _dt.date.max,
                    r.occurrence.invoice_id,
                    r.occurrence.occurrence_index,
                    r.line_id,
                ),
            )
            first = ordered[0]
            for rl in ordered[1:]:
                same_invoice = rl.occurrence.occurrence_id == first.occurrence.occurrence_id
                findings[rl.line_id] = Finding(
                    category="duplicate_service" if same_invoice else "cross_invoice_duplicate",
                    detail=(
                        f"{svc!r} already billed for patient {patient} on {date} "
                        f"(line {first.line_id} of invoice {first.occurrence.invoice_id})"
                    ),
                    line_id=rl.line_id,
                )
        return findings

    # ------------------------------------------------------------------
    # exclusion windows
    # ------------------------------------------------------------------

    def _exclusion_finding(
        self, rl: ResolvedLine, ctx: AuditContext
    ) -> tuple[Optional[Finding], list[str]]:
        """Section 10, evaluated only against services we are sure were given.

        A date that merely *might* carry the paired service -- because some
        other line that day had an ambiguous description -- is not proof of a
        violation.  It is recorded as an uncertainty instead.
        """
        svc = rl.service
        date = rl.service_date
        if svc is None or date is None:
            return None, []

        def within(delta: int, window_days: int) -> bool:
            return (
                delta <= window_days
                if self.policy.exclusion_window_inclusive
                else delta < window_days
            )

        reasons: list[str] = []
        for window in self.rules.exclusions_for(svc):
            certain = ctx.dates_for(rl.patient_id, window.other_service)
            for other in sorted(certain):
                delta = abs((date - other).days)
                if within(delta, window.days):
                    return (
                        Finding(
                            category="exclusion_window_violation",
                            detail=(
                                f"{svc!r} billed on {date}, within {window.days} days "
                                f"of {window.other_service!r} on {other} "
                                f"({delta} day(s) apart) -- Section 10"
                            ),
                            line_id=rl.line_id,
                        ),
                        reasons,
                    )
            maybe = ctx.dates_for(rl.patient_id, window.other_service, certain_only=False)
            for other in sorted(maybe - certain):
                if within(abs((date - other).days), window.days):
                    reasons.append(
                        f"line {rl.line_id}: {window.other_service!r} may have been "
                        f"delivered on {other}, which would put this line inside a "
                        f"Section 10 exclusion window; the description on that day "
                        f"is ambiguous, so this is not asserted"
                    )
                    break
        return None, reasons

    # ------------------------------------------------------------------
    # per-line audit
    # ------------------------------------------------------------------

    def _audit_line(
        self,
        rl: ResolvedLine,
        ctx: AuditContext,
        cap_allowed: dict[str, int],
        duplicate_findings: dict[str, Finding],
    ) -> LineOutcome:
        findings: list[Finding] = []
        reasons: list[str] = []
        line = rl.line

        findings.extend(check_service_identified(rl))
        findings.extend(check_unit_basis(rl, self.rules))

        # --- unknown service ------------------------------------------------
        if rl.match.status is MatchStatus.UNKNOWN:
            reasons.append(
                f"line {rl.line_id}: description matches no contracted service, so "
                f"its correct price is unknown"
            )
            payable = 0 if self.policy.unknown_service_is_not_payable else line.line_total_cents
            return LineOutcome(findings, payable, payable, reasons)

        # --- duplicate / exclusion: line is simply not payable ---------------
        dup = duplicate_findings.get(rl.line_id)
        if dup is not None:
            findings.append(dup)
        excl, excl_reasons = self._exclusion_finding(rl, ctx)
        reasons.extend(excl_reasons)
        if excl is not None:
            findings.append(excl)

        not_payable = (
            (dup is not None and self.policy.duplicate_repeat_is_not_payable)
            or excl is not None
        )

        # --- ambiguous service ----------------------------------------------
        if rl.match.status is MatchStatus.AMBIGUOUS:
            f, payable, r = self._audit_ambiguous_line(rl, ctx)
            findings.extend(f)
            reasons.extend(r)
            if not_payable:
                payable = 0
            return LineOutcome(findings, payable, payable, reasons)

        service = rl.service
        assert service is not None
        si = self.engine.stage_inputs(rl, service, ctx)
        correct = self.engine.compute(rl, service, si)
        plausible = self.engine.plausible_prices(rl, service, si)
        reasons.extend(f"line {rl.line_id}: {x}" for x in correct.uncertainty_reasons)

        # --- daily cap -------------------------------------------------------
        # A breach is detected with certainty from the invoice.  The corrected
        # amount is not: the cap tells us the ceiling, not the truth.
        capped_qty = line.quantity
        cap_breached = False
        cap = self.rules.daily_caps.get(service)
        if cap is not None and rl.service_date is not None:
            daily = ctx.daily_quantity(rl.patient_id, service, rl.service_date)
            if daily.low > cap.max_units:
                cap_breached = True
                capped_qty = cap_allowed.get(rl.line_id, line.quantity)
                findings.append(
                    Finding(
                        category="daily_cap_exceeded",
                        detail=(
                            f"{service!r}: {daily.low} units billed for patient "
                            f"{rl.patient_id} on {rl.service_date}; Section 8 caps "
                            f"this at {cap.max_units}"
                        ),
                        line_id=rl.line_id,
                        blocks_reconstruction=self.policy.cap_breach_blocks_reconstruction,
                    )
                )
                reasons.append(
                    f"line {rl.line_id}: {line.quantity} units billed against a cap of "
                    f"{cap.max_units}, so the quantity on the invoice is not the "
                    f"quantity delivered and the corrected total cannot be recovered "
                    f"from the invoice; the capped price is an upper bound only"
                )
            elif daily.high > cap.max_units:
                reasons.append(
                    f"line {rl.line_id}: daily quantity of {service!r} is only known to "
                    f"lie in {daily}, which straddles the Section 8 cap"
                )

        # --- unit price ------------------------------------------------------
        # The billed rate is wrong only if it matches *no* reading the evidence
        # leaves open.  With everything known, ``plausible`` has one entry and
        # this is an exact comparison against the contract rate.
        rate_ok = line.unit_price_cents in plausible
        if not rate_ok:
            findings.append(self._diagnose_rate(rl, service, si, correct))

        # --- payable amount ---------------------------------------------------
        def priced(qty: int) -> Optional[int]:
            if correct.complete:
                return self.engine.compute(
                    rl, service, si, quantity=qty
                ).expected_line_total_cents
            if rate_ok:
                # Several readings survive, but the provider billed one of them,
                # so the line is contractually valid under that reading.
                return self.engine.plausible_prices(
                    rl, service, si, quantity=qty
                ).get(line.unit_price_cents)
            return None

        max_payable = 0 if not_payable else priced(capped_qty)
        if not_payable:
            payable: Optional[int] = 0
        elif cap_breached and self.policy.cap_breach_blocks_reconstruction:
            payable = None
        else:
            payable = priced(capped_qty)
        return LineOutcome(findings, payable, max_payable, reasons)

    def _diagnose_rate(
        self, rl: ResolvedLine, service: str, si, correct: LinePrice
    ) -> Finding:
        """Name the contractual stage that explains a wrong unit price.

        The mismatch has already been established by comparing against the
        correct rate.  This only decides *what to call it*, by asking which
        single-stage deviation reproduces the billed figure.
        """
        billed = rl.line.unit_price_cents
        for category, rates in self.engine.rate_variants(rl, service, si).items():
            if billed in rates:
                return Finding(
                    category=category,
                    detail=(
                        f"{service!r} billed at {billed}; the contract rate is "
                        f"{correct.expected_unit_rate_cents}. The billed figure is "
                        f"exactly the rate with this adjustment wrong."
                    ),
                    line_id=rl.line_id,
                )
        return Finding(
            category="unit_price_mismatch",
            detail=(
                f"{service!r} billed at {billed}; the contract rate is "
                f"{correct.expected_unit_rate_cents} "
                f"({' -> '.join(s.label for s in correct.trace[:-1])})"
            ),
            line_id=rl.line_id,
        )

    def _audit_ambiguous_line(
        self, rl: ResolvedLine, ctx: AuditContext
    ) -> tuple[list[Finding], Optional[int], list[str]]:
        """Audit a line whose service the text cannot pin down.

        We do not choose a service.  We ask a weaker question that the evidence
        can answer: is the billed rate correct under *any* plausible reading?
        If it is, the line gives no evidence of error and its billed amount
        stands.  If it is not, the line is wrong under every reading, which is
        a sound finding even though we still cannot say what it should be.
        """
        reasons = [
            f"line {rl.line_id}: description {rl.line.description!r} is consistent with "
            f"{len(rl.possible_services)} contracted services "
            f"({', '.join(rl.possible_services)}); identity not resolved"
        ]
        billed = rl.line.unit_price_cents
        consistent: list[tuple[str, int]] = []
        for candidate in rl.possible_services:
            si = self.engine.stage_inputs(rl, candidate, ctx)
            prices = self.engine.plausible_prices(rl, candidate, si)
            if billed in prices:
                consistent.append((candidate, prices[billed]))

        if consistent:
            totals = {t for _, t in consistent}
            if len(totals) == 1:
                return [], totals.pop(), reasons
            return [], None, reasons + [
                f"line {rl.line_id}: plausible readings disagree about the line total"
            ]

        return (
            [
                Finding(
                    category="unit_price_mismatch",
                    detail=(
                        f"billed {billed} does not match the contract rate under any "
                        f"plausible reading of {rl.line.description!r} "
                        f"({', '.join(rl.possible_services)})"
                    ),
                    line_id=rl.line_id,
                )
            ],
            None,
            reasons,
        )

    # ------------------------------------------------------------------
    # per-invoice audit
    # ------------------------------------------------------------------

    def audit(self, occurrences: Iterable[InvoiceOccurrence]) -> list[AuditResult]:
        occurrences = list(occurrences)
        resolved = [
            ResolvedLine(occ, line, self.matcher.match(line.description, line.unit_basis_as_billed))
            for occ in occurrences
            for line in occ.line_items
        ]
        ctx = AuditContext(self.rules, resolved)
        return self.audit_with_context(occurrences, ctx)

    def audit_with_context(
        self,
        occurrences: Iterable[InvoiceOccurrence],
        ctx: AuditContext,
        *,
        subjects: Optional[Iterable[InvoiceOccurrence]] = None,
    ) -> list[AuditResult]:
        """Audit invoices against an already-built context.

        ``subjects`` restricts which invoices get a result, without changing the
        context.  The temporal evaluation uses it to score later invoices
        against a context built only from earlier ones.
        """
        occurrences = list(occurrences)
        cap_allowed = self._cap_allocation(ctx)
        duplicate_findings = self._duplicate_lines(ctx)
        by_line = {rl.line_id: rl for rl in ctx.lines}

        by_id: dict[str, list[InvoiceOccurrence]] = defaultdict(list)
        for occ in occurrences:
            by_id[occ.invoice_id].append(occ)

        wanted = (
            {o.invoice_id for o in subjects} if subjects is not None else set(by_id)
        )

        results: list[AuditResult] = []
        for invoice_id in sorted(by_id):
            if invoice_id not in wanted:
                continue
            results.append(
                self._audit_invoice(by_id[invoice_id], ctx, cap_allowed, duplicate_findings, by_line)
            )
        return results

    def _audit_invoice(
        self,
        occurrences: list[InvoiceOccurrence],
        ctx: AuditContext,
        cap_allowed: dict[str, int],
        duplicate_findings: dict[str, Finding],
        by_line: dict[str, ResolvedLine],
    ) -> AuditResult:
        # Where an identifier has been reused, the reusing record is the
        # subject of the claim: it is the invoice whose submission breaks
        # clause 11.2, and it is the one whose money is in question.  Findings
        # from the earlier record are still reported, because they are also
        # findings about this identifier.
        subject = occurrences[-1]
        findings: list[Finding] = []
        reasons: list[str] = []

        findings.extend(check_duplicate_invoice_id(occurrences))

        for occ in occurrences:
            findings.extend(check_contract_number(occ, self.rules))
            if self.enable_date_validation:
                findings.extend(check_service_dates(occ, self.rules))
            findings.extend(check_line_arithmetic(occ))
            findings.extend(check_invoice_total(occ))

        payable_total: Optional[int] = 0
        max_total: Optional[int] = 0
        for occ in occurrences:
            for line in occ.line_items:
                outcome = self._audit_line(
                    by_line[line.line_id], ctx, cap_allowed, duplicate_findings
                )
                findings.extend(outcome.findings)
                if occ is not subject:
                    # A finding on a superseded record still evidences that this
                    # identifier is erroneous, but its money is not the money we
                    # are reconstructing.
                    continue
                reasons.extend(outcome.reasons)
                if outcome.payable is None:
                    payable_total = None
                elif payable_total is not None:
                    payable_total += outcome.payable
                if outcome.max_payable is None:
                    max_total = None
                elif max_total is not None:
                    max_total += outcome.max_payable

        # Clause 3.3: the corrected invoice total is the sum of corrected line
        # totals, whatever the provider wrote in the header.
        return AuditResult(
            invoice_id=subject.invoice_id,
            occurrence_ids=[o.occurrence_id for o in occurrences],
            billed_total_cents=subject.invoice_total_cents,
            flagged=bool(findings),
            findings=findings,
            expected_total_cents=payable_total,
            maximum_contractually_payable_total_cents=max_total,
            pricing_complete=payable_total is not None,
            # Having a number is not the same as being able to vouch for it.
            # An unknown-service line carries its billed amount through because
            # we have nothing better to put there, not because we verified it.
            correction_reconstructable=(
                payable_total is not None
                and not any(f.blocks_reconstruction for f in findings)
            ),
            uncertainty_reasons=reasons,
        )


# ==========================================================================
# 6. Confidence
# ==========================================================================
#
# Evidence confidence, which is *not* a calibrated probability.
#
# The number this section produces is a review priority: how much of the claim
# rests on a deterministic contract rule applied to fully known inputs, and how
# much rests on a reading that could be wrong.  It has not been calibrated
# against outcomes, and it must not be reported as "the chance this invoice is
# erroneous".  It is ordinal, and the band matters more than the digits.
#
# Nothing here looks at a label, and nothing here is adjusted because a
# particular invoice happened to be right or wrong during development.

#: Representative value for each band.  Deliberately coarse.
BAND_VALUES = {"high": 0.92, "medium": 0.70, "low": 0.40}

#: Findings that follow from the invoice alone -- no matching, no pricing, no
#: cross-line state.  Either the field parses or it does not.
_STRUCTURAL = frozenset(
    {
        "contract_number_mismatch",
        "duplicate_invoice_id",
        "malformed_service_date",
        "service_date_out_of_window",
        "service_date_after_invoice_date",
        "line_total_arithmetic",
        "invoice_total_mismatch",
    }
)

#: Findings that depend on hospital-wide state: they are only as good as our
#: ability to see every other line that bears on them.
_STATEFUL = frozenset(
    {
        "daily_cap_exceeded",
        "premium_omitted",
        "premium_incorrectly_applied",
        "volume_discount_omitted",
        "volume_discount_incorrectly_applied",
        "bundle_not_applied",
        "bundle_incorrectly_applied",
        "exclusion_window_violation",
        "duplicate_service",
        "cross_invoice_duplicate",
    }
)

#: A textual match at or above this score rests on whole words rather than
#: reconstructed abbreviations.
STRONG_MATCH = 0.90


@dataclass
class ConfidenceAssessment:
    band: str
    value: float
    rationale: list[str]


def assess_confidence(
    result: AuditResult, lines: list[ResolvedLine]
) -> ConfidenceAssessment:
    rationale: list[str] = []

    statuses = [rl.match.status for rl in lines]
    has_unknown = MatchStatus.UNKNOWN in statuses
    has_ambiguous = MatchStatus.AMBIGUOUS in statuses
    used_basis = any(rl.match.used_unit_basis_for_matching for rl in lines)
    weakest = min(
        (rl.match.match_score for rl in lines if rl.match.status is MatchStatus.MATCHED),
        default=1.0,
    )
    categories = set(result.error_categories)

    # --- low ------------------------------------------------------------
    if has_unknown:
        rationale.append("an invoice line names no contracted service")
    if not result.pricing_complete:
        rationale.append("the corrected total could not be reconstructed")
    if "malformed_service_date" in categories:
        rationale.append(
            "a malformed service date removes a line from the cumulative sequence"
        )
    if rationale:
        return ConfidenceAssessment("low", BAND_VALUES["low"], rationale)

    # --- high -------------------------------------------------------------
    structural_only = bool(categories) and categories <= _STRUCTURAL
    if structural_only and not has_ambiguous:
        rationale.append(
            "every finding follows from the invoice's own fields, independently "
            "of service matching or pricing"
        )
        return ConfidenceAssessment("high", BAND_VALUES["high"], rationale)

    # --- medium -----------------------------------------------------------
    if has_ambiguous:
        rationale.append(
            "a description on this invoice is consistent with more than one "
            "contracted service"
        )
    if used_basis:
        rationale.append(
            "a service was identified by its billed unit basis after the text "
            "left a tie"
        )
    if weakest < STRONG_MATCH:
        rationale.append(
            f"the weakest service match on this invoice scores {weakest:.2f}, so it "
            f"rests on expanded abbreviations rather than whole words"
        )
    if categories & _STATEFUL:
        rationale.append(
            "a finding depends on hospital-wide state (daily aggregates, "
            "cumulative utilisation or billing history)"
        )
    if rationale:
        return ConfidenceAssessment("medium", BAND_VALUES["medium"], rationale)

    rationale.append(
        "every line matched a contracted service on whole words and priced "
        "exactly against the contract"
    )
    return ConfidenceAssessment("high", BAND_VALUES["high"], rationale)


# ==========================================================================
# 7. Pipeline
# ==========================================================================
#
# Wiring: contract + invoices -> audit results.
#
# Part of the prediction path, and covered by the freeze.  It knows nothing
# about labels, scoring or reporting: ``evaluation.py`` and ``main.py`` do
# that, and are deliberately *not* frozen, so report wording can be fixed
# without invalidating an unseen measurement.

class Pipeline:
    """Everything needed to go from files to audit results.  Label-free."""

    def __init__(self, options: Optional[PricingOptions] = None, **auditor_kwargs):
        self.rules = parse_contract(CONTRACT)
        self.matcher = ServiceMatcher(
            self.rules.services, {k: v.unit_basis for k, v in self.rules.services.items()}
        )
        self.engine = PricingEngine(self.rules, options)
        self.auditor = Auditor(self.rules, self.matcher, self.engine, **auditor_kwargs)
        self.occurrences = load_occurrences(JSONL)
        self.resolved = [
            ResolvedLine(occ, line, self.matcher.match(line.description, line.unit_basis_as_billed))
            for occ in self.occurrences
            for line in occ.line_items
        ]

    def lines_by_invoice(self) -> dict[str, list[ResolvedLine]]:
        out: dict[str, list[ResolvedLine]] = defaultdict(list)
        for rl in self.resolved:
            out[rl.occurrence.invoice_id].append(rl)
        return out

    def run(self, ctx: Optional[AuditContext] = None) -> list[AuditResult]:
        ctx = ctx or AuditContext(self.rules, self.resolved)
        results = self.auditor.audit_with_context(self.occurrences, ctx)
        self.attach_confidence(results)
        return results

    def attach_confidence(self, results: Iterable[AuditResult]) -> None:
        lines = self.lines_by_invoice()
        for result in results:
            assessment = assess_confidence(result, lines.get(result.invoice_id, []))
            result.confidence = assessment.value
            result.confidence_band = assessment.band
            result.uncertainty_reasons = list(
                dict.fromkeys(result.uncertainty_reasons + assessment.rationale)
            )
