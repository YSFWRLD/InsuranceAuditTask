"""Hospital-independent data structures.

What belongs here is what every hospital's audit handles regardless of its
contract: the invoice schema every hospital submits in (see the exercise
README), the three-way outcome of identifying a service, and the shape of an
audit result.  Contract rules do *not* belong here -- each hospital's
agreement is its own, and its rule types live beside its parser.

Nothing in this module (or in any prediction module) may read a label file.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# --------------------------------------------------------------------------
# Unit bases
# --------------------------------------------------------------------------

class UnitBasis(str, Enum):
    """Canonical unit bases: the ``unit_basis_as_billed`` invoice tokens.

    The members are the tokens observed in Hospital 1's invoices.  A token
    outside this set loads as ``None`` with the raw string kept on the line
    (see ``shared.data``), so an unfamiliar vocabulary degrades to "unknown
    basis" rather than failing.  Mapping a contract's own prose ("per night of
    occupancy") onto these tokens is that hospital's job.
    """

    HOUR = "per_hour"
    DAY = "per_day"
    VISIT = "per_visit"
    TEST = "per_test"
    PROCEDURE = "per_procedure"
    NIGHT = "per_night"
    UNIT_DISPENSED = "per_unit_dispensed"
    ITEM = "per_item"


# --------------------------------------------------------------------------
# Invoice data
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LineItem:
    line_id: str
    invoice_id: str
    line_no: int
    service_date_raw: str
    service_date: Optional[_dt.date]
    description: str
    quantity: int
    unit_basis_as_billed: Optional[UnitBasis]
    unit_basis_raw: str
    unit_price_cents: int
    line_total_cents: int


@dataclass(frozen=True)
class InvoiceOccurrence:
    """One *physical* invoice record.

    ``invoice_id`` is not unique: the JSONL file carries two records sharing an
    id where the provider has reused an identifier.  ``occurrence_id`` is.
    """

    occurrence_id: str
    invoice_id: str
    occurrence_index: int
    hospital_id: str
    contract_number: str
    invoice_date_raw: str
    invoice_date: Optional[_dt.date]
    patient_id: str
    facility_code: str
    plan_tier: str
    admission_date_raw: str
    discharge_date_raw: str
    invoice_total_cents: int
    line_items: tuple[LineItem, ...]


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

class MatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ServiceMatch:
    description: str
    normalized_description: str
    status: MatchStatus
    service: Optional[str]
    match_score: float
    runner_up: Optional[str]
    runner_up_score: float
    candidate_services: tuple[str, ...]
    match_method: str
    used_unit_basis_for_matching: bool = False


# --------------------------------------------------------------------------
# Audit results
# --------------------------------------------------------------------------

@dataclass
class Finding:
    """One detected contract or data violation."""

    category: str
    detail: str
    line_id: Optional[str] = None
    #: Does this finding, on its own, prevent us reconstructing the true total?
    blocks_reconstruction: bool = False


@dataclass
class AuditResult:
    invoice_id: str
    occurrence_ids: list[str]
    billed_total_cents: int
    flagged: bool
    findings: list[Finding] = field(default_factory=list)
    expected_total_cents: Optional[int] = None
    maximum_contractually_payable_total_cents: Optional[int] = None
    pricing_complete: bool = True
    correction_reconstructable: bool = True
    uncertainty_reasons: list[str] = field(default_factory=list)
    confidence: float = 0.5
    confidence_band: str = "medium"

    @property
    def error_categories(self) -> list[str]:
        seen: list[str] = []
        for f in self.findings:
            if f.category not in seen:
                seen.append(f.category)
        return seen


# --------------------------------------------------------------------------
# Rate schedule
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ServiceRule:
    """One contracted service: what it is called, how it is billed, what it costs.

    ``source_section`` is required: provenance is part of the rule, and a
    default would silently attribute every rule to one contract's layout.
    """

    name: str
    unit_basis: UnitBasis
    base_rate_cents: int
    source_section: str
    #: Where the rate schedule itself states a daily cap.
    daily_cap_units: Optional[int] = None
