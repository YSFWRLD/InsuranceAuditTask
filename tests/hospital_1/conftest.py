"""Fixtures for the Hospital 1 synthetic test suite.

Everything here is invented.  No Hospital 1 invoice, description, patient or
expected total is copied into the tests: a test that reproduces a labelled
answer proves only that the answer was copied.  The synthetic contract carries
one instance of every rule family so that each rule can be exercised at its
boundary in isolation.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from src.hospital_1.audit import (
    AuditContext,
    Auditor,
    PricingEngine,
    ResolvedLine,
    assess_confidence,
)
from src.hospital_1.contract import parse_contract
from src.hospital_1.matcher import ServiceMatcher
from src.shared.models import InvoiceOccurrence, LineItem, UnitBasis

SYNTHETIC_CONTRACT = """# Provider Services Agreement

_Schedule of Contracted Services and Rates_

**Contract number:** INS-TEST-0001
**Provider:** Test Provider
**Payer:** Test Payer
**Effective from:** 1 March 2024
**Effective to:** 28 February 2025
**Currency:** GBP
**Rounding convention:** half_up_cent

## 1. Parties and Term

1.1 The Agreement takes effect on 1 March 2024 and expires on 28 February 2025.

1.2 Services are delivered from a single facility. No facility differential applies.

1.3 All patient plan tiers are reimbursed at the same rate under this Agreement.

## 2. Interpretation

2.4 "Cumulative utilisation" means the running total of Units of a Service.

## 3. Calculation Conventions

3.1 Half up, after each step.

## 4. Rate Schedule

| Service | Unit basis | Rate | Daily cap |
|---|---|---|---|
| Routine Hepatic Infusion Therapy | per hour | GBP 100.00 | — |
| Advanced Hepatic Infusion Therapy | per hour | GBP 33.33 | — |
| Capped Renal Dialysis Session | per visit | GBP 50.00 | 4 visits |
| Premium Cardiac Ward Round | per visit | GBP 80.00 | — |
| Weekend Vascular Transport Service | per visit | GBP 200.00 | — |
| Volume Ophthalmic Specimen Analysis | per test | GBP 250.00 | — |
| Bundled Alpha Theatre Time | per hour | GBP 400.00 | — |
| Bundled Beta Specimen Analysis | per item supplied | GBP 120.00 | — |
| Excluded Neurological Biopsy Procedure | per procedure | GBP 900.00 | — |
| Trigger Pulmonary Telemetry Monitoring | per day of service | GBP 60.00 | — |
| Solitary Geriatric Wound Care | per item supplied | GBP 17.00 | — |
| Alpha Paediatric Imaging Review | per test | GBP 500.00 | — |
| Beta Paediatric Imaging Review | per visit | GBP 700.00 | — |

## 5. Threshold Premiums

| Service | Applies when daily quantity exceeds | Uplift |
|---|---|---|
| Premium Cardiac Ward Round | 6 visits | +25% |

## 6. Non-Business-Day Uplifts

| Service | Uplift where the Service Date is not a Business Day |
|---|---|
| Weekend Vascular Transport Service | +10% |

## 7. Cumulative Volume Discounts

| Service | Cumulative utilisation exceeds | Discount on subsequent units |
|---|---|---|
| Volume Ophthalmic Specimen Analysis | 10 tests | 10% |
| Volume Ophthalmic Specimen Analysis | 20 tests | 30% |

7.2 Deeper discount wins; ties broken by ascending line identifier.

## 8. Daily Quantity Caps

| Service | Maximum billable units per Patient per Service Day |
|---|---|
| Capped Renal Dialysis Session | 4 visits |

## 9. Bundled Services

| Service A | Service B | Bundled rate A | Bundled rate B |
|---|---|---|---|
| Bundled Alpha Theatre Time | Bundled Beta Specimen Analysis | GBP 300.00 | GBP 90.00 |

## 10. Exclusion Windows

