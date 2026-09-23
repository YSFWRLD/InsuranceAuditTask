"""Hospital 4: the Conditional Reimbursement Agreement as structured rules.

Everything the Hospital 4 audit knows about the contract comes out of this
module, read from ``contracts/hospital_4/conditional_reimbursement_agreement.md``
with the section and table row each rule came from.  No rate, cap, threshold,
uplift, discount, bundle or exclusion window is written into the audit code.

The agreement is table-driven (Sections 3 and 5-10), with the pricing
conventions in prose (Sections 2, 4 and 11).  The parser is loud: a section
that should carry rules but does not parse raises ``ContractParseError``, and
so does any prose clause whose wording is not the wording the engine
implements (the pricing order, the rounding rule, the facility and plan-tier
statement).  Reading "no rule" out of an unparsed section is the most
dangerous failure available to an auditing engine.

Contents
    1. Rule types
    2. Vocabulary        unit-basis prose -> invoice tokens; numbers in words
    3. Parser            text -> H4Contract, with cross-validation
    4. Serialisation     contract_rules.json, with a source fingerprint

Nothing here reads an invoice or a label.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..shared.money import decimal_to_cents

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "contracts" / "hospital_4" / "conditional_reimbursement_agreement.md"

#: Bumped whenever the parser's reading of the contract could change.  Part of
#: the fingerprint written to every Hospital 4 artifact.
PARSER_VERSION = "h4-contract-1"


class ContractParseError(Exception):
    """A section or clause that should carry a rule could not be read completely."""


# ==========================================================================
# 1. Rule types
# ==========================================================================

@dataclass(frozen=True)
class H4Service:
    name: str
    #: The three name slots.  Every Hospital 4 service name is
    #: <qualifier> <specialty> <service concept>; the parser verifies that the
    #: qualifier and specialty vocabularies are disjoint before the matcher
    #: relies on that.
    qualifier: str
    specialty: str
    concept: str
    base_rate_cents: int
    #: The billed-token form of the contractual unit basis ("per_night").
    unit_basis: str
    #: The contract's own words ("per night of occupancy").
    unit_basis_text: str
    #: "per hour, per item" is a basis of its own, not collapsed into either part.
    compound_unit_basis: bool
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class ThresholdPremium:
    """Section 5: uplift once the patient's Service Day aggregate exceeds a bound."""

    service: str
    exceeds_units: int
    uplift_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class DailyCap:
    """Section 6: the quantity in excess of the limit is not payable (6.1)."""

    service: str
    max_units: int
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class Bundle:
    """Section 7: both services on the same patient's Service Day -> substituted rates."""

    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class VolumeDiscountTier:
    """Section 8.3: discount once cumulative utilisation *prior to* a line exceeds a bound."""

    service: str
    exceeds_units: int
    discount_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class ExclusionWindow:
    """Section 9: ``service`` is not billable within ``days`` of ``other_service``."""

    service: str
    days: int
    other_service: str
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class NonBusinessDayUplift:
    """Section 10.  The agreement lists none; the type exists so that an
    uplift appearing in a future version is parsed, not ignored."""

    service: str
    uplift_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass
class H4Contract:
    contract_number: str
    provider: str
    payer: str
    effective_from: str
    effective_to: str
    currency: str
    rounding_convention: str
    facility_code: str
    facility_multiplier: Decimal
    plan_tier_multiplier: Decimal
    #: Section 4.1 stage order, as parsed: bundle, facility, plan, premium, discount.
    pricing_order: tuple[str, ...]
    services: dict[str, H4Service]
    threshold_premiums: dict[str, ThresholdPremium]
    daily_caps: dict[str, DailyCap]
    bundles: list[Bundle]
    volume_discounts: dict[str, list[VolumeDiscountTier]]
    exclusion_windows: list[ExclusionWindow]
    non_business_day_uplifts: dict[str, NonBusinessDayUplift]
    #: Section 11, clause id -> the clause text.
    invoice_clauses: dict[str, str]
    #: Prose conventions the engine implements, clause id -> text.
    conventions: dict[str, str]
    fingerprint: str
    source_sha256: str
    warnings: list[str] = field(default_factory=list)

    def bundle_partner(self, service: str) -> Optional[tuple[str, int, str]]:
        """``(partner, this service's substituted rate, clause id)``."""
        for b in self.bundles:
            if b.service_a == service:
                return b.service_b, b.rate_a_cents, b.clause_id
            if b.service_b == service:
                return b.service_a, b.rate_b_cents, b.clause_id
        return None

    def exclusions_for(self, service: str) -> list[ExclusionWindow]:
        """Windows in which ``service`` (the left-hand column) is not billable."""
        return [e for e in self.exclusion_windows if e.service == service]

    def rules_touching(self, service: str) -> list[str]:
        """Which rule families involve ``service`` -- used to rank unresolved lines."""
        out = []
        if self.bundle_partner(service):
            out.append("bundle")
        if service in self.threshold_premiums:
            out.append("threshold_premium")
        if service in self.daily_caps:
            out.append("daily_cap")
        if service in self.volume_discounts:
            out.append("cumulative_discount")
        if any(service in (e.service, e.other_service) for e in self.exclusion_windows):
            out.append("exclusion")
        return out


