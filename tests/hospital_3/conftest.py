"""Hospital 3 test fixtures.

Audit tests run against the **real** Hospital 3 contract package (so every
rule tested is a rule the contract states) and against **synthetic** invoices
built here.  No Hospital 3 invoice, and no label of any hospital, is used as
an expected answer.

The lexicon is the pipeline's own: Hospital 3's normalisation is
deterministic (derived from the contract and the descriptions), so there is
no reviewed vocabulary to stub.  Missing-word decisions are passed explicitly
where a test needs one; nothing here touches the network.
"""

from __future__ import annotations

import datetime as _dt
import shutil
from pathlib import Path

import pytest

from src.hospital_3.audit import AuditPolicy, LineAuditor, audit_resolved, resolve_lines
from src.hospital_3.contract import AMENDMENT_PATH, APPENDIX_PATH, BASE_PATH, parse_contract
from src.hospital_3.matcher import H3Matcher
from src.hospital_3.normalization import build_lexicon
from src.shared.data import load_occurrences
from src.shared.models import InvoiceOccurrence, LineItem

REPO = Path(__file__).resolve().parents[2]
JSONL = REPO / "invoices" / "hospital_3_invoices.jsonl"

# Real Hospital 3 services and the rules the contract gives them.
PLAIN = "Standard Orthopaedic Theatre Time"                        # 6025 per_hour; no rule
PREMIUM = "Ambulatory Renal Case Conference"                       # 8775 per_hour; > 8 hours/day +25%
WEEKEND = "Routine Vascular Rehabilitation Programme"               # 37575 per_visit; non-business day +10%
WEEKEND_AMENDED = "Specialist Psychiatric Discharge Planning"       # 38650 -> 44050 per_visit; +10%
CAPPED = "Routine Psychiatric Consultation"                         # 282975 per_procedure; cap 8
CAPPED_AMENDED = "Intensive Infectious Anaesthesia Administration"  # 294675 -> 312350; cap 4
BUNDLE_A = "Bedside Palliative Wound Care"                         # 14150 per_visit; bundled 12025
BUNDLE_B = "Intermittent Gastrointestinal Endoscopic Procedure"    # 276475 per_procedure; bundled 235000
TWO_TIER_AMENDED = "Assisted Urologic Endoscopic Procedure"         # 94250 -> 111225; >100 10%, >300 20%
ONE_TIER = "Ambulatory Ophthalmic Imaging Interpretation"          # 414850 per_procedure; >100 10%
ONE_TIER_AMENDED = "Intensive Otolaryngologic Anaesthesia Administration"  # 68025 -> 80275; >60 12%
EXCLUDED = "Preoperative Dermatologic Dialysis Session"            # 439850; not within 10 days of:
TRIGGER = "Outpatient Gastrointestinal Isolation Room Occupancy"   # 57050 per_day
EXCLUDED_AMENDED = "Intensive Ophthalmic Laboratory Panel"          # 41825 -> 39725; not within 30 days of:
TRIGGER_OF_AMENDED = "Assisted Ophthalmic Recovery Room Occupancy"  # 8775 per_hour
ADDED_PROC = "Elective Pulmonary Imaging Interpretation"           # 456000 per_procedure, from 2025-01-01
ADDED_DAY = "Advanced Dermatologic Nutritional Support"            # 86525 per_day, from 2025-01-01
TIE_METAB = "Inpatient Metabolic Theatre Time"                     # 21000 per_hour; cap 12
TIE_MSK = "Inpatient Musculoskeletal Theatre Time"                 # 9250 per_hour

AMENDED = {  # A1.2, as stated: (rate to 31 December 2024, rate from 1 January 2025)
    "Ambulatory Otolaryngologic Imaging Interpretation": (182625, 208200),
    "Assisted Urologic Endoscopic Procedure": (94250, 111225),
    "Bedside Neurological Radiotherapy Fraction": (2575, 3050),
    "Intensive Infectious Anaesthesia Administration": (294675, 312350),
    "Intensive Ophthalmic Laboratory Panel": (41825, 39725),
    "Intensive Otolaryngologic Anaesthesia Administration": (68025, 80275),
    "Specialist Psychiatric Discharge Planning": (38650, 44050),
}
ADDED = {ADDED_DAY: 86525, ADDED_PROC: 456000}

