"""Hospital 3: the three-document contract package as structured rules.

Hospital 3's agreement (``INS-H3-2024-0562``) is split across three documents
in ``contracts/hospital_3/``:

    base_agreement.md             pricing conventions and every rule table
    appendix_b_rate_schedule.md   the rates as originally agreed
    amendment_no_1.md             rate substitutions and two added services,
                                  effective 1 January 2025 *by Service Date*

Everything the Hospital 3 audit knows about the contract comes out of this
module, with the document, section and table row each rule came from.  No
rate, cap, threshold, uplift, discount, bundle or exclusion window is written
into the audit code.

**Precedence (clause 1.3) is explicit.**  An amendment prevails over
Appendix B, and Appendix B prevails over the Base Agreement.  Rates are
modelled as dated entries, each tagged with its document; ``rate_on`` picks,
among the entries in force on a Service Date, the one from the document of
highest precedence.  The Base Agreement states no rates, so for a rate the
question is only ever "Amendment or Appendix B".  Where two documents state
the same rule (the daily cap appears both as an Appendix B column and as
Base Agreement Section 7), the parser requires them to agree rather than
silently preferring one.

**The amendment operates by Service Date (A1.1.2).**  A substituted rate
applies to a line whose Service Date is on or after 1 January 2025; an
earlier Service Date keeps the Appendix B rate; the invoice date is
irrelevant.  The two added services (A1.3) have no rate at all before that
date: they "were not contracted before that date and are not billable in
respect of earlier Service Dates".

The parser is loud.  A table whose header is not the header the engine
implements, a row with the wrong number of cells, a service a rule names that
the rate schedule does not carry, front matter that differs between the
three documents, or a prose clause whose wording is not the wording
implemented all raise ``ContractParseError``.

Contents
    1. Rule types
    2. Vocabulary        unit-basis prose -> invoice tokens
    3. Markdown helpers
    4. Parser            three documents -> H3Contract, with cross-validation
    5. Plain-text rendering cross-check
    6. Serialisation     contract_rules.json, with a source fingerprint

Nothing here reads an invoice or a label.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..shared.money import decimal_to_cents

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO_ROOT / "contracts" / "hospital_3"
BASE_PATH = CONTRACT_DIR / "base_agreement.md"
APPENDIX_PATH = CONTRACT_DIR / "appendix_b_rate_schedule.md"
AMENDMENT_PATH = CONTRACT_DIR / "amendment_no_1.md"

#: Bumped whenever the parser's reading of the contract could change.  Part of
#: the fingerprint written to every Hospital 3 artifact.
PARSER_VERSION = "h3-contract-1"

#: Document names, and clause 1.3 precedence: a higher number prevails.
BASE, APPENDIX_B, AMENDMENT_1 = "base_agreement", "appendix_b", "amendment_no_1"
PRECEDENCE: dict[str, int] = {BASE: 1, APPENDIX_B: 2, AMENDMENT_1: 3}


class ContractParseError(Exception):
    """A section or clause that should carry a rule could not be read completely."""


# ==========================================================================
# 1. Rule types
# ==========================================================================

@dataclass(frozen=True)
class RateEntry:
    """One rate a document states for one service, and the Service Dates it covers.

    ``start`` / ``end`` are inclusive; ``None`` means open-ended.  Appendix B
    entries are open-ended: B.1 says they apply "from the commencement of the
    term", and an amendment entry covering the same date prevails (1.3).
    """

    service: str
    rate_cents: int
    document: str
    clause_id: str
    start: Optional[_dt.date]
    end: Optional[_dt.date]
    source_text: str

    def covers(self, date: _dt.date) -> bool:
        return (self.start is None or date >= self.start) and (self.end is None or date <= self.end)


@dataclass(frozen=True)
class H3Service:
    name: str
    #: Every Hospital 3 service name is <qualifier> <specialty> <concept>; the
    #: parser verifies the three vocabularies are disjoint before the matcher
    #: relies on that.
    qualifier: str
    specialty: str
    concept: str
    #: The billed-token form of the contractual unit basis ("per_night").
    unit_basis: str
    #: The contract's own words ("per night of occupancy").
    unit_basis_text: str
    #: "per hour, per item" is a basis of its own, not collapsed into either part.
    compound_unit_basis: bool
    #: Every rate stated for the service, in any document.
    rates: tuple[RateEntry, ...]
    #: The first Service Date on which the service is contracted; ``None``
    #: means from the commencement of the term (an Appendix B service).
    contracted_from: Optional[_dt.date]
    #: The document that brought the service into the contract.
    listed_in: str
    source_text: str


@dataclass(frozen=True)
class DailyCap:
    """Base Agreement Section 7, restated in the Appendix B "Daily cap" column.
    The agreement states the maximum and nothing else; what a breach does to
    the payable amount is an audit policy (``audit.AuditPolicy``)."""

    service: str
    max_units: int
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class ThresholdPremium:
    """Section 4: uplift once the patient's Service Day aggregate exceeds a bound."""

    service: str
    exceeds_units: int
    uplift_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class NonBusinessDayUplift:
    """Section 5: uplift for a Service Day that is not a Business Day (2.2)."""

    service: str
    uplift_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class Bundle:
    """Section 8 / 8.1: both services on the same patient's Service Day -> both
    take their bundled rate, substituted before any premium or discount."""

    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class VolumeDiscountTier:
    """Section 6 / 6.1: discount once cumulative utilisation *before* a line
    exceeds a bound."""

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
class H3Contract:
    contract_number: str
    provider: str
    payer: str
    effective_from: str
    effective_to: str
    currency: str
    rounding_convention: str
    #: Clause 1.3 and ``PRECEDENCE``: documents, highest precedence first.
    precedence: tuple[str, ...]
    #: A1.1.1: the amendment's effective date; A1.1.2: it operates by Service Date.
    amendment_effective: str
    amendment_basis: str
    #: Clause 1.5: the one facility.  No facility differential applies and all
    #: plan tiers are reimbursed identically, so both multipliers are 1.
    facility_code: str
    facility_multiplier: Decimal
    plan_tier_multiplier: Decimal
    #: Clause 2.2: ISO weekday numbers (Monday = 0) that are not Business Days.
    non_business_weekdays: tuple[int, ...]
    #: Clause 3.2 stage order, as parsed.
    pricing_order: tuple[str, ...]
    services: dict[str, H3Service]
    daily_caps: dict[str, DailyCap]
    threshold_premiums: dict[str, ThresholdPremium]
    non_business_day_uplifts: dict[str, NonBusinessDayUplift]
    bundles: list[Bundle]
    volume_discounts: dict[str, list[VolumeDiscountTier]]
    exclusion_windows: list[ExclusionWindow]
    #: Section 10, clause id -> the clause text.
    invoice_clauses: dict[str, str]
    #: Prose conventions the engine implements, "document clause" -> text.
    conventions: dict[str, str]
    fingerprint: str
    source_sha256: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    # -- dated rates ---------------------------------------------------------

    @property
    def amendment_date(self) -> _dt.date:
        return _dt.date.fromisoformat(self.amendment_effective)

    def rate_entry_on(self, service: str, date: _dt.date) -> Optional[RateEntry]:
        """The rate entry in force for ``service`` on ``date``, by clause 1.3
        precedence; ``None`` if the service is not contracted on that date."""
        live = [e for e in self.services[service].rates if e.covers(date)]
        if not live:
            return None
        top = max(PRECEDENCE[e.document] for e in live)
        winners = [e for e in live if PRECEDENCE[e.document] == top]
        if len(winners) != 1:
            raise ContractParseError(f"{service!r}: {len(winners)} rates of equal precedence on {date}")
        return winners[0]

    def rate_on(self, service: str, date: _dt.date) -> Optional[int]:
        e = self.rate_entry_on(service, date)
        return None if e is None else e.rate_cents

    def contracted_on(self, service: str, date: _dt.date) -> bool:
        return self.rate_entry_on(service, date) is not None

    def is_amended(self, service: str) -> bool:
        """The amendment substitutes this service's rate (A1.2)."""
        s = self.services[service]
        return s.listed_in == APPENDIX_B and any(e.document == AMENDMENT_1 for e in s.rates)

    def is_added(self, service: str) -> bool:
        """The amendment adds this service to the contracted list (A1.3)."""
        return self.services[service].listed_in == AMENDMENT_1

    def date_sensitive(self, service: str) -> bool:
        """Whether the rate or contracted status depends on which side of the
        amendment date a Service Date falls."""
        return self.is_amended(service) or self.is_added(service)

    # -- rules ---------------------------------------------------------------

    def bundle_partner(self, service: str) -> Optional[tuple[str, int, str]]:
        """``(partner, this service's bundled rate, clause id)``."""
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
        """Which rule families make ``service``'s price depend on something
        beyond the line itself (used to rank unresolved lines)."""
        out = []
        if self.date_sensitive(service):
            out.append("amendment")
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

    def slot(self, word: str) -> str:
        if word in self.qualifiers:
            return "qualifier"
        if word in self.specialties:
            return "specialty"
        if word in self.concept_words:
            return "concept"
        raise KeyError(word)


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