# ==========================================================================
# 2. Vocabulary
# ==========================================================================

#: Contract prose -> the unit-basis token invoices use.
UNIT_BASIS_TEXT = {
    "per hour": "per_hour",
    "per day of service": "per_day",
    "per night of occupancy": "per_night",
    "per visit": "per_visit",
    "per test": "per_test",
    "per procedure": "per_procedure",
    "per item supplied": "per_item",
    "per unit dispensed": "per_unit_dispensed",
    "per hour, per item": "per_hour_per_item",
}
COMPOUND_BASES = {"per_hour_per_item"}

#: The noun a cap or threshold uses for a unit on each basis ("12 nights").
#: Used only to cross-check the tables against Section 3, never to price.
UNIT_NOUNS = {
    "hours": "per_hour", "hour": "per_hour",
    "days": "per_day", "day": "per_day",
    "nights": "per_night", "night": "per_night",
    "visits": "per_visit", "visit": "per_visit",
    "tests": "per_test", "test": "per_test",
    "procedures": "per_procedure", "procedure": "per_procedure",
    "items": "per_item", "item": "per_item",
    "units": "per_unit_dispensed", "unit": "per_unit_dispensed",
}

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}


def words_to_int(words: str) -> int:
    """'two hundred and forty' -> 240.  Raises on anything it cannot read."""
    total = current = 0
    tokens = re.split(r"[\s-]+", words.strip().lower())
    if not tokens or tokens == [""]:
        raise ContractParseError(f"empty number in words: {words!r}")
    for tok in tokens:
        if tok == "and":
            continue
        if tok in _ONES:
            current += _ONES[tok]
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok == "hundred":
            current = (current or 1) * 100
        elif tok == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            raise ContractParseError(f"unreadable number word {tok!r} in {words!r}")
    return total + current


# ==========================================================================
# 3. Parser
# ==========================================================================

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

#: Clause 4.1, in the engine's stage names.
EXPECTED_PRICING_ORDER = ("bundle", "facility", "plan_tier", "premium", "cumulative_discount")
_ORDER_PHRASES = {
    "substitution of a bundled rate": "bundle",
    "the facility multiplier": "facility",
    "the plan-tier multiplier": "plan_tier",
    "any premium or uplift": "premium",
    "any cumulative volume discount": "cumulative_discount",
}


def _long_date(text: str) -> str:
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text.strip())
    if not m or m.group(2).lower() not in _MONTHS:
        raise ContractParseError(f"unparseable date: {text!r}")
    return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _gbp(text: str, context: str) -> int:
    """'GBP 3,217.75' -> 321775, exactly; never touches binary floating point."""
    m = re.fullmatch(r"GBP\s+(\d{1,3}(?:,\d{3})*|\d+)(\.\d{1,2})?", text.strip())
    if not m:
        raise ContractParseError(f"{context}: unparseable money amount {text!r}")
    try:
        return decimal_to_cents(m.group(1).replace(",", "") + (m.group(2) or ""))
    except ValueError as exc:
        raise ContractParseError(f"{context}: {exc}") from exc


def _uplift(text: str, context: str) -> Decimal:
    """'+25%' -> Decimal('0.25')."""
    m = re.fullmatch(r"\+\s*(\d+(?:\.\d+)?)\s*%", text.strip())
    if not m:
        raise ContractParseError(f"{context}: unparseable percentage {text!r}")
    return Decimal(m.group(1)) / 100