| Service | Not billable within | Of this Service |
|---|---|---|
| Excluded Neurological Biopsy Procedure | 7 days | Trigger Pulmonary Telemetry Monitoring |
"""


@pytest.fixture(scope="session")
def contract_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("contract") / "synthetic.md"
    path.write_text(SYNTHETIC_CONTRACT, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def rules(contract_path):
    return parse_contract(contract_path)


@pytest.fixture()
def matcher(rules):
    return ServiceMatcher(rules.services, {k: v.unit_basis for k, v in rules.services.items()})


@pytest.fixture()
def engine(rules):
    return PricingEngine(rules)


@pytest.fixture()
def auditor(rules, matcher, engine):
    return Auditor(rules, matcher, engine)


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

_BASIS_BY_NAME = {
    "Routine Hepatic Infusion Therapy": UnitBasis.HOUR,
    "Advanced Hepatic Infusion Therapy": UnitBasis.HOUR,
    "Capped Renal Dialysis Session": UnitBasis.VISIT,
    "Premium Cardiac Ward Round": UnitBasis.VISIT,
    "Weekend Vascular Transport Service": UnitBasis.VISIT,
    "Volume Ophthalmic Specimen Analysis": UnitBasis.TEST,
    "Bundled Alpha Theatre Time": UnitBasis.HOUR,
    "Bundled Beta Specimen Analysis": UnitBasis.ITEM,
    "Excluded Neurological Biopsy Procedure": UnitBasis.PROCEDURE,
    "Trigger Pulmonary Telemetry Monitoring": UnitBasis.DAY,
    "Solitary Geriatric Wound Care": UnitBasis.ITEM,
    "Alpha Paediatric Imaging Review": UnitBasis.TEST,
    "Beta Paediatric Imaging Review": UnitBasis.VISIT,
    # A description that names neither qualifier: genuinely tied.
    "Paediatric Imaging Review": UnitBasis.TEST,
}


def line(
    line_id: str,
    invoice_id: str,
    line_no: int,
    description: str,
    quantity: int,
    unit_price_cents: int,
    service_date: str = "2024-06-03",
    unit_basis: UnitBasis | str | None = None,
    line_total_cents: int | None = None,
) -> LineItem:
    """Build one line item.  ``unit_basis`` defaults to the contract's."""
    if unit_basis is None:
        unit_basis = _BASIS_BY_NAME.get(description, UnitBasis.VISIT)
    raw = unit_basis.value if isinstance(unit_basis, UnitBasis) else str(unit_basis)
    parsed = unit_basis if isinstance(unit_basis, UnitBasis) else None
    if parsed is None:
        try:
            parsed = UnitBasis(raw)
        except ValueError:
            parsed = None
    try:
        parsed_date = _dt.date.fromisoformat(service_date)
    except ValueError:
        parsed_date = None
    return LineItem(
        line_id=line_id,
        invoice_id=invoice_id,
        line_no=line_no,
        service_date_raw=service_date,
        service_date=parsed_date,
        description=description,
        quantity=quantity,
        unit_basis_as_billed=parsed,
        unit_basis_raw=raw,
        unit_price_cents=unit_price_cents,
        line_total_cents=(
            unit_price_cents * quantity if line_total_cents is None else line_total_cents
        ),
    )


def invoice(
    invoice_id: str,
    lines: list[LineItem],
    *,
    patient_id: str = "PT-TEST-1",
    invoice_date: str = "2024-07-01",
    contract_number: str = "INS-TEST-0001",
    occurrence_index: int = 0,
    invoice_total_cents: int | None = None,
    plan_tier: str = "GOLD",
) -> InvoiceOccurrence:
    try:
        parsed = _dt.date.fromisoformat(invoice_date)
    except ValueError:
        parsed = None
    return InvoiceOccurrence(
        occurrence_id=f"{invoice_id}#{occurrence_index}",
        invoice_id=invoice_id,
        occurrence_index=occurrence_index,
        hospital_id="HT",
        contract_number=contract_number,
        invoice_date_raw=invoice_date,
        invoice_date=parsed,
        patient_id=patient_id,
        facility_code="F-TEST",
        plan_tier=plan_tier,
        admission_date_raw=invoice_date,
        discharge_date_raw=invoice_date,
        invoice_total_cents=(
            sum(l.line_total_cents for l in lines)
            if invoice_total_cents is None
            else invoice_total_cents
        ),
        line_items=tuple(lines),
    )


def audit(auditor: Auditor, matcher: ServiceMatcher, occurrences: list[InvoiceOccurrence]):
    """Run the full audit over a set of synthetic occurrences."""
    resolved = [
        ResolvedLine(occ, li, matcher.match(li.description, li.unit_basis_as_billed))
        for occ in occurrences
        for li in occ.line_items
    ]
    ctx = AuditContext(auditor.rules, resolved)
    results = auditor.audit_with_context(occurrences, ctx)
    by_invoice: dict[str, list[ResolvedLine]] = {}
    for rl in resolved:
        by_invoice.setdefault(rl.occurrence.invoice_id, []).append(rl)
    for result in results:
        a = assess_confidence(result, by_invoice.get(result.invoice_id, []))
        result.confidence, result.confidence_band = a.value, a.band
    return {r.invoice_id: r for r in results}


def build_context(auditor: Auditor, matcher: ServiceMatcher, occurrences):
    """Context and resolved lines, for tests that price below the audit layer."""
    resolved = [
        ResolvedLine(occ, li, matcher.match(li.description, li.unit_basis_as_billed))
        for occ in occurrences
        for li in occ.line_items
    ]
    return AuditContext(auditor.rules, resolved), {rl.line_id: rl for rl in resolved}


def categories(result) -> set[str]:
    return set(result.error_categories)