#: The noun a cap, threshold or tier uses for a unit on each basis ("12 procedures").
#: Used only to cross-check the tables against the rate schedule, never to price.
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

#: Clause 3.2, in the engine's stage names.
EXPECTED_PRICING_ORDER = ("bundle", "facility", "plan_tier", "premium", "cumulative_discount")
_ORDER_PHRASES = {
    "substitution of a bundled rate": "bundle",
    "the facility multiplier": "facility",
    "the plan-tier multiplier": "plan_tier",
    "any premium or uplift": "premium",
    "any cumulative volume discount": "cumulative_discount",
}

#: The front matter every document of the package must carry identically.
FRONT_MATTER = ("Contract number", "Provider", "Payer", "Effective from", "Effective to", "Currency",
                "Rounding convention")


# ==========================================================================
# 3. Markdown helpers
# ==========================================================================

def _long_date(text: str) -> str:
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text.strip())
    if not m or m.group(2).lower() not in _MONTHS:
        raise ContractParseError(f"unparseable date: {text!r}")
    return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _gbp(text: str, context: str) -> int:
    """'GBP 1,826.25' -> 182625, exactly; never touches binary floating point."""
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


def _quantity(text: str, context: str, *, prefix: str = "") -> tuple[int, str]:
    """'more than 10 items' -> (10, 'per_item'); '12 procedures' -> (12, 'per_procedure')."""
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


