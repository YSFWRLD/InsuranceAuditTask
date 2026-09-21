"""Hospital 1: the Provider Services Agreement as structured rules.

Everything the audit knows about the contract comes out of this module.  No
rate, cap, threshold, uplift, discount, bundle or exclusion window may be
written into the audit code itself; it is read here, from
``contracts/hospital_1/provider_services_agreement.md``, with the clause it
came from recorded on the rule.

The parser is *loud*.  A section that looks like a pricing table but does not
parse raises ``ContractParseError`` rather than quietly yielding an empty
rule family.  Silently reading "no rule" out of an unparsed rate table is the
single most dangerous failure mode available to an auditing engine.

Contents
    1. Rule types            the rule families of Sections 4-10
    2. Contract vocabulary   Section 4 unit-basis prose -> invoice tokens
    3. Parser                text -> ContractRules, with validation
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..shared.models import ServiceRule, UnitBasis
from ..shared.money import decimal_to_cents

#: The Hospital 1 agreement, relative to the repository root.
CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts" / "hospital_1" / "provider_services_agreement.md"
)


# ==========================================================================
# 1. Rule types
# ==========================================================================

@dataclass(frozen=True)
class ThresholdPremium:
    """Section 5: uplift once the *daily aggregate* quantity exceeds a bound."""

    service: str
    exceeds_units: int
    uplift_fraction: Decimal
    source_section: str = "Section 5"


@dataclass(frozen=True)
class NonBusinessDayUplift:
    """Section 6: uplift where the service date is a Saturday or Sunday."""

    service: str
    uplift_fraction: Decimal
    source_section: str = "Section 6"


@dataclass(frozen=True)
class VolumeDiscountTier:
    """Section 7: discount once cumulative utilisation exceeds a bound."""

    service: str
    exceeds_units: int
    discount_fraction: Decimal
    source_section: str = "Section 7"


@dataclass(frozen=True)
class DailyCap:
    """Section 8: maximum billable units per patient per service day."""

    service: str
    max_units: int
    source_section: str = "Section 8"


@dataclass(frozen=True)
class Bundle:
    """Section 9: paired services priced at substituted rates."""

    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int
    source_section: str = "Section 9"


@dataclass(frozen=True)
class ExclusionWindow:
    """Section 10: ``service`` is not billable within ``days`` of ``other``."""

    service: str
    days: int
    other_service: str
    source_section: str = "Section 10"


@dataclass
class ContractRules:
    """Everything the pricing engine is allowed to know about the contract."""

    contract_number: str
    provider: str
    payer: str
    effective_from: _dt.date
    effective_to: _dt.date
    currency: str
    rounding_convention: str
    facility_multipliers: dict[str, Decimal]
    plan_tier_multipliers: dict[str, Decimal]
    services: dict[str, ServiceRule]
    threshold_premiums: dict[str, ThresholdPremium]
    non_business_day_uplifts: dict[str, NonBusinessDayUplift]
    volume_discounts: dict[str, list[VolumeDiscountTier]]
    daily_caps: dict[str, DailyCap]
    bundles: list[Bundle]
    exclusion_windows: list[ExclusionWindow]
    warnings: list[str] = field(default_factory=list)

    # -- convenience lookups ------------------------------------------------

    def bundle_partner(self, service: str) -> Optional[tuple[str, int]]:
        """Return ``(partner_service, bundled_rate_for_this_service)``."""
        for b in self.bundles:
            if b.service_a == service:
                return b.service_b, b.rate_a_cents
            if b.service_b == service:
                return b.service_a, b.rate_b_cents
        return None

    def exclusions_for(self, service: str) -> list[ExclusionWindow]:
        """Windows in which ``service`` may not be billed.

        Section 10.1 makes the window symmetric in *time* ("either
        direction"), but the table itself is directional: the left column is
        the service that becomes unbillable.  Only the left column is returned.
        """
        return [e for e in self.exclusion_windows if e.service == service]


# ==========================================================================
# 2. Contract vocabulary
# ==========================================================================

CONTRACT_UNIT_BASIS_TEXT = {
    "per hour": UnitBasis.HOUR,
    "per day of service": UnitBasis.DAY,
    "per visit": UnitBasis.VISIT,
    "per test": UnitBasis.TEST,
    "per procedure": UnitBasis.PROCEDURE,
    "per night of occupancy": UnitBasis.NIGHT,
    "per unit dispensed": UnitBasis.UNIT_DISPENSED,
    "per item supplied": UnitBasis.ITEM,
}

#: The noun the contract uses for a unit on each basis.  Used only to read the
#: cap/threshold tables ("6 nights", "4 tests"), never to price anything.
UNIT_NOUNS = {
    "hour": UnitBasis.HOUR,
    "hours": UnitBasis.HOUR,
    "day": UnitBasis.DAY,
    "days": UnitBasis.DAY,
    "visit": UnitBasis.VISIT,
    "visits": UnitBasis.VISIT,
    "test": UnitBasis.TEST,
    "tests": UnitBasis.TEST,
    "procedure": UnitBasis.PROCEDURE,
    "procedures": UnitBasis.PROCEDURE,
    "night": UnitBasis.NIGHT,
    "nights": UnitBasis.NIGHT,
    "item": UnitBasis.ITEM,
    "items": UnitBasis.ITEM,
    "unit": UnitBasis.UNIT_DISPENSED,
    "units": UnitBasis.UNIT_DISPENSED,
}


# ==========================================================================
# 3. Parser
# ==========================================================================

class ContractParseError(Exception):
    """Raised when a section that should carry pricing rules cannot be read."""


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _parse_long_date(text: str) -> _dt.date:
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text.strip())
    if not m:
        raise ContractParseError(f"unparseable date: {text!r}")
    day, month_name, year = m.group(1), m.group(2).lower(), m.group(3)
    if month_name not in _MONTHS:
        raise ContractParseError(f"unknown month in date: {text!r}")
    return _dt.date(int(year), _MONTHS[month_name], int(day))


def _parse_gbp_to_cents(text: str) -> int:
    """'GBP 1,301.25' -> 130125.  Exact; never touches binary floating point.

    The format check (currency prefix, thousands separators, at most two
    decimal places) is this contract's typography; the conversion itself is
    ``shared.money``.
    """
    cleaned = text.strip().replace("GBP", "").replace(",", "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned):
        raise ContractParseError(f"unparseable money amount: {text!r}")
    try:
        return decimal_to_cents(cleaned)
    except ValueError as exc:
        raise ContractParseError(f"money amount is not a whole cent: {text!r}") from exc


def _parse_percent(text: str) -> Decimal:
    """'+20%' -> Decimal('0.20')."""
    m = re.fullmatch(r"\+?\s*(\d+(?:\.\d+)?)\s*%", text.strip())
    if not m:
        raise ContractParseError(f"unparseable percentage: {text!r}")
    return Decimal(m.group(1)) / Decimal(100)


def _parse_units(text: str) -> tuple[int, UnitBasis | None]:
    """'6 nights' -> (6, NIGHT).  '—' -> raises."""
    m = re.fullmatch(r"(\d+)\s*([A-Za-z]+)?", text.strip())
    if not m:
        raise ContractParseError(f"unparseable unit quantity: {text!r}")
    noun = (m.group(2) or "").lower()
    return int(m.group(1)), UNIT_NOUNS.get(noun)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _section_body(text: str, heading_re: str) -> str:
    """Return the text of the numbered section whose heading matches."""
    m = re.search(rf"^## {heading_re}\s*$", text, flags=re.M)
    if not m:
        raise ContractParseError(f"section not found: {heading_re}")
    start = m.end()
    nxt = re.search(r"^## ", text[start:], flags=re.M)
    return text[start: start + nxt.start()] if nxt else text[start:]


def _table_rows(body: str, section_label: str, expected_cols: int) -> list[list[str]]:
    """Extract the data rows of the single markdown table in ``body``.

    Raises if the section contains something table-shaped that will not parse,
    or if it contains no table at all.
    """
    rows: list[list[str]] = []
    seen_header = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = _split_row(line)
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            seen_header = True          # the |---|---| separator
            continue
        if not seen_header:
            continue                    # the header row itself
        if len(cells) != expected_cols:
            raise ContractParseError(
                f"{section_label}: row has {len(cells)} columns, expected "
                f"{expected_cols}: {line!r}"
            )
        rows.append(cells)
    if not rows:
        raise ContractParseError(f"{section_label}: no table rows parsed")
    return rows


# --------------------------------------------------------------------------

def parse_contract(path: str | Path) -> ContractRules:
    text = Path(path).read_text(encoding="utf-8")
    warnings: list[str] = []

    # -- metadata (front matter) -------------------------------------------
    def meta(label: str) -> str:
        m = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text, flags=re.M)
        if not m:
            raise ContractParseError(f"contract metadata missing: {label}")
        return m.group(1)

    contract_number = meta("Contract number")
    effective_from = _parse_long_date(meta("Effective from"))
    effective_to = _parse_long_date(meta("Effective to"))
    rounding = meta("Rounding convention")
    if rounding != "half_up_cent":
        # The engine implements ROUND_HALF_UP on cents.  Anything else is a
        # contract we cannot price correctly, so say so rather than guess.
        raise ContractParseError(f"unsupported rounding convention: {rounding!r}")

    # -- Section 1: facility / plan-tier differentials ----------------------
    s1 = _section_body(text, r"1\. Parties and Term")
    facility_multipliers: dict[str, Decimal] = {}
    plan_tier_multipliers: dict[str, Decimal] = {}
    if re.search(r"No facility differential applies", s1):
        facility_multipliers["*"] = Decimal(1)
    else:
        raise ContractParseError(
            "Section 1: facility differential clause not recognised; refusing "
            "to assume a multiplier of 1.0"
        )
    if re.search(r"reimbursed at the same rate", s1):
        plan_tier_multipliers["*"] = Decimal(1)
    else:
        raise ContractParseError(
            "Section 1: plan-tier clause not recognised; refusing to assume a "
            "multiplier of 1.0"
        )

    # -- Section 4: rate schedule ------------------------------------------
    s4 = _section_body(text, r"4\. Rate Schedule")
    services: dict[str, ServiceRule] = {}
    caps_from_s4: dict[str, int] = {}
    for name, basis_text, rate_text, cap_text in _table_rows(s4, "Section 4", 4):
        basis = CONTRACT_UNIT_BASIS_TEXT.get(basis_text.lower())
        if basis is None:
            raise ContractParseError(
                f"Section 4: unknown unit basis {basis_text!r} for {name!r}"
            )
        if name in services:
            raise ContractParseError(f"Section 4: duplicate service {name!r}")
        cap_units = None
        if cap_text not in {"—", "-", "", "–"}:
            cap_units, cap_basis = _parse_units(cap_text)
            if cap_basis is not None and cap_basis is not basis:
                warnings.append(
                    f"Section 4: cap for {name!r} is stated in {cap_text!r} but "
                    f"the service is billed {basis_text!r}"
                )
            caps_from_s4[name] = cap_units
        services[name] = ServiceRule(
            name=name,
            unit_basis=basis,
            base_rate_cents=_parse_gbp_to_cents(rate_text),
            source_section="Section 4",
            daily_cap_units=cap_units,
        )

    # -- Section 5: threshold premiums -------------------------------------
    s5 = _section_body(text, r"5\. Threshold Premiums")
    threshold_premiums: dict[str, ThresholdPremium] = {}
    for name, when_text, uplift_text in _table_rows(s5, "Section 5", 3):
        if name not in services:
            raise ContractParseError(f"Section 5: unknown service {name!r}")
        if name in threshold_premiums:
            raise ContractParseError(f"Section 5: duplicate service {name!r}")
        qty, _ = _parse_units(when_text)
        threshold_premiums[name] = ThresholdPremium(
            service=name, exceeds_units=qty, uplift_fraction=_parse_percent(uplift_text)
        )

    # -- Section 6: non-business-day uplifts -------------------------------
    s6 = _section_body(text, r"6\. Non-Business-Day Uplifts")
    nbd_uplifts: dict[str, NonBusinessDayUplift] = {}
    for name, uplift_text in _table_rows(s6, "Section 6", 2):
        if name not in services:
            raise ContractParseError(f"Section 6: unknown service {name!r}")
        nbd_uplifts[name] = NonBusinessDayUplift(
            service=name, uplift_fraction=_parse_percent(uplift_text)
        )

    # -- Section 7: cumulative volume discounts ----------------------------
    s7 = _section_body(text, r"7\. Cumulative Volume Discounts")
    volume_discounts: dict[str, list[VolumeDiscountTier]] = {}
    for name, when_text, disc_text in _table_rows(s7, "Section 7", 3):
        if name not in services:
            raise ContractParseError(f"Section 7: unknown service {name!r}")
        qty, _ = _parse_units(when_text)
        volume_discounts.setdefault(name, []).append(
            VolumeDiscountTier(
                service=name,
                exceeds_units=qty,
                discount_fraction=_parse_percent(disc_text),
            )
        )
    for name, tiers in volume_discounts.items():
        tiers.sort(key=lambda t: t.exceeds_units)
        # 7.2: "where two thresholds are met, the deeper discount applies".
        # A schedule whose deeper threshold is not also the deeper discount
        # would make that clause self-contradictory; refuse to guess.
        for lo, hi in zip(tiers, tiers[1:]):
            if hi.discount_fraction <= lo.discount_fraction:
                raise ContractParseError(
                    f"Section 7: {name!r} tiers are not monotonically deeper"
                )

    # -- Section 8: daily caps ---------------------------------------------
    s8 = _section_body(text, r"8\. Daily Quantity Caps")
    daily_caps: dict[str, DailyCap] = {}
    for name, cap_text in _table_rows(s8, "Section 8", 2):
        if name not in services:
            raise ContractParseError(f"Section 8: unknown service {name!r}")
        qty, _ = _parse_units(cap_text)
        daily_caps[name] = DailyCap(service=name, max_units=qty)

    # Cross-check Section 4's "Daily cap" column against Section 8.  The two
    # tables restate the same rule; a disagreement means one of them was
    # misread and we must not pick a winner silently.
    if set(caps_from_s4) != set(daily_caps):
        raise ContractParseError(
            "Sections 4 and 8 disagree about which services are capped: "
            f"{sorted(set(caps_from_s4) ^ set(daily_caps))}"
        )
    for name, qty in caps_from_s4.items():
        if daily_caps[name].max_units != qty:
            raise ContractParseError(
                f"Sections 4 and 8 disagree about the cap for {name!r}: "
                f"{qty} vs {daily_caps[name].max_units}"
            )

    # -- Section 9: bundles -------------------------------------------------
    s9 = _section_body(text, r"9\. Bundled Services")
    bundles: list[Bundle] = []
    bundled_services: set[str] = set()
    for a, b, rate_a, rate_b in _table_rows(s9, "Section 9", 4):
        for name in (a, b):
            if name not in services:
                raise ContractParseError(f"Section 9: unknown service {name!r}")
            if name in bundled_services:
                # The engine substitutes at most one bundled rate per line.  A
                # service appearing in two pairs would make that ambiguous.
                raise ContractParseError(
                    f"Section 9: {name!r} appears in more than one bundle"
                )
            bundled_services.add(name)
        bundles.append(
            Bundle(
                service_a=a,
                service_b=b,
                rate_a_cents=_parse_gbp_to_cents(rate_a),
                rate_b_cents=_parse_gbp_to_cents(rate_b),
            )
        )

    # -- Section 10: exclusion windows -------------------------------------
    s10 = _section_body(text, r"10\. Exclusion Windows")
    exclusions: list[ExclusionWindow] = []
    for name, within_text, other in _table_rows(s10, "Section 10", 3):
        for n in (name, other):
            if n not in services:
                raise ContractParseError(f"Section 10: unknown service {n!r}")
        m = re.fullmatch(r"(\d+)\s*days?", within_text.strip(), flags=re.I)
        if not m:
            raise ContractParseError(
                f"Section 10: unparseable window {within_text!r} for {name!r}"
            )
        exclusions.append(
            ExclusionWindow(service=name, days=int(m.group(1)), other_service=other)
        )

    rules = ContractRules(
        contract_number=contract_number,
        provider=meta("Provider"),
        payer=meta("Payer"),
        effective_from=effective_from,
        effective_to=effective_to,
        currency=meta("Currency"),
        rounding_convention=rounding,
        facility_multipliers=facility_multipliers,
        plan_tier_multipliers=plan_tier_multipliers,
        services=services,
        threshold_premiums=threshold_premiums,
        non_business_day_uplifts=nbd_uplifts,
        volume_discounts=volume_discounts,
        daily_caps=daily_caps,
        bundles=bundles,
        exclusion_windows=exclusions,
        warnings=warnings,
    )
    _validate_rule_families(rules, text)
    return rules


def _validate_rule_families(rules: ContractRules, text: str) -> None:
    """Sanity checks on the parsed rule set.

    The point is to catch a *silently truncated* parse: a regex that stops
    early still produces a plausible-looking ContractRules object.
    """
    n_pipe_rows = len(
        [l for l in _section_body(text, r"4\. Rate Schedule").splitlines()
         if l.strip().startswith("|")]
    )
    # header + separator + N data rows
    if len(rules.services) != n_pipe_rows - 2:
        raise ContractParseError(
            f"Section 4: parsed {len(rules.services)} services from "
            f"{n_pipe_rows - 2} table rows"
        )

    if not rules.services:
        raise ContractParseError("no services parsed")
    for family, coll in (
        ("threshold premiums", rules.threshold_premiums),
        ("non-business-day uplifts", rules.non_business_day_uplifts),
        ("volume discounts", rules.volume_discounts),
        ("daily caps", rules.daily_caps),
    ):
        if not coll:
            raise ContractParseError(f"{family}: section present but nothing parsed")
    if not rules.bundles:
        raise ContractParseError("bundles: section present but nothing parsed")
    if not rules.exclusion_windows:
        raise ContractParseError("exclusion windows: section present but nothing parsed")

    if rules.effective_from > rules.effective_to:
        raise ContractParseError("contract term runs backwards")

    # Every rule family must reference only known services -- already enforced
    # row by row above, restated here as a single invariant.
    referenced = (
        set(rules.threshold_premiums)
        | set(rules.non_business_day_uplifts)
        | set(rules.volume_discounts)
        | set(rules.daily_caps)
        | {s for b in rules.bundles for s in (b.service_a, b.service_b)}
        | {s for e in rules.exclusion_windows for s in (e.service, e.other_service)}
    )
    unknown = referenced - set(rules.services)
    if unknown:
        raise ContractParseError(f"rules reference unknown services: {sorted(unknown)}")

    # Sections 5 and 6 are both "stage (d)" adjustments in clause 3.2 and the
    # contract never says how they would combine.  If a future contract puts a
    # service in both lists, that ambiguity must surface rather than be
    # resolved by whichever branch the code happens to reach first.
    both = set(rules.threshold_premiums) & set(rules.non_business_day_uplifts)
    if both:
        rules.warnings.append(
            "services carry both a threshold premium and a non-business-day "
            f"uplift; clause 3.2 does not define their interaction: {sorted(both)}"
        )
