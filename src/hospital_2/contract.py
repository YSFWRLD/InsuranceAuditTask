"""Hospital 2: the Master Services and Reimbursement Agreement as structured rules.

The agreement has no tables.  Each of its 76 services is described in one
numbered clause ("In respect of <Service>, the Provider shall invoice ...")
scattered across thirteen "Contracted Services" Articles, between Articles of
unrelated boilerplate.  Every sentence of every service clause is one of a
closed set of templates, so the parser recognises each sentence explicitly and
**raises on any sentence it does not recognise**.  A clause that cannot be
read completely is never read as "no rule".

Contents
    1. Rule types
    2. Vocabulary        unit-basis prose -> invoice tokens; numbers in words
    3. Parser            text -> H2Contract, with cross-validation
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
CONTRACT_PATH = REPO_ROOT / "contracts" / "hospital_2" / "master_services_agreement.md"

#: Bumped whenever the parser's reading of the contract could change.  Part of
#: the fingerprint, so persisted semantic mappings are invalidated with it.
PARSER_VERSION = "h2-contract-1"


class ContractParseError(Exception):
    """A clause that should carry a rule could not be read completely."""


# ==========================================================================
# 1. Rule types
# ==========================================================================

@dataclass(frozen=True)
class H2Service:
    clause_id: str
    article: str
    name: str
    #: The three name slots.  Every Hospital 2 service name is
    #: <qualifier> <specialty> <service type>; the parser verifies the three
    #: vocabularies are disjoint before relying on that.
    qualifier: str
    specialty: str
    service_type: str
    base_rate_cents: int
    #: The billed-token form of the contractual unit basis ("per_night").
    unit_basis: str
    #: The contract's own words ("per night of occupancy").
    unit_basis_text: str
    #: True only for a basis the contract states as a compound ("per hour, per
    #: item", clause 4.2).  Not collapsed into either component.
    compound_unit_basis: bool
    #: "must be expressed on the unit basis stated in this clause and on no
    #: other basis"
    basis_exclusive: bool
    #: "presents this Service on a unit basis other than the one stated ...
    #: shall be treated as incorrectly presented and returned to the Provider"
    basis_mismatch_returned: bool
    source_text: str


@dataclass(frozen=True)
class NonBusinessDayUplift:
    service: str
    uplift_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class DailyAggregateUplift:
    """Clause 3.4: assessed on the patient's aggregate quantity for the Service Day."""

    service: str
    exceeds_units: int
    uplift_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class VolumeDiscountTier:
    service: str
    exceeds_units: int
    discount_fraction: Decimal
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class DailyCap:
    service: str
    max_units: int
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class Bundle:
    service_a: str
    service_b: str
    rate_a_cents: int
    rate_b_cents: int
    clause_ids: tuple[str, str]
    source_text: str


@dataclass(frozen=True)
class ExclusionWindow:
    """``service`` is not billable within ``days`` of ``other_service`` (clause 3.6: either direction)."""

    service: str
    days: int
    other_service: str
    clause_id: str
    source_text: str


@dataclass(frozen=True)
class InvoiceRules:
    """Article XIII."""

    submission_days_after_discharge: int
    unique_invoice_number: bool
    must_quote_contract_number: bool
    source_clauses: dict[str, str]