def _section(text: str, heading: str) -> str:
    """The body under ``## <heading>``, up to the next ``## `` heading."""
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, flags=re.M)
    if not m:
        raise ContractParseError(f"section not found: {heading}")
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, flags=re.M)
    return rest[: nxt.start()] if nxt else rest


def _clause(body: str, clause_id: str) -> str:
    m = re.search(rf"^{re.escape(clause_id)}\s+(.+?)\s*$", body, flags=re.M)
    if not m:
        raise ContractParseError(f"clause {clause_id} not found")
    return m.group(1)


def _table(body: str, context: str, header: tuple[str, ...]) -> list[tuple[list[str], str]]:
    """Data rows of the one markdown table in ``body``, with each row's source line.

    The header must be exactly ``header``, so a reordered or renamed column
    fails instead of silently pricing from the wrong cell.  Raises on a
    missing table, a second table, or a row with the wrong number of cells.
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


def _basis(text: str, context: str) -> str:
    basis = UNIT_BASIS_TEXT.get(text.strip().lower())
    if basis is None:
        raise ContractParseError(f"{context}: unknown unit basis {text!r}")
    return basis


# ==========================================================================
# 4. Parser
# ==========================================================================

def parse_contract(base_path: Path = BASE_PATH, appendix_path: Path = APPENDIX_PATH,
                   amendment_path: Path = AMENDMENT_PATH) -> H3Contract:
    raws = {BASE: Path(base_path).read_bytes(), APPENDIX_B: Path(appendix_path).read_bytes(),
            AMENDMENT_1: Path(amendment_path).read_bytes()}
    texts = {k: v.decode("utf-8") for k, v in raws.items()}
    base, appendix, amendment = texts[BASE], texts[APPENDIX_B], texts[AMENDMENT_1]
    warnings: list[str] = []

    # -- front matter: one contract, three documents --------------------------
    meta = {label: _meta(base, label) for label in FRONT_MATTER}
    for doc in (APPENDIX_B, AMENDMENT_1):
        for label in FRONT_MATTER:
            if _meta(texts[doc], label) != meta[label]:
                raise ContractParseError(f"{doc}: front matter {label!r} differs from the Base Agreement")
    if meta["Rounding convention"] != "half_up_cent":
        raise ContractParseError(f"unsupported rounding convention: {meta['Rounding convention']!r}")
    if meta["Currency"] != "GBP":
        raise ContractParseError(f"unsupported currency {meta['Currency']!r}: every rate is parsed as GBP")
    effective_from = _long_date(meta["Effective from"])
    effective_to = _long_date(meta["Effective to"])
    if effective_from > effective_to:
        raise ContractParseError("contract term runs backwards")
    term_start, term_end = _dt.date.fromisoformat(effective_from), _dt.date.fromisoformat(effective_to)

    # -- Base Agreement Section 1: term, precedence, facility ---------------------
    s1 = _section(base, "1. Parties, Term and Structure")
    term = _require(_clause(s1, "1.2"), r"runs from (\d{1,2} [A-Za-z]+ \d{4}) to (\d{1,2} [A-Za-z]+ \d{4})",
                    "clause 1.2 term")
    if (_long_date(term.group(1)), _long_date(term.group(2))) != (effective_from, effective_to):
        raise ContractParseError("clause 1.2 term disagrees with the front matter")
    c13 = _clause(s1, "1.3")
    _require(c13, r"an amendment prevails over Appendix B, and Appendix B prevails over this Base Agreement",
             "clause 1.3 precedence")
    precedence = tuple(sorted(PRECEDENCE, key=lambda d: -PRECEDENCE[d]))
    c14 = _clause(s1, "1.4")
    _require(c14, r"states its own effective date and the basis on which that date operates", "clause 1.4")
    c15 = _clause(s1, "1.5")
    facility = _require(c15, r"delivers the Services from [^(]*\(([A-Z0-9-]+)\)", "clause 1.5 facility").group(1)
    _require(c15, r"No facility differential applies", "clause 1.5 facility multiplier")
    _require(c15, r"all plan tiers are reimbursed identically", "clause 1.5 plan-tier multiplier")

    # -- Section 2: interpretation ---------------------------------------------
    s2 = _section(base, "2. Interpretation")
    c21 = _clause(s2, "2.1")
    _require(c21, r"calendar day recorded as the Service Date of the line item", "clause 2.1 Service Day")
    bd = _require(_clause(s2, "2.2"), r"any Service Day other than a (\w+) or a (\w+)\.", "clause 2.2 business day")
    try:
        non_business = tuple(sorted(_WEEKDAYS[d.lower()] for d in bd.groups()))
    except KeyError:
        raise ContractParseError(f"clause 2.2: unknown weekday in {bd.group(0)!r}") from None

    # -- Section 3: rounding and order ------------------------------------------
    s3 = _section(base, "3. Calculation Conventions")
    c31 = _clause(s3, "3.1")
    _require(c31, r"rounded to the nearest whole cent, with exact halves rounded away from zero", "clause 3.1 rounding")
    _require(c31, r"Rounding is applied after each individual step of the calculation, not once at the end",
             "clause 3.1 rounding frequency")
    c32 = _clause(s3, "3.2")
    stages = re.findall(r"\(([a-e])\)\s*([^;.]+?)(?:;|\.)", c32)
    order = tuple(_ORDER_PHRASES.get(re.sub(r"^and\s+", "", s.strip()), "?") for _, s in stages)
    if order != EXPECTED_PRICING_ORDER:
        raise ContractParseError(f"clause 3.2 pricing order not recognised: {stages!r}")
    _require(c32, r"The line total is then the resulting unit rate multiplied by the billed quantity",
             "clause 3.2 line total")
    c33 = _clause(s3, "3.3")
    _require(c33, r"the invoice total is the sum of the line totals", "clause 3.3 invoice total")

    # -- Appendix B: the original rates -------------------------------------------
    b1 = _section(appendix, "B.1 Rates")
    _require(b1, r"apply from the commencement of the term", "B.1 commencement")
    _require(b1, r"the rate in this Appendix continues to apply to Service Dates before the amendment's effective "
                 r"date", "B.1 pre-amendment rates")
    services: dict[str, H3Service] = {}
    appendix_caps: dict[str, tuple[int, str, str]] = {}
    for (name, basis_text, rate_text, cap_text), line in _table(
            b1, "Appendix B.1", ("Service", "Unit basis", "Rate", "Daily cap")):
        if name in services:
            raise ContractParseError(f"Appendix B.1: duplicate service {name!r}")
        basis = _basis(basis_text, f"Appendix B.1 {name!r}")
        words = name.split()
        if len(words) < 3:
            raise ContractParseError(f"Appendix B.1: {name!r} is not <qualifier> <specialty> <concept>")
        entry = RateEntry(name, _gbp(rate_text, f"Appendix B.1 {name!r}"), APPENDIX_B, "B.1", None, None, line)
        services[name] = H3Service(
            name=name, qualifier=words[0].lower(), specialty=words[1].lower(), concept=" ".join(words[2:]).lower(),
            unit_basis=basis, unit_basis_text=basis_text, compound_unit_basis=basis in COMPOUND_BASES,
            rates=(entry,), contracted_from=None, listed_in=APPENDIX_B, source_text=line)
        if cap_text != "—":
            qty, noun = _quantity(cap_text, f"Appendix B.1 cap {name!r}")
            if noun != basis:
                raise ContractParseError(f"Appendix B.1: cap for {name!r} is stated in the wrong unit ({cap_text!r})")
            appendix_caps[name] = (qty, cap_text, line)
    _require(_clause(_section(appendix, "B.2 Application"), "B.2.1"), r"A rate in this Appendix is a base rate",
             "B.2.1")

    # -- Amendment No. 1 ------------------------------------------------------------
    a11 = _section(amendment, "A1.1 Effective Date")
    effective = _long_date(_require(_clause(a11, "A1.1.1"), r"takes effect on (\d{1,2} [A-Za-z]+ \d{4})",
                                    "A1.1.1 effective date").group(1))
    amendment_date = _dt.date.fromisoformat(effective)
    if not term_start < amendment_date <= term_end:
        raise ContractParseError(f"A1.1.1: effective date {effective} is not inside the term")
    a112 = _clause(a11, "A1.1.2")
    _require(a112, r"applies \*\*by Service Date\*\*", "A1.1.2 basis")
    _require(a112, r"Service Date falls on or after the effective date is priced under this Amendment",
             "A1.1.2 on or after")
    _require(a112, r"Service Date falls before the effective date is priced under Appendix B", "A1.1.2 before")
    _require(a112, r"The date on which an invoice is issued is irrelevant for this purpose", "A1.1.2 invoice date")
    day_before = amendment_date - _dt.timedelta(days=1)

    def spelled(d: _dt.date) -> str:
        return f"{d.day} {[k for k, v in _MONTHS.items() if v == d.month][0].capitalize()} {d.year}"

    a12 = _section(amendment, "A1.2 Substituted Rates")
    _require(a12, r"substituted for the corresponding rates in Appendix B", "A1.2 substitution")
    substituted = _table(a12, "A1.2", ("Service", "Unit basis", f"Rate to {spelled(day_before)}",
                                       f"Rate from {spelled(amendment_date)}"))
    for (name, basis_text, before_text, after_text), line in substituted:
        if name not in services:
            raise ContractParseError(f"A1.2 substitutes a rate for {name!r}, which Appendix B does not carry")
        s = services[name]
        if s.listed_in != APPENDIX_B or len(s.rates) != 1:
            raise ContractParseError(f"A1.2: {name!r} is substituted twice")
        if _basis(basis_text, f"A1.2 {name!r}") != s.unit_basis:
            raise ContractParseError(f"A1.2: unit basis for {name!r} differs from Appendix B")
        before = _gbp(before_text, f"A1.2 {name!r}")
        if before != s.rates[0].rate_cents:
            # Precedence would let the amendment's figure win, but a
            # disagreement about the *old* rate means one of the documents is
            # misread; refuse rather than pick.
            raise ContractParseError(f"A1.2: rate to {day_before} for {name!r} ({before}) differs from Appendix B "
                                     f"({s.rates[0].rate_cents})")
        new = RateEntry(name, _gbp(after_text, f"A1.2 {name!r}"), AMENDMENT_1, "A1.2", amendment_date, None, line)
        services[name] = H3Service(**{**asdict(s), "rates": (s.rates[0], new)})

    a13 = _section(amendment, "A1.3 Additional Services")
    _require(a13, rf"become billable in respect of Service Dates on or after {re.escape(spelled(amendment_date))}",
             "A1.3 from")
    _require(a13, r"not contracted before that date and are not billable in respect of earlier Service Dates",
             "A1.3 before")
    for (name, basis_text, rate_text), line in _table(a13, "A1.3", ("Service", "Unit basis", "Rate")):
        if name in services:
            raise ContractParseError(f"A1.3 adds {name!r}, which is already contracted")
        words = name.split()
        if len(words) < 3:
            raise ContractParseError(f"A1.3: {name!r} is not <qualifier> <specialty> <concept>")
        basis = _basis(basis_text, f"A1.3 {name!r}")
        entry = RateEntry(name, _gbp(rate_text, f"A1.3 {name!r}"), AMENDMENT_1, "A1.3", amendment_date, None, line)
        services[name] = H3Service(
            name=name, qualifier=words[0].lower(), specialty=words[1].lower(), concept=" ".join(words[2:]).lower(),
            unit_basis=basis, unit_basis_text=basis_text, compound_unit_basis=basis in COMPOUND_BASES,
            rates=(entry,), contracted_from=amendment_date, listed_in=AMENDMENT_1, source_text=line)
    a14 = _section(amendment, "A1.4 Other Terms Unaffected")
    a141 = _clause(a14, "A1.4.1")
    _require(a141, r"the premiums, caps, bundles, exclusion windows and calculation conventions in the Base Agreement "
                   r"are unchanged, and apply to the substituted rates", "A1.4.1")
    a142 = _clause(a14, "A1.4.2")
    _check_vocabularies(services)
    appendix_services = {n for n, s in services.items() if s.listed_in == APPENDIX_B}

    def known(name: str, context: str) -> str:
        # Base Agreement rules predate the amendment; they may name only
        # Appendix B services.  (A rule naming an added service would need a
        # reading of how it applies before 2025 -- refuse rather than guess.)
        if name not in appendix_services:
            raise ContractParseError(f"{context}: unknown service {name!r}")
        return name

    def check_noun(noun_basis: str, service: str, context: str) -> None:
        if noun_basis != services[service].unit_basis:
            raise ContractParseError(f"{context}: quantity for {service!r} is stated in the wrong unit "
                                     f"({noun_basis} vs {services[service].unit_basis})")

    # -- Section 4: threshold premiums ----------------------------------------------
    s4 = _section(base, "4. Threshold Premiums")
    c41 = _clause(s4, "4.1")
    _require(c41, r"assessed against the aggregate quantity of the Service delivered to the Patient on the "
                  r"Service Day", "clause 4.1 aggregate")
    premiums: dict[str, ThresholdPremium] = {}
    for (name, when, uplift), line in _table(s4, "Section 4", ("Service", "Daily quantity threshold", "Uplift")):
        known(name, "Section 4")
        if name in premiums:
            raise ContractParseError(f"Section 4: duplicate service {name!r}")
        qty, noun = _quantity(when, f"Section 4 {name!r}", prefix=r"more than\s+")
        check_noun(noun, name, "Section 4")
        premiums[name] = ThresholdPremium(name, qty, _percent(uplift, f"Section 4 {name!r}", signed=True), "4.1", line)

    # -- Section 5: non-business-day uplifts ------------------------------------------
    s5 = _section(base, "5. Non-Business-Day Uplifts")
    nbd: dict[str, NonBusinessDayUplift] = {}
    for (name, uplift), line in _table(s5, "Section 5", ("Service", "Uplift")):
        known(name, "Section 5")
        if name in nbd:
            raise ContractParseError(f"Section 5: duplicate service {name!r}")
        nbd[name] = NonBusinessDayUplift(name, _percent(uplift, f"Section 5 {name!r}", signed=True), "5", line)

    # -- Section 6: cumulative volume discounts -----------------------------------------
    s6 = _section(base, "6. Cumulative Volume Discounts")
    c61 = _clause(s6, "6.1")
    _require(c61, r"counted cumulatively across the whole term of this Agreement and aggregated across all "
                  r"Patients, in Service Date order, up to but excluding the line item being priced",
             "clause 6.1 prior-exclusive")
    _require(c61, r"where two line items share a Service Date they are counted in ascending order of line "
                  r"identifier", "clause 6.1 ordering")
    _require(c61, r"Where two thresholds are met the deeper discount applies", "clause 6.1 deeper discount")
    discounts: dict[str, list[VolumeDiscountTier]] = {}
    for (name, exceeds, pct), line in _table(s6, "Section 6", ("Service", "Cumulative utilisation", "Discount")):
        known(name, "Section 6")
        qty, noun = _quantity(exceeds, f"Section 6 {name!r}", prefix=r"more than\s+")
        check_noun(noun, name, "Section 6")
        discounts.setdefault(name, []).append(VolumeDiscountTier(
            name, qty, _percent(pct, f"Section 6 {name!r}", signed=False), "6", line))
    for name, tiers in discounts.items():
        tiers.sort(key=lambda t: t.exceeds_units)
        if len({t.exceeds_units for t in tiers}) != len(tiers):
            raise ContractParseError(f"Section 6: {name!r} repeats a threshold")
        for lo, hi in zip(tiers, tiers[1:]):
            if hi.discount_fraction <= lo.discount_fraction:
                # 6.1: "the deeper discount applies".  A higher threshold that
                # is not deeper would make that sentence self-contradictory.
                raise ContractParseError(f"Section 6: {name!r} tiers are not monotonically deeper")

    # -- Section 7: daily caps, cross-checked against the Appendix B column ---------------
    s7 = _section(base, "7. Daily Quantity Caps")
    caps: dict[str, DailyCap] = {}
    for (name, limit), line in _table(s7, "Section 7", ("Service", "Maximum units per Patient per Service Day")):
        known(name, "Section 7")
        if name in caps:
            raise ContractParseError(f"Section 7: duplicate service {name!r}")
        qty, noun = _quantity(limit, f"Section 7 {name!r}")
        check_noun(noun, name, "Section 7")
        caps[name] = DailyCap(name, qty, "7", line)
    if {n: c.max_units for n, c in caps.items()} != {n: v[0] for n, v in appendix_caps.items()}:
        raise ContractParseError("the Base Agreement Section 7 caps and the Appendix B 'Daily cap' column disagree")

    # -- Section 8: bundles ---------------------------------------------------------------
    s8 = _section(base, "8. Bundled Services")
    c81 = _clause(s8, "8.1")
    _require(c81, r"replace the Appendix B rates for both Services whenever the pair is delivered to the same "
                  r"Patient on the same Service Day, and are substituted before any premium or discount", "clause 8.1")
    bundles: list[Bundle] = []
    bundled: set[str] = set()
    for (a, b, rate_a, rate_b), line in _table(
            s8, "Section 8", ("Service A", "Service B", "Bundled rate A", "Bundled rate B")):
        for n in (known(a, "Section 8"), known(b, "Section 8")):
            if n in bundled:
                # The engine substitutes at most one bundled rate per line.
                raise ContractParseError(f"Section 8: {n!r} appears in more than one bundle")
            bundled.add(n)
        bundles.append(Bundle(a, b, _gbp(rate_a, f"Section 8 {a!r}"), _gbp(rate_b, f"Section 8 {b!r}"), "8.1", line))

    # -- Section 9: exclusion windows ---------------------------------------------------------
    s9 = _section(base, "9. Exclusion Windows")
    exclusions: list[ExclusionWindow] = []
    for (name, window, other), line in _table(s9, "Section 9", ("Service", "Not billable within", "Of this Service")):
        known(name, "Section 9")
        known(other, "Section 9")
        m = re.fullmatch(r"(\d+)\s+days?", window.strip())
        if not m:
            raise ContractParseError(f"Section 9: unparseable window {window!r} for {name!r}")
        exclusions.append(ExclusionWindow(name, int(m.group(1)), other, "9", line))
    if re.search(r"either direction|after|following|before|prior to", s9, flags=re.I):
        # The engine's reading of "within" (audit.AuditPolicy) assumes the
        # section says nothing about direction.  If it ever does, re-read it.
        raise ContractParseError("Section 9 now states a direction; the exclusion reading must be revisited")

    # -- Section 10: invoicing ------------------------------------------------------------------
    s10 = _section(base, "10. Invoicing")
    invoice_clauses = {cid: _clause(s10, cid) for cid in ("10.1", "10.2", "10.3")}
    _require(invoice_clauses["10.1"], r"quotes the contract number", "clause 10.1 contract number")
    _require(invoice_clauses["10.1"], r"invoice number unique across the term", "clause 10.1 unique number")
    _require(invoice_clauses["10.2"], r"Every Service Date falls within the term and may not fall after the invoice "
                                      r"date", "clause 10.2")
    _require(invoice_clauses["10.3"], r"same Service may not be billed twice for the same Patient and Service Date, "
                                      r"whether on one invoice or across several", "clause 10.3")

    for name in set(premiums) & set(nbd):
        # Both are stage (d) of clause 3.2 and the agreement never says how
        # they combine.  Refuse rather than let code order decide.
        raise ContractParseError(f"{name!r} carries both a threshold premium and a non-business-day uplift")

    source_sha = {doc: hashlib.sha256(raw).hexdigest() for doc, raw in raws.items()}
    combined = ":".join(f"{d}={source_sha[d]}" for d in sorted(source_sha))
    contract = H3Contract(
        contract_number=meta["Contract number"],
        provider=meta["Provider"],
        payer=meta["Payer"],
        effective_from=effective_from,
        effective_to=effective_to,
        currency=meta["Currency"],
        rounding_convention=meta["Rounding convention"],
        precedence=precedence,
        amendment_effective=effective,
        amendment_basis="service_date",
        facility_code=facility,
        facility_multiplier=Decimal(1),
        plan_tier_multiplier=Decimal(1),
        non_business_weekdays=non_business,
        pricing_order=order,
        services=services,
        daily_caps=caps,
        threshold_premiums=premiums,
        non_business_day_uplifts=nbd,
        bundles=bundles,
        volume_discounts=discounts,
        exclusion_windows=exclusions,
        invoice_clauses=invoice_clauses,
        conventions={
            "base 1.3": c13, "base 1.4": c14, "base 1.5": c15, "base 2.1": c21, "base 2.2": _clause(s2, "2.2"),
            "base 3.1": c31, "base 3.2": c32, "base 3.3": c33, "base 4.1": c41, "base 6.1": c61, "base 8.1": c81,
            "amendment A1.1.2": a112, "amendment A1.4.1": a141, "amendment A1.4.2": a142,
        },
        fingerprint=hashlib.sha256(f"{PARSER_VERSION}:{combined}".encode()).hexdigest(),
        source_sha256=source_sha,
        warnings=warnings,
    )
    _validate(contract)
    return contract


def _check_vocabularies(services: dict[str, H3Service]) -> None:
    """The matcher reads a name as three slots; that needs disjoint vocabularies."""
    qualifiers = {s.qualifier for s in services.values()}
    specialties = {s.specialty for s in services.values()}
    concept_words = {w for s in services.values() for w in s.concept.split()}
    for a, b, label in ((qualifiers, specialties, "qualifier/specialty"),
                        (qualifiers, concept_words, "qualifier/concept"),
                        (specialties, concept_words, "specialty/concept")):
        if a & b:
            raise ContractParseError(f"service-name vocabularies overlap ({label}): {sorted(a & b)}")


def _validate(c: H3Contract) -> None:
    """Invariants over the parsed rule set, to catch a silently truncated parse."""
    for family, coll in (("threshold premiums", c.threshold_premiums), ("daily caps", c.daily_caps),
                         ("non-business-day uplifts", c.non_business_day_uplifts), ("bundles", c.bundles),
                         ("cumulative discounts", c.volume_discounts), ("exclusion windows", c.exclusion_windows)):
        if not coll:
            raise ContractParseError(f"{family}: section present but nothing parsed")
    amended = [n for n in c.services if c.is_amended(n)]
    added = [n for n in c.services if c.is_added(n)]
    if not amended or not added:
        raise ContractParseError("Amendment No. 1: substitutions or additions missing")
    for name in c.services:
        # Every service has exactly one rate on every in-term date on which it
        # is contracted; ``rate_entry_on`` raises on a precedence tie.
        for d in (c.amendment_date - _dt.timedelta(days=1), c.amendment_date):
            c.rate_entry_on(name, d)


# ==========================================================================
# 5. Plain-text rendering cross-check
# ==========================================================================

_TXT = {BASE: CONTRACT_DIR / "base_agreement.txt", APPENDIX_B: CONTRACT_DIR / "appendix_b_rate_schedule.txt",
        AMENDMENT_1: CONTRACT_DIR / "amendment_no_1.txt"}


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


def cross_check_text_rendering(c: H3Contract) -> list[str]:
    """Compare the rate tables with the plain-text rendering of the same documents.

    The Markdown files are the ones parsed; the plain text is the same package
    laid out differently.  Any disagreement is reported rather than resolved.
    """
    problems = []
    appendix = _txt_rows(_TXT[APPENDIX_B].read_text(encoding="utf-8"), "B.1 RATES", "Service")
    b_services = [s for s in c.services.values() if s.listed_in == APPENDIX_B]
    if len(appendix) != len(b_services):
        problems.append(f"Appendix B: txt has {len(appendix)} rows, md has {len(b_services)}")
    for row in appendix:
        s = c.services.get(row[0]) if len(row) == 4 else None
        if s is None:
            problems.append(f"Appendix B txt row not understood: {row}")
            continue
        cap = c.daily_caps.get(s.name)
        if (row[1], _gbp(row[2], "txt"), row[3]) != (s.unit_basis_text, s.rates[0].rate_cents,
                                                     "—" if cap is None else row[3]):
            problems.append(f"Appendix B txt disagrees for {s.name!r}: {row}")
        if cap is not None and _quantity(row[3], "txt")[0] != cap.max_units:
            problems.append(f"Appendix B txt cap disagrees for {s.name!r}: {row[3]}")
    amend = _TXT[AMENDMENT_1].read_text(encoding="utf-8")
    for row in _txt_rows(amend, "A1.2 SUBSTITUTED RATES", "Service"):
        s = c.services.get(row[0]) if len(row) == 4 else None
        new = next((e for e in s.rates if e.document == AMENDMENT_1), None) if s else None
        if new is None or (_gbp(row[2], "txt"), _gbp(row[3], "txt")) != (s.rates[0].rate_cents, new.rate_cents):
            problems.append(f"A1.2 txt disagrees: {row}")
    for row in _txt_rows(amend, "A1.3 ADDITIONAL SERVICES", "Service"):
        s = c.services.get(row[0]) if len(row) == 3 else None
        if s is None or not c.is_added(s.name) or (row[1], _gbp(row[2], "txt")) != (s.unit_basis_text,
                                                                                    s.rates[0].rate_cents):
            problems.append(f"A1.3 txt disagrees: {row}")
    return problems


# ==========================================================================
# 6. Serialisation
# ==========================================================================

def _jsonable(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (_dt.date,)):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return _jsonable(asdict(obj))
    return obj


def rule_counts(c: H3Contract) -> dict[str, int]:
    return {
        "services": len(c.services),
        "appendix_b_services": sum(s.listed_in == APPENDIX_B for s in c.services.values()),
        "amended_rates": sum(c.is_amended(n) for n in c.services),
        "added_services": sum(c.is_added(n) for n in c.services),
        "daily_caps": len(c.daily_caps),
        "threshold_premiums": len(c.threshold_premiums),
        "non_business_day_uplifts": len(c.non_business_day_uplifts),
        "bundle_pairs": len(c.bundles),
        "cumulative_discount_services": len(c.volume_discounts),
        "cumulative_discount_tiers": sum(len(v) for v in c.volume_discounts.values()),
        "exclusion_windows": len(c.exclusion_windows),
        "compound_unit_bases": sum(s.compound_unit_basis for s in c.services.values()),
    }


def write_contract_rules(c: H3Contract, path: Path) -> None:
    doc = {
        "fingerprint": c.fingerprint,
        "sources": {d: str(p.relative_to(REPO_ROOT)).replace("\\", "/")
                    for d, p in ((BASE, BASE_PATH), (APPENDIX_B, APPENDIX_PATH), (AMENDMENT_1, AMENDMENT_PATH))},
        "source_sha256": c.source_sha256,
        "parser_version": PARSER_VERSION,
        "counts": rule_counts(c),
        "text_rendering_disagreements": cross_check_text_rendering(c),
        **{k: _jsonable(v) for k, v in c.__dict__.items() if k not in ("fingerprint", "source_sha256")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
