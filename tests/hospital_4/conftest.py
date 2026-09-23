"""Hospital 4 test fixtures.

Audit tests run against the **real** Hospital 4 contract (so every rule tested
is a rule the contract states) and against **synthetic** invoices built here.
No Hospital 4 invoice, and no label of any hospital, is used as an expected
answer.  Nothing in the Hospital 4 pipeline touches the network.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from src.hospital_4.audit import AuditPolicy, H4Auditor, audit_resolved, resolve_lines
from src.hospital_4.contract import CONTRACT_PATH, parse_contract
from src.hospital_4.matcher import H4Matcher
from src.shared.models import InvoiceOccurrence, LineItem

# Real Hospital 4 services and the rules the contract gives them.
PLAIN = "Advanced Oncology Ward Bed Occupancy"             # 68700 per_night; no other rule
PREMIUM = "Standard Hepatic Infusion Therapy"              # 3975 per_unit_dispensed; >6 units/day +15%
CAPPED = "Focused Metabolic Biopsy Procedure"              # 15025 per_item; limit 8 per day
DISCOUNT = "Standard Oncology Ward Bed Occupancy"          # 57950 per_day; >120 cumulative -10%
TWO_TIER = "Ambulatory Musculoskeletal Ventilation Support"  # 157150 per_day; >80 -15%, >240 -30%
BUNDLE_A = "Ambulatory Obstetric Case Conference"          # 13975 per_hour; bundled 11875
BUNDLE_B = "Focused Vascular Infusion Therapy"             # 5775 per_unit_dispensed; bundled 4900
EXCLUDED = "Focused Urologic Case Conference"              # 20200 per_hour; not within 21 days of:
TRIGGER = "Outpatient Urologic Endoscopic Procedure"       # 372925 per_procedure
COMPOUND = "Intermittent Urologic Telemetry Monitoring"    # 7275 per_hour_per_item
TIE_HOUR = "Emergency Cardiac Physiotherapy Session"       # 9875 per_hour (bundle with Extended Obstetric CC)
TIE_VISIT = "Outpatient Cardiac Physiotherapy Session"     # 27300 per_visit

DAY = "2024-06-03"
CONTRACT_NUMBER = "INS-H4-2024-2049"


@pytest.fixture(scope="session")
def contract():
    return parse_contract()


@pytest.fixture(scope="session")
def matcher(contract):
    return H4Matcher(contract)


@pytest.fixture(scope="session")
def contract_text() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def rate(contract, service: str) -> int:
    return contract.services[service].base_rate_cents


def basis(contract, service: str) -> str:
    return contract.services[service].unit_basis


def line(line_id, description, quantity, unit_price, *, date=DAY, unit_basis="per_visit",
         total=None) -> LineItem:
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
    """A line billing ``service`` by its contract name, at its base rate unless told otherwise."""
    return line(line_id, description or service, quantity, rate(contract, service) if price is None else price,
                date=date, unit_basis=unit_basis or basis(contract, service), total=total)


def invoice(invoice_id, lines, *, patient="PT-1", invoice_date="2024-07-01", contract_number=CONTRACT_NUMBER,
            facility="F-MAIN", index=0, total=None) -> InvoiceOccurrence:
    lines = [LineItem(**{**l.__dict__, "invoice_id": invoice_id}) for l in lines]
    return InvoiceOccurrence(
        occurrence_id=f"{invoice_id}#{index}", invoice_id=invoice_id, occurrence_index=index,
        hospital_id="H4", contract_number=contract_number, invoice_date_raw=invoice_date,
        invoice_date=_dt.date.fromisoformat(invoice_date), patient_id=patient, facility_code=facility,
        plan_tier="GOLD", admission_date_raw="2024-06-01", discharge_date_raw="2024-06-30",
        invoice_total_cents=sum(l.line_total_cents for l in lines) if total is None else total,
        line_items=tuple(lines),
    )


@pytest.fixture
def run(contract, matcher):
    """Audit synthetic occurrences; returns {invoice_id: H4InvoiceResult}."""
    def _run(*occurrences, policy=None):
        occs = list(occurrences)
        resolved = resolve_lines(occs, matcher)
        results = audit_resolved(occs, resolved, H4Auditor(contract, policy or AuditPolicy()))
        return {r.result.invoice_id: r for r in results}
    return _run


def line_trace(result, line_id) -> dict:
    return next(t for a in result.occurrences for t in a.traces if t["line_id"] == line_id)


def categories(result, line_id=None) -> set:
    return {f.category for a in result.occurrences[-1:] for f in a.findings
            if line_id is None or f.line_id == line_id}


def patched_contract(tmp_path: Path, text: str, old: str, new: str):
    """Parse a copy of the contract with one textual change."""
    assert old in text, old
    path = tmp_path / "contract.md"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return parse_contract(path)