@dataclass
class H2Contract:
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
    services: dict[str, H2Service]
    non_business_day_uplifts: dict[str, NonBusinessDayUplift]
    daily_aggregate_uplifts: dict[str, DailyAggregateUplift]
    volume_discounts: dict[str, list[VolumeDiscountTier]]
    daily_caps: dict[str, DailyCap]
    bundles: list[Bundle]
    exclusion_windows: list[ExclusionWindow]
    invoice_rules: InvoiceRules
    conventions: dict[str, str]
    fingerprint: str
    source_sha256: str
    warnings: list[str] = field(default_factory=list)

    def bundle_partner(self, service: str) -> Optional[tuple[str, int]]:
        """``(partner, this service's bundled rate)``."""
        for b in self.bundles:
            if b.service_a == service:
                return b.service_b, b.rate_a_cents
            if b.service_b == service:
                return b.service_a, b.rate_b_cents
        return None

    def exclusions_for(self, service: str) -> list[ExclusionWindow]:
        return [e for e in self.exclusion_windows if e.service == service]

    def uplift_for(self, service: str):
        return self.daily_aggregate_uplifts.get(service) or self.non_business_day_uplifts.get(service)

    def clause_ids_for(self, service: str) -> list[str]:
        ids = [self.services[service].clause_id]
        partner = self.bundle_partner(service)
        if partner:
            ids.append(self.services[partner[0]].clause_id)
        for e in self.exclusions_for(service):
            ids.append(self.services[e.other_service].clause_id)
        return list(dict.fromkeys(ids))


# ==========================================================================
# 2. Vocabulary
# ==========================================================================

#: Contract prose -> the unit-basis token invoices use.  The compound basis of
#: clause 4.2 is its own token, observed on invoices as ``per_hour_per_item``.
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