def _words_and_digits(text: str, context: str, *, percent: bool) -> Decimal | int:
    """'eighty (80)' -> 80; 'fifteen percent (15%)' -> Decimal('0.15').

    The number is stated twice; the two statements must agree.
    """
    if percent:
        m = re.fullmatch(r"([a-z\s-]+?)\s+percent\s*\((\d+)%\)", text.strip(), flags=re.I)
    else:
        m = re.fullmatch(r"([a-z\s-]+?)\s*\((\d+)\)", text.strip(), flags=re.I)
    if not m:
        raise ContractParseError(f"{context}: unparseable {'percentage' if percent else 'number'} {text!r}")
    in_words, in_digits = words_to_int(m.group(1)), int(m.group(2))
    if in_words != in_digits:
        raise ContractParseError(f"{context}: {text!r} states {in_words} in words but {in_digits} in digits")
    return Decimal(in_digits) / 100 if percent else in_digits


def _quantity(text: str, context: str, *, prefix: str = "") -> tuple[int, str]:
    """'more than 10 procedures' -> (10, 'per_procedure'); '12 nights' -> (12, 'per_night')."""
    m = re.fullmatch(rf"{prefix}(\d+)\s+([a-z]+)", text.strip(), flags=re.I)
    if not m:
        raise ContractParseError(f"{context}: unparseable quantity {text!r}")
    noun = m.group(2).lower()
    if noun not in UNIT_NOUNS:
        raise ContractParseError(f"{context}: unknown unit noun {noun!r} in {text!r}")
    return int(m.group(1)), UNIT_NOUNS[noun]


def _meta(text: str, label: str) -> str:
    m = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text, flags=re.M)
    if not m:
        raise ContractParseError(f"contract metadata missing: {label}")
    return m.group(1)


def _section(text: str, number: int, title: str) -> str:
    m = re.search(rf"^## {number}\. {re.escape(title)}\s*$", text, flags=re.M)
    if not m:
        raise ContractParseError(f"section not found: {number}. {title}")
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, flags=re.M)
    return rest[: nxt.start()] if nxt else rest


def _clause(body: str, clause_id: str) -> str:
    m = re.search(rf"^{re.escape(clause_id)}\s+(.+?)\s*$", body, flags=re.M)
    if not m:
        raise ContractParseError(f"clause {clause_id} not found")
    return m.group(1)


def _table(body: str, context: str, columns: int) -> list[tuple[list[str], str]]:
    """Data rows of the one markdown table in ``body``, with each row's source line.

    Raises if a row has the wrong number of cells, or if there is no table.
    """
    rows: list[tuple[list[str], str]] = []
    seen_separator = False
    pipe_lines = 0
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        pipe_lines += 1
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            seen_separator = True
            continue
        if not seen_separator:
            continue  # header row
        if len(cells) != columns:
            raise ContractParseError(f"{context}: row has {len(cells)} cells, expected {columns}: {line!r}")
        rows.append((cells, line))
    if not rows:
        raise ContractParseError(f"{context}: no table rows parsed")
    if len(rows) != pipe_lines - 2:
        raise ContractParseError(f"{context}: parsed {len(rows)} rows from {pipe_lines - 2} table lines")
    return rows


def _require(text: str, pattern: str, what: str) -> re.Match:
    m = re.search(pattern, text)
    if not m:
        raise ContractParseError(f"{what}: expected wording not found; refusing to assume it")
    return m


