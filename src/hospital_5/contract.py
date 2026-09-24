"""Hospital 5: the Network Reimbursement Agreement as structured rules.

Everything the Hospital 5 audit knows about the contract comes out of this
module, read from ``contracts/hospital_5/network_reimbursement_agreement.md``
with the section and table row each rule came from.  No rate, multiplier,
cap, threshold, uplift, discount, bundle or exclusion window is written into
the audit code.

Hospital 5 differs from Hospital 4 in ways that matter to pricing, so the
parser does not reuse Hospital 4's reading of anything:

* rates vary by **facility** (Table 2) and **plan tier** (Table 3), per service;
* Table 1 carries a **Daily cap** column but no clause says what a cap does
  (Hospital 4's clause 6.1 "the excess is not payable" has no counterpart);
* Section 6 lists **non-business-day uplifts**, and Section 2.2 defines a
  business day;
* Section 8's discount column is headed "Discount on **subsequent units**";
* Section 9 says "not billable within N days of" without Hospital 4's
  "measured in either direction".

The parser is loud.  A table whose header is not the header the engine
implements, a row with the wrong number of cells, a service a rule names that
Table 1 does not carry, a Table 1 service missing from Table 2 or Table 3, or
a prose clause whose wording is not the wording implemented all raise
``ContractParseError``.  Reading "no rule" out of an unparsed section is the
most dangerous failure available to an auditing engine.

Contents
    1. Rule types
    2. Vocabulary        unit-basis prose -> invoice tokens
    3. Markdown helpers
    4. Parser            text -> H5Contract, with cross-validation
    5. Plain-text rendering cross-check
    6. Serialisation     contract_rules.json, with a source fingerprint

Nothing here reads an invoice or a label.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from ..shared.money import decimal_to_cents

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO_ROOT / "contracts" / "hospital_5"
CONTRACT_PATH = CONTRACT_DIR / "network_reimbursement_agreement.md"
CONTRACT_TXT_PATH = CONTRACT_DIR / "network_reimbursement_agreement.txt"

#: Bumped whenever the parser's reading of the contract could change.  Part of
#: the fingerprint written to every Hospital 5 artifact, so a semantic review
#: made against an older reading is detected as stale.
PARSER_VERSION = "h5-contract-1"


class ContractParseError(Exception):
    """A section or clause that should carry a rule could not be read completely."""


# ==========================================================================
# 1. Rule types
# ==========================================================================

@dataclass(frozen=True)
class H5Service:
    name: str
    #: Every Hospital 5 service name is <qualifier> <specialty> <concept>; the
    #: parser verifies the three vocabularies are disjoint before the matcher
    #: relies on that.
    qualifier: str
    specialty: str
    concept: str
    base_rate_cents: int
    #: The billed-token form of the contractual unit basis ("per_night").
    unit_basis: str
    #: The contract's own words ("per night of occupancy").
    unit_basis_text: str
    compound_unit_basis: bool
    source_text: str


@dataclass(frozen=True)
class DailyCap:
    """Table 1 "Daily cap".  The agreement states the number and nothing else;
    what a breach does to the payable amount is an audit policy, recorded in
    ``audit.AuditPolicy`` and the decision log."""

    service: str
    max_units: int
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
class NonBusinessDayUplift:
    """Section 6: uplift for a Service delivered on a day that is not a
    Business Day (clause 2.2: Saturday or Sunday)."""

    service: str
    uplift_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class Bundle:
    """Section 7.1: both services on the same patient's Service Day -> both
    take their substituted base rate, before the multipliers."""

    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class VolumeDiscountTier:
    """Section 8: discount on units beyond a cumulative utilisation bound."""

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


@dataclass
class H5Contract:
    contract_number: str
    provider: str
    payer: str
    effective_from: str
    effective_to: str
    currency: str
    rounding_convention: str
    #: Clause 1.2: facility code -> facility name, in Table 2 column order.
    facilities: dict[str, str]
    #: Clause 1.3, in Table 3 column order.
    plan_tiers: tuple[str, ...]
    #: Clause 2.2: ISO weekday numbers (Monday = 0) that are not Business Days.
    non_business_weekdays: tuple[int, ...]
    #: Clause 3.1 stage order, as parsed.
    pricing_order: tuple[str, ...]
    services: dict[str, H5Service]
    #: Table 2: service -> facility code -> multiplier.
    facility_multipliers: dict[str, dict[str, Decimal]]
    #: Table 3: service -> plan tier -> multiplier.
    tier_multipliers: dict[str, dict[str, Decimal]]
    daily_caps: dict[str, DailyCap]
    threshold_premiums: dict[str, ThresholdPremium]
    non_business_day_uplifts: dict[str, NonBusinessDayUplift]
    bundles: list[Bundle]
    volume_discounts: dict[str, list[VolumeDiscountTier]]
    exclusion_windows: list[ExclusionWindow]
    #: Section 10, clause id -> the clause text.
    invoice_clauses: dict[str, str]
    #: Prose conventions the engine implements, clause id -> text.
    conventions: dict[str, str]
    fingerprint: str
    source_sha256: str
    warnings: list[str] = field(default_factory=list)

    # -- lookups -----------------------------------------------------------

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
        """Which hospital-wide rule families involve ``service``.

        Facility and plan-tier multipliers touch every service and depend only
        on the invoice, so they are not listed: they never make one line's
        price depend on another line.
        """
        out = []
        if self.bundle_partner(service):
            out.append("bundle")
        if service in self.threshold_premiums:
            out.append("threshold_premium")
        if service in self.non_business_day_uplifts:
            out.append("non_business_day_uplift")
        if service in self.daily_caps:
            out.append("daily_cap")
        if service in self.volume_discounts:
            out.append("cumulative_discount")
        if any(service in (e.service, e.other_service) for e in self.exclusion_windows):
            out.append("exclusion")
        return out

    # -- vocabulary ----------------------------------------------------------

    @property
    def qualifiers(self) -> frozenset[str]:
        return frozenset(s.qualifier for s in self.services.values())

    @property
    def specialties(self) -> frozenset[str]:
        return frozenset(s.specialty for s in self.services.values())

    @property
    def concepts(self) -> frozenset[str]:
        return frozenset(s.concept for s in self.services.values())

    @property
    def concept_words(self) -> frozenset[str]:
        return frozenset(w for s in self.services.values() for w in s.concept.split())

    @property
    def vocabulary(self) -> frozenset[str]:
        return self.qualifiers | self.specialties | self.concept_words


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
COMPOUND_BASES = frozenset({"per_hour_per_item"})

#: The noun a cap or threshold uses for a unit on each basis ("12 nights").
#: Used only to cross-check the tables against Table 1, never to price.
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

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
             "saturday": 5, "sunday": 6}

#: Clause 3.1, in the engine's stage names.
EXPECTED_PRICING_ORDER = ("bundle", "facility", "plan_tier", "premium", "cumulative_discount")
_ORDER_PHRASES = {
    "substitution of a bundled rate": "bundle",
    "the facility multiplier": "facility",
    "the plan-tier multiplier": "plan_tier",
    "any premium or uplift": "premium",
    "any cumulative volume discount": "cumulative_discount",
}


# ==========================================================================
# 3. Markdown helpers
# ==========================================================================

def _long_date(text: str) -> str:
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text.strip())
    if not m or m.group(2).lower() not in _MONTHS:
        raise ContractParseError(f"unparseable date: {text!r}")
    return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _gbp(text: str, context: str) -> int:
    """'GBP 1,265.25' -> 126525, exactly; never touches binary floating point."""
    m = re.fullmatch(r"GBP\s+(\d{1,3}(?:,\d{3})*|\d+)(\.\d{1,2})?", text.strip())
    if not m:
        raise ContractParseError(f"{context}: unparseable money amount {text!r}")
    try:
        return decimal_to_cents(m.group(1).replace(",", "") + (m.group(2) or ""))
    except ValueError as exc:
        raise ContractParseError(f"{context}: {exc}") from exc


def _percent(text: str, context: str, *, signed: bool) -> Decimal:
    """'+15%' -> Decimal('0.15') (signed); '12%' -> Decimal('0.12') (unsigned)."""
    sign = r"\+\s*" if signed else ""
    m = re.fullmatch(rf"{sign}(\d+(?:\.\d+)?)\s*%", text.strip())
    if not m:
        raise ContractParseError(f"{context}: unparseable percentage {text!r}")
    value = Decimal(m.group(1)) / 100
    if not Decimal(0) < value < Decimal(1):
        raise ContractParseError(f"{context}: percentage {text!r} outside (0%, 100%)")
    return value


def _multiplier(text: str, context: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ContractParseError(f"{context}: unparseable multiplier {text!r}") from None
    if not value.is_finite() or not Decimal("0.5") <= value <= Decimal(2):
        # A multiplier outside this band is far more likely a parse or typing
        # error than a negotiated rate; refuse rather than price with it.
        raise ContractParseError(f"{context}: implausible multiplier {text!r}")
    return value


def _quantity(text: str, context: str, *, prefix: str = "") -> tuple[int, str]:
    """'more than 10 items' -> (10, 'per_item'); '24 hours' -> (24, 'per_hour')."""
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


def _subsections(body: str) -> tuple[str, dict[str, str]]:
    """Split a section on ``### `` headings: (text before the first, {title: body})."""
    parts = re.split(r"^### (.+?)\s*$", body, flags=re.M)
    return parts[0], {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def _clause(body: str, clause_id: str) -> str:
    m = re.search(rf"^{re.escape(clause_id)}\s+(.+?)\s*$", body, flags=re.M)
    if not m:
        raise ContractParseError(f"clause {clause_id} not found")
    return m.group(1)


def _table(body: str, context: str, header: tuple[str, ...]) -> list[tuple[list[str], str]]:
    """Data rows of the one markdown table in ``body``, with each row's source line.

    The header must be exactly ``header``: a reordered column (F-NORTH before
    F-MAIN, say) would otherwise price every line with the wrong multiplier
    and never fail.  Raises on a missing table, a second table, a row with the
    wrong number of cells, or a row that is not in the table body.
    """
    lines = [raw.strip() for raw in body.splitlines()]
    pipe = [i for i, line in enumerate(lines) if line.startswith("|")]
    if not pipe:
        raise ContractParseError(f"{context}: no table found")
    if pipe != list(range(pipe[0], pipe[-1] + 1)):
        raise ContractParseError(f"{context}: more than one table, or a table interrupted by text")
    cells = [[c.strip() for c in lines[i].strip("|").split("|")] for i in pipe]
    if tuple(cells[0]) != header:
        raise ContractParseError(f"{context}: header {cells[0]} is not the expected {list(header)}")
    if len(cells) < 3 or not all(re.fullmatch(r":?-{3,}:?", c) for c in cells[1]):
        raise ContractParseError(f"{context}: header separator missing")
    rows = []
    for i, row in zip(pipe[2:], cells[2:]):
        if len(row) != len(header):
            raise ContractParseError(f"{context}: row has {len(row)} cells, expected {len(header)}: {lines[i]!r}")
        rows.append((row, lines[i]))
    if not rows:
        raise ContractParseError(f"{context}: no table rows parsed")
    return rows


def _require(text: str, pattern: str, what: str) -> re.Match:
    m = re.search(pattern, text)
    if not m:
        raise ContractParseError(f"{what}: expected wording not found; refusing to assume it")
    return m


# ==========================================================================
# 4. Parser
# ==========================================================================

def parse_contract(path: Path = CONTRACT_PATH) -> H5Contract:
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
    currency = _meta(text, "Currency")
    if currency != "GBP":
        raise ContractParseError(f"unsupported currency {currency!r}: every rate is parsed as GBP")

    # -- Section 1: parties, term, facilities, plan tiers --------------------
    s1 = _section(text, 1, "Parties, Term and Network")
    term = _require(_clause(s1, "1.1"), r"runs from (\d{1,2} [A-Za-z]+ \d{4}) to (\d{1,2} [A-Za-z]+ \d{4})",
                    "clause 1.1 term")
    if (_long_date(term.group(1)), _long_date(term.group(2))) != (effective_from, effective_to):
        raise ContractParseError("clause 1.1 term disagrees with the front matter")
    c12 = _clause(s1, "1.2")
    _require(c12, r"facility code recorded on a line item determines which column of Table 2 applies",
             "clause 1.2 facility column")
    facilities = {code: name for (code, name), _ in _table(s1, "clause 1.2 facilities", ("Facility code", "Facility"))}
    c13 = _clause(s1, "1.3")
    _require(c13, r"plan tier recorded on an invoice determines which column of Table 3 applies to every "
                  r"line item on that invoice", "clause 1.3 plan-tier column")
    tier_line = _require(s1, r"(?m)^\s{4}([A-Z]+(?:,\s*[A-Z]+)+)\s*$", "clause 1.3 tier list").group(1)
    plan_tiers = tuple(t.strip() for t in tier_line.split(","))

    # -- Section 2: interpretation ------------------------------------------
    s2 = _section(text, 2, "Interpretation")
    _require(_clause(s2, "2.1"), r"calendar day recorded as the Service Date of the line item", "clause 2.1")
    bd = _require(_clause(s2, "2.2"), r"any Service Day other than a (\w+) or a (\w+)\.", "clause 2.2 business day")
    try:
        non_business = tuple(sorted(_WEEKDAYS[d.lower()] for d in bd.groups()))
    except KeyError:
        raise ContractParseError(f"clause 2.2: unknown weekday in {bd.group(0)!r}") from None

    # -- Section 3: order and rounding -------------------------------------
    s3 = _section(text, 3, "Calculating the Effective Unit Rate")
    c31 = _clause(s3, "3.1")
    stages = re.findall(r"\(([a-e])\)\s*([^;.]+?)(?:;|\.)", c31)
    order = tuple(_ORDER_PHRASES.get(re.sub(r"^and\s+", "", s.strip()), "?") for _, s in stages)
    if order != EXPECTED_PRICING_ORDER:
        raise ContractParseError(f"clause 3.1 pricing order not recognised: {stages!r}")
    _require(c31, r"The line total is then the resulting unit rate multiplied by the billed quantity",
             "clause 3.1 line total")
    c32 = _clause(s3, "3.2")
    _require(c32, r"rounded to the nearest whole cent, with exact halves rounded away from zero", "clause 3.2 rounding")
    _require(c32, r"Rounding is applied after each individual step of the calculation, not once at the end",
             "clause 3.2 rounding frequency")
    c33 = _clause(s3, "3.3")
    _require(c33, r"apply any premium from Section 5 or Section 6", "clause 3.3 premium or uplift")
    c34 = _clause(s3, "3.4")
    _require(c34, r"A multiplier of 1\.0 leaves the amount unchanged, but the rounding step is still taken",
             "clause 3.4")

    # -- Section 4: Tables 1, 2 and 3 ----------------------------------------
    s4_head, s4_sub = _subsections(_section(text, 4, "Table 1 — Base Rates"))
    services: dict[str, H5Service] = {}
    caps: dict[str, DailyCap] = {}
    for (name, basis_text, rate_text, cap_text), line in _table(
            s4_head, "Table 1", ("Service", "Unit basis", "Base rate", "Daily cap")):
        if name in services:
            raise ContractParseError(f"Table 1: duplicate service {name!r}")
        basis = UNIT_BASIS_TEXT.get(basis_text.lower())
        if basis is None:
            raise ContractParseError(f"Table 1: unknown unit basis {basis_text!r} for {name!r}")
        words = name.split()
        if len(words) < 3:
            raise ContractParseError(f"Table 1: {name!r} is not <qualifier> <specialty> <concept>")
        services[name] = H5Service(
            name=name, qualifier=words[0].lower(), specialty=words[1].lower(),
            concept=" ".join(words[2:]).lower(), base_rate_cents=_gbp(rate_text, f"Table 1 {name!r}"),
            unit_basis=basis, unit_basis_text=basis_text, compound_unit_basis=basis in COMPOUND_BASES,
            source_text=line)
        if cap_text != "—":
            qty, noun = _quantity(cap_text, f"Table 1 cap {name!r}")
            if basis in COMPOUND_BASES or noun != basis:
                raise ContractParseError(f"Table 1: cap for {name!r} stated in {noun}, basis is {basis}")
            caps[name] = DailyCap(name, qty, "4 (Table 1)", line)
    _check_vocabularies(services)

    def known(name: str, context: str) -> str:
        if name not in services:
            raise ContractParseError(f"{context}: unknown service {name!r}")
        return name

    def matrix(title: str, columns: tuple[str, ...]) -> dict[str, dict[str, Decimal]]:
        if title not in s4_sub:
            raise ContractParseError(f"Section 4: subsection {title!r} not found")
        out: dict[str, dict[str, Decimal]] = {}
        for cells, _ in _table(s4_sub[title], title, ("Service",) + columns):
            name = known(cells[0], title)
            if name in out:
                raise ContractParseError(f"{title}: duplicate row for {name!r}")
            out[name] = {col: _multiplier(v, f"{title} {name!r} {col}") for col, v in zip(columns, cells[1:])}
        missing = sorted(set(services) - set(out))
        if missing:
            raise ContractParseError(f"{title}: no row for Table 1 service(s) {missing}")
        return out

    facility_mult = matrix("Table 2 — Facility Multipliers", tuple(facilities))
    tier_mult = matrix("Table 3 — Plan-Tier Multipliers", plan_tiers)
    _require(s4_sub["Table 2 — Facility Multipliers"], r"Applied to the base rate in Table 1 for the facility",
             "Table 2 scope")
    _require(s4_sub["Table 3 — Plan-Tier Multipliers"], r"Applied after the facility multiplier, for the plan tier "
             r"recorded on the invoice", "Table 3 scope")

    def check_noun(noun_basis: str, service: str, context: str) -> None:
        if noun_basis != services[service].unit_basis:
            raise ContractParseError(f"{context}: quantity for {service!r} is stated in the wrong unit "
                                     f"({noun_basis} vs {services[service].unit_basis})")

    # -- Section 5: threshold premiums --------------------------------------
    s5 = _section(text, 5, "Threshold Premiums")
    c51 = _clause(s5, "5.1")
    _require(c51, r"aggregate quantity of the Service delivered to the Patient on the Service Day, across all "
                  r"line items and all invoices", "clause 5.1 aggregate")
    premiums: dict[str, ThresholdPremium] = {}
    for (name, when, uplift), line in _table(s5, "Section 5", ("Service", "Daily quantity threshold", "Uplift")):
        known(name, "Section 5")
        if name in premiums:
            raise ContractParseError(f"Section 5: duplicate service {name!r}")
        qty, noun = _quantity(when, f"Section 5 {name!r}", prefix=r"more than\s+")
        check_noun(noun, name, "Section 5")
        premiums[name] = ThresholdPremium(name, qty, _percent(uplift, f"Section 5 {name!r}", signed=True), "5.1", line)

    # -- Section 6: non-business-day uplifts ----------------------------------
    s6 = _section(text, 6, "Non-Business-Day Uplifts")
    nbd: dict[str, NonBusinessDayUplift] = {}
    for (name, uplift), line in _table(s6, "Section 6", ("Service", "Uplift")):
        known(name, "Section 6")
        if name in nbd:
            raise ContractParseError(f"Section 6: duplicate service {name!r}")
        nbd[name] = NonBusinessDayUplift(name, _percent(uplift, f"Section 6 {name!r}", signed=True), "6", line)
    both = sorted(set(premiums) & set(nbd))
    if both:
        # Both are stage (d) of clause 3.1 ("any premium or uplift") and the
        # agreement never says how they combine.  Refuse rather than let code
        # order decide.
        raise ContractParseError(f"services carry both a Section 5 premium and a Section 6 uplift: {both}")

    # -- Section 7: bundles -------------------------------------------------
    s7 = _section(text, 7, "Bundled Services")
    c71 = _clause(s7, "7.1")
    _require(c71, r"Where both Services in a pair are delivered to the same Patient on the same Service Day, the "
                  r"substituted rates below replace the Table 1 base rates", "clause 7.1 scope")
    _require(c71, r"The facility and plan-tier multipliers are then applied to the substituted rate",
             "clause 7.1 multipliers")
    bundles: list[Bundle] = []
    bundled: set[str] = set()
    for (a, rate_a, b, rate_b), line in _table(
            s7, "Section 7", ("Service A", "Substituted rate A", "Service B", "Substituted rate B")):
        for n in (known(a, "Section 7"), known(b, "Section 7")):
            if n in bundled:
                # The engine substitutes at most one bundled rate per line.
                raise ContractParseError(f"Section 7: {n!r} appears in more than one bundle")
            bundled.add(n)
        if a == b:
            raise ContractParseError(f"Section 7: {a!r} is bundled with itself")
        bundles.append(Bundle(a, b, _gbp(rate_a, f"Section 7 {a!r}"), _gbp(rate_b, f"Section 7 {b!r}"), "7.1", line))

    # -- Section 8: cumulative volume discounts -----------------------------
    s8 = _section(text, 8, "Cumulative Volume Discounts")
    c81 = _clause(s8, "8.1")
    for pattern, what in (
        (r"counted cumulatively across the whole term of this Agreement", "whole term"),
        (r"aggregated across all Patients", "all patients"),
        (r"in Service Date order", "service date order"),
        (r"not reset by a change of facility or of plan tier", "no reset"),
        (r"Where two line items share a Service Date they are counted in ascending order of line identifier",
         "line identifier order"),
        (r"Where two thresholds are met the deeper discount applies", "deeper discount"),
    ):
        _require(c81, pattern, f"clause 8.1 {what}")
    discounts: dict[str, list[VolumeDiscountTier]] = {}
    for (name, exceeds, pct), line in _table(
            s8, "Section 8", ("Service", "Cumulative utilisation", "Discount on subsequent units")):
        known(name, "Section 8")
        qty, noun = _quantity(exceeds, f"Section 8 {name!r}", prefix=r"more than\s+")
        check_noun(noun, name, "Section 8")
        discounts.setdefault(name, []).append(
            VolumeDiscountTier(name, qty, _percent(pct, f"Section 8 {name!r}", signed=False), "8", line))
    for name, tiers in discounts.items():
        tiers.sort(key=lambda t: t.exceeds_units)
        if len({t.exceeds_units for t in tiers}) != len(tiers):
            raise ContractParseError(f"Section 8: {name!r} repeats a threshold")
        for lo, hi in zip(tiers, tiers[1:]):
            if hi.discount_fraction <= lo.discount_fraction:
                # 8.1: "the deeper discount applies".  A higher threshold with a
                # shallower discount would make that sentence contradict the
                # table; refuse to guess.
                raise ContractParseError(f"Section 8: {name!r} tiers are not monotonically deeper")

    # -- Section 9: exclusion windows ---------------------------------------
    s9 = _section(text, 9, "Exclusion Windows")
    exclusions: list[ExclusionWindow] = []
    for (name, window, other), line in _table(s9, "Section 9", ("Service", "Not billable within", "Of this Service")):
        known(name, "Section 9")
        known(other, "Section 9")
        if name == other:
            raise ContractParseError(f"Section 9: {name!r} excludes itself")
        m = re.fullmatch(r"(\d+)\s+days?", window.strip())
        if not m or int(m.group(1)) <= 0:
            raise ContractParseError(f"Section 9: unparseable window {window!r} for {name!r}")
        exclusions.append(ExclusionWindow(name, int(m.group(1)), other, "9", line))
    if re.search(r"either direction|before or after", s9):
        warnings.append("Section 9 now states a direction; re-check the exclusion policy")

    # -- Section 10: invoicing ----------------------------------------------
    s10 = _section(text, 10, "Invoicing")
    invoice_clauses = {cid: _clause(s10, cid) for cid in ("10.1", "10.2", "10.3")}
    _require(invoice_clauses["10.1"], r"quotes the contract number", "clause 10.1 contract number")
    _require(invoice_clauses["10.1"], r"invoice number unique across the term", "clause 10.1 unique number")
    _require(invoice_clauses["10.2"], r"within the term and may not fall after the invoice date", "clause 10.2")
    _require(invoice_clauses["10.3"], r"same Service may not be billed twice for the same Patient and the same "
                                      r"Service Date, whether on one invoice or across several", "clause 10.3")

    source_sha = hashlib.sha256(raw).hexdigest()
    contract = H5Contract(
        contract_number=_meta(text, "Contract number"),
        provider=_meta(text, "Provider"),
        payer=_meta(text, "Payer"),
        effective_from=effective_from,
        effective_to=effective_to,
        currency=currency,
        rounding_convention=rounding,
        facilities=facilities,
        plan_tiers=plan_tiers,
        non_business_weekdays=non_business,
        pricing_order=order,
        services=services,
        facility_multipliers=facility_mult,
        tier_multipliers=tier_mult,
        daily_caps=caps,
        threshold_premiums=premiums,
        non_business_day_uplifts=nbd,
        bundles=bundles,
        volume_discounts=discounts,
        exclusion_windows=exclusions,
        invoice_clauses=invoice_clauses,
        conventions={"1.2": c12, "1.3": c13, "2.2": _clause(s2, "2.2"), "3.1": c31, "3.2": c32, "3.3": c33,
                     "3.4": c34, "5.1": c51, "7.1": c71, "8.1": c81},
        fingerprint=hashlib.sha256(f"{PARSER_VERSION}:{source_sha}".encode()).hexdigest(),
        source_sha256=source_sha,
        warnings=warnings,
    )
    _validate(contract)
    return contract


def _check_vocabularies(services: dict[str, H5Service]) -> None:
    """The matcher reads a name as three slots; that needs disjoint vocabularies."""
    qualifiers = {s.qualifier for s in services.values()}
    specialties = {s.specialty for s in services.values()}
    concept_words = {w for s in services.values() for w in s.concept.split()}
    for a, b, label in ((qualifiers, specialties, "qualifier/specialty"),
                        (qualifiers, concept_words, "qualifier/concept"),
                        (specialties, concept_words, "specialty/concept")):
        if a & b:
            raise ContractParseError(f"service-name vocabularies overlap ({label}): {sorted(a & b)}")


def _validate(c: H5Contract) -> None:
    """Invariants over the parsed rule set, to catch a silently truncated parse."""
    for family, coll in (("facilities", c.facilities), ("plan tiers", c.plan_tiers),
                         ("threshold premiums", c.threshold_premiums), ("daily caps", c.daily_caps),
                         ("non-business-day uplifts", c.non_business_day_uplifts), ("bundles", c.bundles),
                         ("cumulative discounts", c.volume_discounts), ("exclusion windows", c.exclusion_windows)):
        if not coll:
            raise ContractParseError(f"{family}: section present but nothing parsed")
    referenced = (
        set(c.threshold_premiums) | set(c.daily_caps) | set(c.volume_discounts)
        | set(c.non_business_day_uplifts) | set(c.facility_multipliers) | set(c.tier_multipliers)
        | {s for b in c.bundles for s in (b.service_a, b.service_b)}
        | {s for e in c.exclusion_windows for s in (e.service, e.other_service)}
    )
    unknown = referenced - set(c.services)
    if unknown:
        raise ContractParseError(f"rules reference unknown services: {sorted(unknown)}")
    for name in c.services:
        if set(c.facility_multipliers[name]) != set(c.facilities):
            raise ContractParseError(f"Table 2 row for {name!r} does not cover every facility")
        if set(c.tier_multipliers[name]) != set(c.plan_tiers):
            raise ContractParseError(f"Table 3 row for {name!r} does not cover every plan tier")


# ==========================================================================
# 5. Plain-text rendering cross-check
# ==========================================================================

def _txt_rows(text: str, heading: str, first_column: str) -> list[list[str]]:
    """Fixed-width rows of the table under ``heading`` in the .txt rendering."""
    start = text.find(heading)
    if start < 0:
        raise ContractParseError(f"txt: heading {heading!r} not found")
    rows, in_table = [], False
    for line in text[start:].splitlines()[1:]:
        if not in_table:
            in_table = line.startswith(first_column + " ")
            continue
        if not line.strip():
            break
        rows.append(re.split(r"\s{2,}", line.strip()))
    return rows


def cross_check_text_rendering(c: H5Contract, path: Path = CONTRACT_TXT_PATH) -> list[str]:
    """Compare Tables 1-3 with the plain-text rendering of the same agreement.

    The Markdown file is the one parsed; the plain text is the same agreement
    laid out differently.  Any disagreement is reported rather than resolved.
    (The PDF rate-table rendering is not machine-checked: the project carries
    no PDF library.  It was compared by eye; see the decision log.)
    """
    text = Path(path).read_text(encoding="utf-8")
    problems = []
    t1 = _txt_rows(text, "TABLE 1 — BASE RATES", "Service")
    if len(t1) != len(c.services):
        problems.append(f"Table 1: txt has {len(t1)} rows, md has {len(c.services)}")
    for row in t1:
        if len(row) != 4 or row[0] not in c.services:
            problems.append(f"Table 1 txt row not understood: {row}")
            continue
        s = c.services[row[0]]
        cap = c.daily_caps.get(s.name)
        if (row[1], _gbp(row[2], "txt"), row[3]) != (s.unit_basis_text, s.base_rate_cents,
                                                     "—" if cap is None else row[3]):
            problems.append(f"Table 1 txt disagrees for {s.name!r}: {row}")
        if cap is not None and _quantity(row[3], "txt")[0] != cap.max_units:
            problems.append(f"Table 1 txt cap disagrees for {s.name!r}: {row[3]}")
    for heading, matrix, cols in (("TABLE 2 — FACILITY MULTIPLIERS", c.facility_multipliers, tuple(c.facilities)),
                                  ("TABLE 3 — PLAN-TIER MULTIPLIERS", c.tier_multipliers, c.plan_tiers)):
        rows = _txt_rows(text, heading, "Service")
        if len(rows) != len(matrix):
            problems.append(f"{heading}: txt has {len(rows)} rows, md has {len(matrix)}")
        for row in rows:
            if len(row) != 1 + len(cols) or row[0] not in matrix:
                problems.append(f"{heading} txt row not understood: {row}")
            elif [Decimal(v) for v in row[1:]] != [matrix[row[0]][col] for col in cols]:
                problems.append(f"{heading} txt disagrees for {row[0]!r}: {row[1:]}")
    return problems


# ==========================================================================
# 6. Serialisation
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


def rule_counts(c: H5Contract) -> dict[str, int]:
    return {
        "services": len(c.services),
        "facilities": len(c.facilities),
        "plan_tiers": len(c.plan_tiers),
        "facility_multiplier_cells": sum(len(v) for v in c.facility_multipliers.values()),
        "plan_tier_multiplier_cells": sum(len(v) for v in c.tier_multipliers.values()),
        "daily_caps": len(c.daily_caps),
        "threshold_premiums": len(c.threshold_premiums),
        "non_business_day_uplifts": len(c.non_business_day_uplifts),
        "bundle_pairs": len(c.bundles),
        "cumulative_discount_services": len(c.volume_discounts),
        "cumulative_discount_tiers": sum(len(v) for v in c.volume_discounts.values()),
        "exclusion_windows": len(c.exclusion_windows),
        "compound_unit_bases": sum(s.compound_unit_basis for s in c.services.values()),
    }


def write_contract_rules(c: H5Contract, path: Path) -> None:
    doc = {
        "fingerprint": c.fingerprint,
        "source": str(CONTRACT_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "source_sha256": c.source_sha256,
        "parser_version": PARSER_VERSION,
        "counts": rule_counts(c),
        "text_rendering_disagreements": cross_check_text_rendering(c),
        **{k: _jsonable(v) for k, v in c.__dict__.items() if k not in ("fingerprint", "source_sha256")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