#: The noun a cap/threshold uses for a unit on each basis.
UNIT_NOUNS = {
    "hours": "per_hour", "days": "per_day", "nights": "per_night",
    "visits": "per_visit", "tests": "per_test", "procedures": "per_procedure",
    "items": "per_item", "units": "per_unit_dispensed",
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
    """'one hundred and eighty' -> 180.  Raises on anything unrecognised."""
    total = 0
    current = 0
    for token in re.split(r"[\s-]+", words.strip().lower()):
        if token == "and" or not token:
            continue
        if token in _ONES:
            current += _ONES[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token == "hundred":
            current = (current or 1) * 100
        elif token == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            raise ContractParseError(f"unrecognised number word {token!r} in {words!r}")
    return total + current


def _number(words: str, digits: str, context: str) -> int:
    """The contract states quantities twice -- "twelve (12)".  Both must agree."""
    value = int(digits)
    if words_to_int(words) != value:
        raise ContractParseError(f"{context}: {words!r} does not equal ({digits})")
    return value


def _percent(words: str, digits: str, context: str) -> Decimal:
    value = _number(words, digits, context)
    return Decimal(value) / Decimal(100)


def _gbp(text: str, context: str) -> int:
    m = re.fullmatch(r"GBP ([\d,]+\.\d{2})", text.strip())
    if not m:
        raise ContractParseError(f"{context}: unparseable amount {text!r}")
    return decimal_to_cents(m.group(1).replace(",", ""))


def _basis(text: str, context: str) -> str:
    token = UNIT_BASIS_TEXT.get(text.strip())
    if token is None:
        raise ContractParseError(f"{context}: unknown unit basis {text!r}")
    return token


# ==========================================================================
# 3. Parser
# ==========================================================================

_NUM = r"([a-z]+(?:[ -][a-z]+)*) \((\d+)\)"
_PCT = r"([a-z]+(?:[ -][a-z]+)*) percent \((\d+)%\)"
_BASIS = r"(per hour, per item|per [a-z ]+?)"
_SVC = r"([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)+)"

#: Sentences that carry no pricing rule but whose presence is recorded.
_FLAG_SENTENCES = {
    "basis_exclusive": "Any quantity billed for this Service must be expressed on the unit basis stated in this clause and on no other basis.",
    "basis_mismatch_returned": "Where an invoice presents this Service on a unit basis other than the one stated in this clause, the line item shall be treated as incorrectly presented and shall be returned to the Provider.",
    "rate_follows_service": "The description applied by the Provider on any invoice shall not of itself vary the rate applicable to this Service; the rate follows the Service actually delivered.",
    "inclusive": "The rate stated in this clause is inclusive of all consumables, staffing and overhead attributable to the Service, and no separate charge shall be raised in respect of them.",
    "demonstrable": "The Provider shall be able to demonstrate, from its clinical record, the quantity billed for this Service on any Service Day.",
}

_RE_RATE = re.compile(
    rf"In respect of {_SVC}, the Provider shall invoice the Payer at the rate of "
    rf"(GBP [\d,]+\.\d{{2}}) {_BASIS}\."
)
_RE_PRESENT = re.compile(
    rf"The Provider shall present this Service on its invoices under a description "
    rf"sufficient to identify it as {_SVC}, and shall not present it under a "
    rf"description that identifies a different contracted Service\."
)
_RE_NBD = re.compile(
    rf"Where the Service Date of this Service does not fall on a Business Day, the "
    rf"rate applicable to it shall be increased by {_PCT}\."
)
_RE_DAILY = re.compile(
    rf"Where the aggregate quantity of this Service delivered to a Patient on a "
    rf"single Service Day exceeds {_NUM} ([a-z]+), the rate applicable to that "
    rf"Service Day shall be increased by {_PCT}\."
)
_RE_CUMULATIVE = re.compile(
    rf"Where cumulative utilisation of this Service exceeds {_NUM} ([a-z]+), counted "
    rf"cumulatively across the whole term of this Agreement and aggregated across "
    rf"all Patients, a discount of {_PCT} shall be applied to each subsequent Unit\."
)
_RE_CAP = re.compile(
    rf"The Provider shall not bill more than {_NUM} ([a-z]+) of this Service for a "
    rf"Patient on a single Service Day\."
)
_RE_BUNDLE = re.compile(
    rf"Where this Service and {_SVC} are both delivered to the same Patient on the "
    rf"same Service Day, the two shall be billed as a bundle, this Service at "
    rf"(GBP [\d,]+\.\d{{2}}) {_BASIS} and {_SVC} at (GBP [\d,]+\.\d{{2}}) {_BASIS}, "
    rf"in substitution for their standalone rates\."
)
_RE_EXCLUSION = re.compile(
    rf"This Service is not billable where {_SVC} has been delivered to the same "
    rf"Patient within {_NUM} days of the Service Date\."
)


def _sentences(clause_text: str) -> list[str]:
    # Split on ". " before a capital, but never inside "GBP 1,204.75".
    return [s.strip() for s in re.split(r"(?<=\.)\s+(?=[A-Z])", clause_text) if s.strip()]


def _meta(text: str, label: str) -> str:
    m = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text, flags=re.M)
    if not m:
        raise ContractParseError(f"contract metadata missing: {label}")
    return m.group(1)


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def _long_date(text: str) -> str:
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) (\d{4})", text.strip())
    if not m or m.group(2).lower() not in _MONTHS:
        raise ContractParseError(f"unparseable date {text!r}")
    return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def _require(text: str, needle: str, what: str) -> str:
    """Return the numbered clause containing ``needle``; raise if absent.

    Used for the Article III conventions the engine implements: if the
    contract stopped saying them, the engine would be pricing to rules the
    contract no longer contains.
    """
    for line in text.splitlines():
        if needle in line:
            return line.strip()
    raise ContractParseError(f"{what}: expected wording not found: {needle!r}")