def parse_contract(path: Path = CONTRACT_PATH) -> H4Contract:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8")
    warnings: list[str] = []

    # -- front matter -------------------------------------------------------
    rounding = _meta(text, "Rounding convention")
    if rounding != "half_up_cent":
        raise ContractParseError(f"unsupported rounding convention: {rounding!r}")
    effective_from = _long_date(_meta(text, "Effective from"))
    effective_to = _long_date(_meta(text, "Effective to"))
    if effective_from > effective_to:
        raise ContractParseError("contract term runs backwards")

    # -- Section 2: term, facility, plan tier ------------------------------
    s2 = _section(text, 2, "Term and Scope")
    c21 = _clause(s2, "2.1")
    term = _require(c21, r"runs from (\d{1,2} [A-Za-z]+ \d{4}) to (\d{1,2} [A-Za-z]+ \d{4})", "clause 2.1")
    if (_long_date(term.group(1)), _long_date(term.group(2))) != (effective_from, effective_to):
        raise ContractParseError("clause 2.1 term disagrees with the front matter")
    c22 = _clause(s2, "2.2")
    facility = _require(c22, r"delivered from [^(]*\(([A-Z0-9-]+)\)", "clause 2.2 facility").group(1)
    _require(c22, r"no facility differential arises", "clause 2.2 facility multiplier")
    _require(c22, r"plan tier does not affect the rate", "clause 2.2 plan-tier multiplier")

    # -- Section 4: order and rounding -------------------------------------
    s4 = _section(text, 4, "Order of Adjustments")
    c41 = _clause(s4, "4.1")
    stages = re.findall(r"\(([a-e])\)\s*([^;.]+?)(?:;|\.)", c41)
    order = tuple(_ORDER_PHRASES.get(re.sub(r"^and\s+", "", s.strip()), "?") for _, s in stages)
    if order != EXPECTED_PRICING_ORDER:
        raise ContractParseError(f"clause 4.1 pricing order not recognised: {stages!r}")
    c42 = _clause(s4, "4.2")
    _require(c42, r"rounded to the nearest whole cent, with exact halves rounded away from zero", "clause 4.2 rounding")
    _require(c42, r"Rounding is applied after each individual step of the calculation, not once at the end",
             "clause 4.2 rounding frequency")
    c43 = _clause(s4, "4.3")
    _require(c43, r"only the deeper discount is applied", "clause 4.3 deeper discount")
    c44 = _clause(s4, "4.4")
    _require(c44, r"The invoice total is the sum of the line totals on the invoice", "clause 4.4 invoice total")

    # -- Section 3: base rates ----------------------------------------------
    s3 = _section(text, 3, "Base Rates")
    services: dict[str, H4Service] = {}
    for (name, basis_text, rate_text), line in _table(s3, "Section 3", 3):
        if name in services:
            raise ContractParseError(f"Section 3: duplicate service {name!r}")
        if not basis_text:
            raise ContractParseError(f"Section 3: missing unit basis for {name!r}")
        basis = UNIT_BASIS_TEXT.get(basis_text.lower())
        if basis is None:
            raise ContractParseError(f"Section 3: unknown unit basis {basis_text!r} for {name!r}")
        words = name.split()
        if len(words) < 3:
            raise ContractParseError(f"Section 3: {name!r} is not <qualifier> <specialty> <concept>")
        services[name] = H4Service(
            name=name, qualifier=words[0].lower(), specialty=words[1].lower(),
            concept=" ".join(words[2:]).lower(), base_rate_cents=_gbp(rate_text, f"Section 3 {name!r}"),
            unit_basis=basis, unit_basis_text=basis_text, compound_unit_basis=basis in COMPOUND_BASES,
            clause_id="3.1", source_text=line,
        )
    qualifiers = {s.qualifier for s in services.values()}
    specialties = {s.specialty for s in services.values()}
    concept_words = {w for s in services.values() for w in s.concept.split()}
    for a, b, label in ((qualifiers, specialties, "qualifier/specialty"),
                        (qualifiers, concept_words, "qualifier/concept"),
                        (specialties, concept_words, "specialty/concept")):
        if a & b:
            raise ContractParseError(f"service-name vocabularies overlap ({label}): {sorted(a & b)}")

    def known(name: str, context: str) -> str:
        if name not in services:
            raise ContractParseError(f"{context}: unknown service {name!r}")
        return name

    def check_noun(noun_basis: str, service: str, context: str) -> None:
        if noun_basis != services[service].unit_basis:
            raise ContractParseError(
                f"{context}: quantity for {service!r} is stated in the wrong unit "
                f"({noun_basis} vs {services[service].unit_basis})")

    # -- Section 5: threshold premiums --------------------------------------
    s5 = _section(text, 5, "Threshold Premiums")
    _require(_clause(s5, "5.1"), r"assessed against the aggregate for the Service Day", "clause 5.1 aggregate")
    premiums: dict[str, ThresholdPremium] = {}
    for (name, when, uplift), line in _table(s5, "Section 5", 3):
        known(name, "Section 5")
        if name in premiums:
            raise ContractParseError(f"Section 5: duplicate service {name!r}")
        qty, noun = _quantity(when, f"Section 5 {name!r}", prefix=r"more than\s+")
        check_noun(noun, name, "Section 5")
        premiums[name] = ThresholdPremium(name, qty, _uplift(uplift, f"Section 5 {name!r}"), "5.1", line)

    # -- Section 6: daily caps ----------------------------------------------
    s6 = _section(text, 6, "Daily Quantity Limits")
    _require(_clause(s6, "6.1"), r"A quantity in excess of the limit is not payable", "clause 6.1 excess")
    _require(_clause(s6, "6.2"), r"irrespective of the number of line items or invoices", "clause 6.2 aggregation")
    caps: dict[str, DailyCap] = {}
    for (name, limit), line in _table(s6, "Section 6", 2):
        known(name, "Section 6")
        if name in caps:
            raise ContractParseError(f"Section 6: duplicate service {name!r}")
        qty, noun = _quantity(limit, f"Section 6 {name!r}")
        check_noun(noun, name, "Section 6")
        caps[name] = DailyCap(name, qty, "6.1", line)

    # -- Section 7: bundles -------------------------------------------------
    s7 = _section(text, 7, "Bundled Delivery")
    _require(_clause(s7, "7.1"), r"same Patient on the same Service Day", "clause 7.1 scope")
    bundles: list[Bundle] = []
    bundled: set[str] = set()
    for (a, rate_a, b, rate_b), line in _table(s7, "Section 7", 4):
        for n in (known(a, "Section 7"), known(b, "Section 7")):
            if n in bundled:
                # The engine substitutes at most one bundled rate per line.
                raise ContractParseError(f"Section 7: {n!r} appears in more than one bundle")
            bundled.add(n)
        bundles.append(Bundle(a, b, _gbp(rate_a, f"Section 7 {a!r}"), _gbp(rate_b, f"Section 7 {b!r}"), "7.1", line))

    # -- Section 8: cumulative volume discounts -----------------------------
    s8 = _section(text, 8, "Discounts")
    _require(_clause(s8, "8.4"), r"cumulative utilisation prior to that line item exceeds the threshold",
             "clause 8.4 prior-exclusive")
    _require(_clause(s8, "8.5"), r"counted in Service Date order, and where two line items share a Service Date "
             r"they are counted in ascending order of line identifier", "clause 8.5 ordering")
    discounts: dict[str, list[VolumeDiscountTier]] = {}
    for (name, exceeds, pct), line in _table(s8, "Section 8", 3):
        known(name, "Section 8")
        discounts.setdefault(name, []).append(VolumeDiscountTier(
            name, _words_and_digits(exceeds, f"Section 8 {name!r}", percent=False),
            _words_and_digits(pct, f"Section 8 {name!r}", percent=True), "8.3", line))
    for name, tiers in discounts.items():
        tiers.sort(key=lambda t: t.exceeds_units)
        if len({t.exceeds_units for t in tiers}) != len(tiers):
            raise ContractParseError(f"Section 8: {name!r} repeats a threshold")
        for lo, hi in zip(tiers, tiers[1:]):
            if hi.discount_fraction <= lo.discount_fraction:
                # 8.3 / 4.3: "the deeper discount applies once its threshold has
                # been exceeded".  A later tier that is not deeper would make
                # that sentence self-contradictory; refuse to guess.
                raise ContractParseError(f"Section 8: {name!r} tiers are not monotonically deeper")

    # -- Section 9: exclusion windows ---------------------------------------
    s9 = _section(text, 9, "Exclusion Windows")
    c91 = _clause(s9, "9.1")
    _require(c91, r"same Patient", "clause 9.1 patient scope")
    _require(c91, r"measured in either direction", "clause 9.1 direction")
    exclusions: list[ExclusionWindow] = []
    for (name, window, other), line in _table(s9, "Section 9", 3):
        known(name, "Section 9")
        known(other, "Section 9")
        m = re.fullmatch(r"(\d+)\s+days?", window.strip())
        if not m:
            raise ContractParseError(f"Section 9: unparseable window {window!r} for {name!r}")
        exclusions.append(ExclusionWindow(name, int(m.group(1)), other, "9.1", line))

    # -- Section 10: non-business-day uplifts -------------------------------
    s10 = _section(text, 10, "Non-Business-Day Uplifts")
    nbd: dict[str, NonBusinessDayUplift] = {}
    if re.search(r"^_None\._\s*$", s10, flags=re.M):
        if "|" in s10:
            raise ContractParseError("Section 10: says 'None' but also carries a table")
    else:
        for (name, uplift), line in _table(s10, "Section 10", 2):
            nbd[known(name, "Section 10")] = NonBusinessDayUplift(
                name, _uplift(uplift, f"Section 10 {name!r}"), "10.1", line)

    # -- Section 11: invoicing ----------------------------------------------
    s11 = _section(text, 11, "Invoicing")
    invoice_clauses = {cid: _clause(s11, cid) for cid in ("11.1", "11.2", "11.3")}
    _require(invoice_clauses["11.1"], r"quotes the contract number", "clause 11.1 contract number")
    _require(invoice_clauses["11.1"], r"invoice number unique across the term", "clause 11.1 unique number")
    _require(invoice_clauses["11.2"], r"within the term .* and may not fall after the invoice date", "clause 11.2")
    _require(invoice_clauses["11.3"], r"same Service may not be billed twice for the same Patient and the "
             r"same Service Date, whether on one invoice or across several", "clause 11.3")

    source_sha = hashlib.sha256(raw).hexdigest()
    contract = H4Contract(
        contract_number=_meta(text, "Contract number"),
        provider=_meta(text, "Provider"),
        payer=_meta(text, "Payer"),
        effective_from=effective_from,
        effective_to=effective_to,
        currency=_meta(text, "Currency"),
        rounding_convention=rounding,
        facility_code=facility,
        facility_multiplier=Decimal(1),
        plan_tier_multiplier=Decimal(1),
        pricing_order=order,
        services=services,
        threshold_premiums=premiums,
        daily_caps=caps,
        bundles=bundles,
        volume_discounts=discounts,
        exclusion_windows=exclusions,
        non_business_day_uplifts=nbd,
        invoice_clauses=invoice_clauses,
        conventions={
            "1.1": _clause(_section(text, 1, "Definitions"), "1.1"),
            "2.2": c22, "4.1": c41, "4.2": c42, "4.3": c43, "4.4": c44,
            "5.1": _clause(s5, "5.1"), "5.3": _clause(s5, "5.3"),
            "6.1": _clause(s6, "6.1"), "6.2": _clause(s6, "6.2"),
            "7.1": _clause(s7, "7.1"), "7.2": _clause(s7, "7.2"),
            "8.3": _clause(s8, "8.3"), "8.4": _clause(s8, "8.4"), "8.5": _clause(s8, "8.5"),
            "9.1": c91,
        },
        fingerprint=hashlib.sha256(f"{PARSER_VERSION}:{source_sha}".encode()).hexdigest(),
        source_sha256=source_sha,
        warnings=warnings,
    )
    _validate(contract)
    return contract


