"""Loading invoices: physical occurrences, malformed dates, bad input."""

from __future__ import annotations

import json

import pytest

from src.shared.data import load_occurrences, parse_iso_date


@pytest.mark.parametrize(
    "raw",
    [
        "2025-06-31",     # June has 30 days
        "2025-02-30",
        "2024-00-17",     # month zero
        "31/02/2024",     # wrong format, and not a real date either
        "not-a-date",
        "",
        None,
        20240101,
    ],
)
def test_malformed_dates_come_back_as_none(raw):
    assert parse_iso_date(raw) is None


@pytest.mark.parametrize("raw", ["2024-01-01", "2024-02-29", "2025-12-31"])
def test_well_formed_dates_parse(raw):
    assert parse_iso_date(raw).isoformat() == raw


def test_a_reused_identifier_yields_two_physical_occurrences(tmp_path):
    """Collapsing on invoice_id would silently discard a whole invoice."""
    records = [
        {
            "invoice_id": "INV-X-1", "hospital_id": "HT",
            "contract_number": "C", "invoice_date": "2024-03-01",
            "patient_id": "P1", "facility_code": "F", "plan_tier": "GOLD",
            "admission_date": "2024-03-01", "discharge_date": "2024-03-01",
            "invoice_total_cents": 100,
            "line_items": [{
                "line_id": "A-1", "invoice_id": "INV-X-1", "line_no": 1,
                "service_date": "2024-03-01", "description": "d", "quantity": 1,
                "unit_basis_as_billed": "per_visit", "unit_price_cents": 100,
                "line_total_cents": 100,
            }],
        },
        {
            "invoice_id": "INV-X-1", "hospital_id": "HT",
            "contract_number": "C", "invoice_date": "2024-05-01",
            "patient_id": "P2", "facility_code": "F", "plan_tier": "BRONZE",
            "admission_date": "2024-05-01", "discharge_date": "2024-05-01",
            "invoice_total_cents": 250,
            "line_items": [{
                "line_id": "B-9", "invoice_id": "INV-X-1", "line_no": 1,
                "service_date": "2024-05-01", "description": "d", "quantity": 1,
                "unit_basis_as_billed": "per_visit", "unit_price_cents": 250,
                "line_total_cents": 250,
            }],
        },
    ]
    path = tmp_path / "x.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    occurrences = load_occurrences(path)
    assert len(occurrences) == 2
    assert [o.occurrence_id for o in occurrences] == ["INV-X-1#0", "INV-X-1#1"]
    assert [o.patient_id for o in occurrences] == ["P1", "P2"]
    # The line/invoice association comes from the file structure, never from
    # the digits inside a line identifier.
    assert occurrences[1].line_items[0].line_id == "B-9"


def test_duplicate_line_ids_are_rejected(tmp_path):
    record = {
        "invoice_id": "INV-Y-1", "hospital_id": "HT", "contract_number": "C",
        "invoice_date": "2024-03-01", "patient_id": "P1", "facility_code": "F",
        "plan_tier": "GOLD", "admission_date": "2024-03-01",
        "discharge_date": "2024-03-01", "invoice_total_cents": 200,
        "line_items": [
            {
                "line_id": "SAME", "invoice_id": "INV-Y-1", "line_no": i,
                "service_date": "2024-03-01", "description": "d", "quantity": 1,
                "unit_basis_as_billed": "per_visit", "unit_price_cents": 100,
                "line_total_cents": 100,
            }
            for i in (1, 2)
        ],
    }
    path = tmp_path / "y.jsonl"
    path.write_text(json.dumps(record), encoding="utf-8")
    from src.shared.data import DataError

    with pytest.raises(DataError, match="line ids are not unique"):
        load_occurrences(path)


def test_bad_json_names_the_line(tmp_path):
    path = tmp_path / "z.jsonl"
    path.write_text('{"invoice_id": "A"}\nnot json\n', encoding="utf-8")
    from src.shared.data import DataError

    with pytest.raises(DataError, match=":2:"):
        load_occurrences(path)