def parse_contract(path: Path = CONTRACT_PATH) -> H2Contract:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8")
    source_sha = hashlib.sha256(raw).hexdigest()
    warnings: list[str] = []

    rounding = _meta(text, "Rounding convention")
    if rounding != "half_up_cent":
        raise ContractParseError(f"unsupported rounding convention {rounding!r}")

    # -- Article I: facility and plan tier ----------------------------------
    c13 = _require(text, "No facility differential applies", "clause 1.3")
    m = re.search(r"from Main Campus \(([A-Z-]+)\)", c13)
    if not m:
        raise ContractParseError("clause 1.3: facility code not found")
    facility_code = m.group(1)
    if "irrespective of the Patient's plan tier" not in c13:
        raise ContractParseError("clause 1.3: plan-tier wording not recognised")

    # -- Article III: conventions the engine implements ---------------------
    conventions = {
        "3.1_rounding": _require(text, "Rounding is applied after each individual step", "clause 3.1"),
        "3.2_order": _require(text, "(a) substitution of a bundled rate; (b) the facility multiplier; (c) the plan-tier multiplier; (d) any premium or uplift; and (e) any cumulative volume discount", "clause 3.2"),
        "3.3_totals": _require(text, "The invoice total is the arithmetic sum of the line totals", "clause 3.3"),
        "3.4_daily_aggregate": _require(text, "assessed against the aggregate quantity of the Service delivered to the Patient on that Service Day", "clause 3.4"),
        "3.5_cumulative": _require(text, "where two line items share a Service Date they are counted in ascending order of line identifier", "clause 3.5"),
        "3.5_no_compounding": _require(text, "the deeper discount applies and the discounts are not compounded", "clause 3.5"),
        "3.6_exclusion_direction": _require(text, "An exclusion window is measured in either direction", "clause 3.6"),
        "2.2_service_day": _require(text, '"Service Day" means', "clause 2.2"),
        "2.4_business_day": _require(text, '"Business Day" means a Service Day which commences on a day other than a Saturday or a Sunday', "clause 2.4"),
        "2.7_cumulative_utilisation": _require(text, "up to but excluding the line item being priced", "clause 2.7"),
    }

    # -- Article XIII: invoice submission -----------------------------------
    c131 = _require(text, "shall be submitted no later than", "clause 13.1")
    m = re.search(rf"no later than {_NUM} days after the discharge date", c131)
    if not m:
        raise ContractParseError("clause 13.1: submission deadline not parsed")
    invoice_rules = InvoiceRules(
        submission_days_after_discharge=_number(m.group(1), m.group(2), "clause 13.1"),
        unique_invoice_number=True,
        must_quote_contract_number=True,
        source_clauses={
            "13.1": c131,
            "13.6": _require(text, "identified by an invoice number unique across the whole term", "clause 13.6"),
            "13.11": _require(text, "quoting the contract number recorded on the face of this Agreement", "clause 13.11"),
        },
    )

    # -- Service clauses -----------------------------------------------------
    article = None
    clause_rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        h = re.match(r"^## (Article [IVXLC]+) — (.+)$", line)
        if h:
            article = f"{h.group(1)} — {h.group(2)}"
            continue
        c = re.match(r"^(\d+\.\d+) (In respect of .+)$", line)
        if c:
            if article is None or "Contracted Services" not in article:
                raise ContractParseError(f"clause {c.group(1)}: service clause outside a Contracted Services Article")
            clause_rows.append((c.group(1), article, c.group(2)))
    if not clause_rows:
        raise ContractParseError("no service clauses found")

    services: dict[str, H2Service] = {}
    nbd: dict[str, NonBusinessDayUplift] = {}
    daily: dict[str, DailyAggregateUplift] = {}
    discounts: dict[str, list[VolumeDiscountTier]] = {}
    caps: dict[str, DailyCap] = {}
    bundle_halves: list[tuple] = []
    exclusions: list[ExclusionWindow] = []

    for clause_id, art, clause_text in clause_rows:
        ctx = f"clause {clause_id}"
        sentences = _sentences(clause_text)
        m = _RE_RATE.fullmatch(sentences[0])
        if not m:
            raise ContractParseError(f"{ctx}: rate sentence not recognised: {sentences[0]!r}")
        name, rate_text, basis_text = m.group(1), m.group(2), m.group(3)
        if name in services:
            raise ContractParseError(f"{ctx}: duplicate service {name!r}")
        basis = _basis(basis_text, ctx)
        flags = {k: False for k in _FLAG_SENTENCES}
        presented_as = None
        for s in sentences[1:]:
            matched_flag = next((k for k, v in _FLAG_SENTENCES.items() if s == v), None)
            if matched_flag:
                flags[matched_flag] = True
                continue
            if (p := _RE_PRESENT.fullmatch(s)):
                presented_as = p.group(1)
            elif (p := _RE_NBD.fullmatch(s)):
                nbd[name] = NonBusinessDayUplift(name, _percent(p.group(1), p.group(2), ctx), clause_id, s)
            elif (p := _RE_DAILY.fullmatch(s)):
                _check_noun(p.group(3), basis, ctx)
                daily[name] = DailyAggregateUplift(
                    name, _number(p.group(1), p.group(2), ctx),
                    _percent(p.group(4), p.group(5), ctx), clause_id, s)
            elif (p := _RE_CUMULATIVE.fullmatch(s)):
                _check_noun(p.group(3), basis, ctx)
                discounts.setdefault(name, []).append(VolumeDiscountTier(
                    name, _number(p.group(1), p.group(2), ctx),
                    _percent(p.group(4), p.group(5), ctx), clause_id, s))
            elif (p := _RE_CAP.fullmatch(s)):
                _check_noun(p.group(3), basis, ctx)
                caps[name] = DailyCap(name, _number(p.group(1), p.group(2), ctx), clause_id, s)
            elif (p := _RE_BUNDLE.fullmatch(s)):
                bundle_halves.append((clause_id, name, basis, p, s))
            elif (p := _RE_EXCLUSION.fullmatch(s)):
                exclusions.append(ExclusionWindow(
                    name, _number(p.group(2), p.group(3), ctx), p.group(1), clause_id, s))
            else:
                raise ContractParseError(f"{ctx}: unrecognised sentence: {s!r}")
        if presented_as != name:
            raise ContractParseError(f"{ctx}: description-presentation sentence missing or names {presented_as!r}")

        words = name.split()
        services[name] = H2Service(
            clause_id=clause_id, article=art, name=name,
            qualifier=words[0], specialty=words[1], service_type=" ".join(words[2:]),
            base_rate_cents=_gbp(rate_text, ctx), unit_basis=basis,
            unit_basis_text=basis_text, compound_unit_basis=basis in COMPOUND_BASES,
            basis_exclusive=flags["basis_exclusive"],
            basis_mismatch_returned=flags["basis_mismatch_returned"],
            source_text=clause_text,
        )

    # -- Cross-validation ----------------------------------------------------
    for name, tiers in discounts.items():
        tiers.sort(key=lambda t: t.exceeds_units)
        for lo, hi in zip(tiers, tiers[1:]):
            if hi.discount_fraction <= lo.discount_fraction:
                raise ContractParseError(f"{name}: discount tiers are not monotonically deeper")

    for rule_service in [e.other_service for e in exclusions]:
        if rule_service not in services:
            raise ContractParseError(f"exclusion window names unknown service {rule_service!r}")

    bundles = _pair_bundles(bundle_halves, services)

    # The three name slots must be disjoint vocabularies before the matcher
    # may treat "the second word is the specialty" as structure.
    slots = [{getattr(s, a) for s in services.values()} for a in ("qualifier", "specialty")]
    type_words = {w for s in services.values() for w in s.service_type.split()}
    if slots[0] & slots[1] or slots[0] & type_words or slots[1] & type_words:
        raise ContractParseError("service-name slot vocabularies overlap")

    both = set(nbd) & set(daily)
    if both:
        # Both are stage (d) in clause 3.2; the contract does not say how they
        # combine.  Surface it rather than let code order decide.
        warnings.append(f"services carry both uplift kinds: {sorted(both)}")

    fingerprint = hashlib.sha256(f"{PARSER_VERSION}:{source_sha}".encode()).hexdigest()
    return H2Contract(
        contract_number=_meta(text, "Contract number"),
        provider=_meta(text, "Provider"),
        payer=_meta(text, "Payer"),
        effective_from=_long_date(_meta(text, "Effective from")),
        effective_to=_long_date(_meta(text, "Effective to")),
        currency=_meta(text, "Currency"),
        rounding_convention=rounding,
        facility_code=facility_code,
        facility_multiplier=Decimal(1),
        plan_tier_multiplier=Decimal(1),
        services=services,
        non_business_day_uplifts=nbd,
        daily_aggregate_uplifts=daily,
        volume_discounts=discounts,
        daily_caps=caps,
        bundles=bundles,
        exclusion_windows=exclusions,
        invoice_rules=invoice_rules,
        conventions=conventions,
        fingerprint=fingerprint,
        source_sha256=source_sha,
        warnings=warnings,
    )