def _validate(c: H4Contract) -> None:
    """Invariants over the parsed rule set, to catch a silently truncated parse."""
    for family, coll in (("threshold premiums", c.threshold_premiums), ("daily caps", c.daily_caps),
                         ("bundles", c.bundles), ("cumulative discounts", c.volume_discounts),
                         ("exclusion windows", c.exclusion_windows)):
        if not coll:
            raise ContractParseError(f"{family}: section present but nothing parsed")
    referenced = (
        set(c.threshold_premiums) | set(c.daily_caps) | set(c.volume_discounts)
        | set(c.non_business_day_uplifts)
        | {s for b in c.bundles for s in (b.service_a, b.service_b)}
        | {s for e in c.exclusion_windows for s in (e.service, e.other_service)}
    )
    unknown = referenced - set(c.services)
    if unknown:
        raise ContractParseError(f"rules reference unknown services: {sorted(unknown)}")
    both = set(c.threshold_premiums) & set(c.non_business_day_uplifts)
    if both:
        # Both are stage (d) in clause 4.1 and the agreement never says how they
        # combine.  Surface it rather than let code order decide.
        c.warnings.append(f"services carry both a threshold premium and a non-business-day uplift: {sorted(both)}")


# ==========================================================================
# 4. Serialisation
# ==========================================================================

def _jsonable(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return _jsonable(asdict(obj))
    return obj


def rule_counts(c: H4Contract) -> dict[str, int]:
    return {
        "services": len(c.services),
        "threshold_premiums": len(c.threshold_premiums),
        "daily_caps": len(c.daily_caps),
        "bundle_pairs": len(c.bundles),
        "cumulative_discount_services": len(c.volume_discounts),
        "cumulative_discount_tiers": sum(len(v) for v in c.volume_discounts.values()),
        "exclusion_windows": len(c.exclusion_windows),
        "non_business_day_uplifts": len(c.non_business_day_uplifts),
        "compound_unit_bases": sum(s.compound_unit_basis for s in c.services.values()),
    }


def write_contract_rules(c: H4Contract, path: Path) -> None:
    doc = {
        "fingerprint": c.fingerprint,
        "source": str(CONTRACT_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "source_sha256": c.source_sha256,
        "parser_version": PARSER_VERSION,
        "counts": rule_counts(c),
        **{k: _jsonable(v) for k, v in c.__dict__.items() if k not in ("fingerprint", "source_sha256")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
