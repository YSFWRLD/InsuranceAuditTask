"""Hospital 5 test fixtures.

Audit tests run against the **real** Hospital 5 contract (so every rule tested
is a rule the contract states) and against **synthetic** invoices built here.
No Hospital 5 invoice, and no label of any hospital, is used as an expected
answer.

Matching and audit mechanics use ``TEST_LEXICON``: a small, explicit lexicon
written for the tests only, so they do not depend on what Jev said.  The
pipeline never uses it.  Separate tests pin the committed Jev reviews.
Nothing in these tests touches the network.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from src.hospital_5.audit import AuditPolicy, LineAuditor, audit_resolved, resolve_lines
from src.hospital_5.contract import CONTRACT_PATH, parse_contract
from src.hospital_5.matcher import H5Matcher
from src.hospital_5.normalization import Lexicon
from src.shared.models import InvoiceOccurrence, LineItem

# Real Hospital 5 services and the rules the contract gives them.
PLAIN = "Assisted Renal Imaging Interpretation"               # 161650 per_procedure; no other rule
PREMIUM = "Comprehensive Palliative Consultation"             # 35050 per_visit; >8 visits/day +30%
WEEKEND = "Extended Neurological Consultation"                # 17100 per_visit; non-business day +25%
CAPPED = "Emergency Oncology Imaging Interpretation"          # 441250 per_procedure; cap 4 per day
BUNDLE_A = "Emergency Metabolic Discharge Planning"           # 20575 per_visit; bundled 17500
BUNDLE_B = "Specialist Gastrointestinal Pharmaceutical Dispensing"  # 4125 per_unit_dispensed; bundled 3500
TWO_TIER = "Extended Endocrine Theatre Time"                  # 15700 per_hour; >100 -12%, >300 -30%
ONE_TIER = "Continuous Metabolic Endoscopic Procedure"        # 550750 per_procedure; >80 -12%
EXCLUDED = "Routine Urologic Transport Service"               # 15250 per_visit; not within 10 days of:
TRIGGER = "Inpatient Pulmonary Critical Care Occupancy"       # 57800 per_night
TIE_HOUR = "Ambulatory Hepatic Case Conference"               # 19775 per_hour (also a discount service)
TIE_VISIT = "Intensive Hepatic Case Conference"               # 21375 per_visit
SUPV_PALL = "Supervised Palliative Consultation"              # 441425 per_procedure
SUPV_VASC = "Supervised Vascular Consultation"                # 156100 per_procedure

DAY = "2024-06-05"          # a Wednesday
SATURDAY = "2024-06-08"
CONTRACT_NUMBER = "INS-H5-2024-0731"

#: Test-only readings.  Deliberately small; written by hand for these tests.
TEST_LEXICON = Lexicon(
    "test_lexicon",
    global_map={
        "amb": ("ambulatory",), "asst": ("assisted",), "compr": ("comprehensive",), "consult": ("consultation",),
        "conf": ("conference",), "cs": ("case",), "emer": ("emergency",), "ext": ("extended",),
        "hep": ("hepatic",), "img": ("imaging",), "interp": ("interpretation",), "intens": ("intensive",),
        "metab": ("metabolic",), "neuro": ("neurological",), "pall": ("palliative",), "proc": ("procedure",),
        "ren": ("renal",), "supv": ("supervised",), "thtr": ("theatre",), "tm": ("time",), "vasc": ("vascular",),
        "endosc": ("endoscopic",), "cont": ("continuous",),
    },
    contextual={"endo": (("endocrine",), ("endoscopic",))},
)


@pytest.fixture(scope="session")
def contract():
    return parse_contract()


@pytest.fixture(scope="session")
def matcher(contract):
    return H5Matcher(contract, TEST_LEXICON)


@pytest.fixture(scope="session")
def contract_text() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def rate(contract, service: str) -> int:
    return contract.services[service].base_rate_cents


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
    """A line billing ``service`` by its contract name, at its base rate (F-MAIN,
    BRONZE multipliers of 1 for PLAIN-like services) unless told otherwise."""
    return line(line_id, description or service, quantity, rate(contract, service) if price is None else price,
                date=date, unit_basis=unit_basis or basis(contract, service), total=total)


def invoice(invoice_id, lines, *, patient="PT-1", invoice_date="2024-07-01", contract_number=CONTRACT_NUMBER,
            facility="F-MAIN", tier="BRONZE", index=0, total=None) -> InvoiceOccurrence:
    lines = [LineItem(**{**l.__dict__, "invoice_id": invoice_id}) for l in lines]
    return InvoiceOccurrence(
        occurrence_id=f"{invoice_id}#{index}", invoice_id=invoice_id, occurrence_index=index,
        hospital_id="H5", contract_number=contract_number, invoice_date_raw=invoice_date,
        invoice_date=_dt.date.fromisoformat(invoice_date), patient_id=patient, facility_code=facility,
        plan_tier=tier, admission_date_raw="2024-06-01", discharge_date_raw="2024-06-30",
        invoice_total_cents=sum(l.line_total_cents for l in lines) if total is None else total,
        line_items=tuple(lines),
    )


@pytest.fixture
def run(contract, matcher):
    """Audit synthetic occurrences; returns {invoice_id: H5InvoiceResult}."""
    def _run(*occurrences, policy=None, decisions=None, contract_=None):
        c = contract_ or contract
        m = matcher if contract_ is None else H5Matcher(c, TEST_LEXICON)
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


def patched_contract(tmp_path: Path, text: str, old: str, new: str, *, count: int = 1):
    """Parse a copy of the contract with one textual change."""
    assert text.count(old) >= count, old
    path = tmp_path / "contract.md"
    path.write_text(text.replace(old, new, count), encoding="utf-8")
    return parse_contract(path)