def _check_noun(noun: str, basis: str, ctx: str) -> None:
    expected = UNIT_NOUNS.get(noun)
    if expected is None:
        raise ContractParseError(f"{ctx}: unknown unit noun {noun!r}")
    if expected != basis:
        raise ContractParseError(f"{ctx}: quantity stated in {noun!r} but the service is billed {basis!r}")


def _pair_bundles(halves: list[tuple], services: dict[str, H2Service]) -> list[Bundle]:
    """Each bundle is stated twice, once in each partner's clause; both must agree."""
    stated: dict[frozenset, list[tuple]] = {}
    for clause_id, name, basis, m, s in halves:
        partner = m.group(1)
        if partner not in services:
            raise ContractParseError(f"clause {clause_id}: bundle partner {partner!r} unknown")
        own_rate = _gbp(m.group(2), clause_id)
        own_basis = _basis(m.group(3), clause_id)
        named_partner = m.group(4)
        partner_rate = _gbp(m.group(5), clause_id)
        partner_basis = _basis(m.group(6), clause_id)
        if named_partner != partner:
            raise ContractParseError(f"clause {clause_id}: bundle names {partner!r} then {named_partner!r}")
        if own_basis != basis or partner_basis != services[partner].unit_basis:
            raise ContractParseError(f"clause {clause_id}: bundled rate stated on a different unit basis")
        stated.setdefault(frozenset((name, partner)), []).append(
            (clause_id, name, own_rate, partner, partner_rate, s))

    bundles: list[Bundle] = []
    for pair, statements in stated.items():
        if len(statements) != 2:
            raise ContractParseError(f"bundle {sorted(pair)} is not stated reciprocally")
        (c1, a, a_rate, b, b_rate_1, s1), (c2, b2, b_rate_2, a2, a_rate_2, s2) = sorted(statements)
        if (b2, a2) != (b, a) or a_rate != a_rate_2 or b_rate_1 != b_rate_2:
            raise ContractParseError(f"bundle {sorted(pair)}: the two clauses disagree")
        bundles.append(Bundle(a, b, a_rate, b_rate_1, (c1, c2), f"{s1} || {s2}"))
    for svc in {s for b in bundles for s in (b.service_a, b.service_b)}:
        if sum(svc in (b.service_a, b.service_b) for b in bundles) > 1:
            raise ContractParseError(f"{svc!r} appears in more than one bundle")
    return sorted(bundles, key=lambda b: b.clause_ids)


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


def rule_counts(c: H2Contract) -> dict[str, int]:
    return {
        "services": len(c.services),
        "non_business_day_uplifts": len(c.non_business_day_uplifts),
        "daily_aggregate_uplifts": len(c.daily_aggregate_uplifts),
        "cumulative_discount_services": len(c.volume_discounts),
        "cumulative_discount_tiers": sum(len(v) for v in c.volume_discounts.values()),
        "daily_caps": len(c.daily_caps),
        "exclusion_windows": len(c.exclusion_windows),
        "bundle_pairs": len(c.bundles),
        "compound_unit_bases": sum(s.compound_unit_basis for s in c.services.values()),
    }


def write_contract_rules(c: H2Contract, path: Path) -> None:
    doc = {
        "fingerprint": c.fingerprint,
        "source": str(CONTRACT_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "source_sha256": c.source_sha256,
        "parser_version": PARSER_VERSION,
        "counts": rule_counts(c),
        **{k: _jsonable(v) for k, v in c.__dict__.items()
           if k not in ("fingerprint", "source_sha256")},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