DAY = "2024-06-05"          # a Wednesday
SATURDAY = "2024-06-08"
SUNDAY = "2024-06-09"
CONTRACT_NUMBER = "INS-H3-2024-0562"


@pytest.fixture(scope="session")
def contract():
    return parse_contract()


@pytest.fixture(scope="session")
def occurrences():
    return load_occurrences(JSONL)


@pytest.fixture(scope="session")
def lexicon(contract, occurrences):
    return build_lexicon(contract, [li.description for o in occurrences for li in o.line_items])


@pytest.fixture(scope="session")
def matcher(contract, lexicon):
    return H3Matcher(contract, lexicon)


def rate(contract, service: str, date: str = DAY) -> int:
    return contract.rate_on(service, _dt.date.fromisoformat(date))


def basis(contract, service: str) -> str:
    return contract.services[service].unit_basis


def line(line_id, description, quantity, unit_price, *, date=DAY, unit_basis="per_visit", total=None) -> LineItem:
    try:
        parsed = _dt.date.fromisoformat(date)
    except ValueError:
        parsed = None
    return LineItem(
        line_id=line_id, invoice_id="INV-T", line_no=1, service_date_raw=date, service_date=parsed,
        description=description, quantity=quantity, unit_basis_as_billed=None, unit_basis_raw=unit_basis,
        unit_price_cents=unit_price, line_total_cents=unit_price * quantity if total is None else total,
    )


def billed(contract, line_id, service, quantity, *, price=None, date=DAY, description=None, unit_basis=None,
           total=None) -> LineItem:
    """A line billing ``service`` by its contract name, at the rate in force on
    its Service Date (no adjustment) unless told otherwise."""
    if price is None:
        price = rate(contract, service, date)
    return line(line_id, description or service, quantity, price, date=date,
                unit_basis=unit_basis or basis(contract, service), total=total)


def invoice(invoice_id, lines, *, patient="PT-1", invoice_date="2025-12-31", contract_number=CONTRACT_NUMBER,
            facility="F-MAIN", tier="BRONZE", index=0, total=None) -> InvoiceOccurrence:
    lines = [LineItem(**{**l.__dict__, "invoice_id": invoice_id}) for l in lines]
    return InvoiceOccurrence(
        occurrence_id=f"{invoice_id}#{index}", invoice_id=invoice_id, occurrence_index=index,
        hospital_id="H3", contract_number=contract_number, invoice_date_raw=invoice_date,
        invoice_date=_dt.date.fromisoformat(invoice_date), patient_id=patient, facility_code=facility,
        plan_tier=tier, admission_date_raw="2024-06-01", discharge_date_raw="2024-06-30",
        invoice_total_cents=sum(l.line_total_cents for l in lines) if total is None else total,
        line_items=tuple(lines),
    )


@pytest.fixture
def run(contract, matcher):
    """Audit synthetic occurrences; returns {invoice_id: H3InvoiceResult}."""
    def _run(*occurrences, policy=None, decisions=None, contract_=None):
        c = contract_ or contract
        m = matcher if contract_ is None else H3Matcher(c, matcher.lexicon)
        occs = list(occurrences)
        resolved = resolve_lines(occs, m, decisions or {})
        results = audit_resolved(occs, resolved, LineAuditor(c, policy or AuditPolicy()))
        return {r.result.invoice_id: r for r in results}
    return _run


def line_trace(result, line_id) -> dict:
    return next(t for a in result.occurrences for t in a.traces if t["line_id"] == line_id)


def categories(result, line_id=None) -> set:
    return {f.category for a in result.occurrences[-1:] for f in a.findings
            if line_id is None or f.line_id == line_id}


def expected(result):
    return result.result.expected_total_cents


def patched_package(tmp_path: Path, document: str, old: str, new: str, *, count: int = 1):
    """Parse a copy of the three-document package with one textual change in
    ``document`` ("base", "appendix" or "amendment")."""
    paths = {"base": BASE_PATH, "appendix": APPENDIX_PATH, "amendment": AMENDMENT_PATH}
    copies = {}
    for name, src in paths.items():
        dst = tmp_path / src.name
        shutil.copyfile(src, dst)
        copies[name] = dst
    text = copies[document].read_text(encoding="utf-8")
    assert text.count(old) >= count, old
    copies[document].write_text(text.replace(old, new, count), encoding="utf-8")
    return parse_contract(copies["base"], copies["appendix"], copies["amendment"])
