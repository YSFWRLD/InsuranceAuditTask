"""Hospital 2 test fixtures.

Audit tests run against the **real** Hospital 2 contract (so every rule tested
is a rule the contract actually states) and against **synthetic** invoices
built here.  No Hospital 2 invoice, and no label of any hospital, is used as an
expected answer.  Semantic tests never touch the network: transports are
fakes, and anything that tries to open a URL during an audit test fails.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from src.hospital_2.audit import H2Auditor, audit_resolved, resolve_lines
from src.hospital_2.contract import parse_contract
from src.hospital_2.matcher import H2Matcher, cluster_descriptions
from src.hospital_2.semantic import SemanticConfig, cluster_id, prepare_mappings
from src.shared.models import InvoiceOccurrence, LineItem

# Real Hospital 2 services and the rules the contract gives them.
PLAIN = "Advanced Rheumatologic Isolation Room Occupancy"       # 10.5  141675 per_night
WEEKEND = "Emergency Renal Infusion Therapy"                    # 4.4   5925 per_hour, +12% non-business day
DAILY = "Focused Palliative Recovery Room Occupancy"            # 4.5   52775 per_night, >12 nights/day +30%
CAPPED = "Focused Cardiac Dialysis Session"                     # 8.4   237000 per_procedure, cap 6/day
DISCOUNT = "Emergency Haematology Transport Service"            # 8.3   9675 per_visit, >60 -10%, >180 -20%
BUNDLE_A = "Standard Orthopaedic Isolation Room Occupancy"      # 14.2  33275 / bundled 27275 per_day
BUNDLE_B = "Outpatient Haematology Biopsy Procedure"            # 16.4  89775 / bundled 73625 per_procedure
EXCLUDED = "Intermittent Psychiatric Laboratory Panel"          # 8.6   50475 per_test, not within 7 days of:
TRIGGER = "Emergency Pulmonary Ventilation Support"             # 54675 per_day
COMPOUND = "Assisted Infectious Telemetry Monitoring"           # 4.2   10625 per_hour_per_item

MONDAY = "2024-06-03"
SATURDAY = "2024-06-08"
SUNDAY = "2024-06-09"
CONTRACT_NUMBER = "INS-H2-2024-1183"


@pytest.fixture(scope="session")
def contract():
    return parse_contract()


@pytest.fixture(scope="session")
def matcher(contract):
    return H2Matcher(contract)


def basis_of(contract, service: str) -> str:
    return contract.services[service].unit_basis


def line(line_id, description, quantity, unit_price, *, date=MONDAY, basis=None,
         total=None, invoice_id="INV-T") -> LineItem:
    try:
        parsed = _dt.date.fromisoformat(date)
    except ValueError:
        parsed = None
    return LineItem(
        line_id=line_id, invoice_id=invoice_id, line_no=1,
        service_date_raw=date, service_date=parsed, description=description,
        quantity=quantity, unit_basis_as_billed=None, unit_basis_raw=basis or "per_visit",
        unit_price_cents=unit_price,
        line_total_cents=unit_price * quantity if total is None else total,
    )


def invoice(invoice_id, lines, *, patient="PT-1", invoice_date="2024-07-01",
            discharge="2024-06-30", admission="2024-06-01", contract_number=CONTRACT_NUMBER,
            facility="F-MAIN", index=0, total=None) -> InvoiceOccurrence:
    lines = [LineItem(**{**l.__dict__, "invoice_id": invoice_id}) for l in lines]
    return InvoiceOccurrence(
        occurrence_id=f"{invoice_id}#{index}", invoice_id=invoice_id, occurrence_index=index,
        hospital_id="H2", contract_number=contract_number, invoice_date_raw=invoice_date,
        invoice_date=_dt.date.fromisoformat(invoice_date), patient_id=patient,
        facility_code=facility, plan_tier="GOLD", admission_date_raw=admission,
        discharge_date_raw=discharge,
        invoice_total_cents=sum(l.line_total_cents for l in lines) if total is None else total,
        line_items=tuple(lines),
    )


def svc_line(contract, line_id, service, quantity, unit_price=None, **kw) -> LineItem:
    """A line for a real service, described by its full name, billed on its basis."""
    kw.setdefault("basis", basis_of(contract, service))
    price = contract.services[service].base_rate_cents if unit_price is None else unit_price
    return line(line_id, service, quantity, price, **kw)


def mappings_for(contract, matcher, occurrences, *, overrides=None) -> dict:
    """Deterministic mappings for synthetic invoices, optionally overriding the
    final decision of named descriptions (to simulate verified semantic results)."""
    clusters = cluster_descriptions((l.description for o in occurrences for l in o.line_items), matcher)
    doc = prepare_mappings(clusters, contract, SemanticConfig.from_env(), None)
    for description, final in (overrides or {}).items():
        key = matcher.match(description).normalized_description
        doc["clusters"][cluster_id(key)]["final"] = final
    return doc


def run(contract, matcher, occurrences, **kw) -> dict:
    """Audit synthetic occurrences; results keyed by invoice id."""
    doc = mappings_for(contract, matcher, occurrences, **kw)
    resolved = resolve_lines(occurrences, matcher, doc)
    results = audit_resolved(occurrences, resolved, H2Auditor(contract))
    return {r.result.invoice_id: r for r in results}


def cats(r) -> set[str]:
    return set(r.result.error_categories)
