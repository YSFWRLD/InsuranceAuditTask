"""Load invoice data in the exercise's common schema.

Every hospital submits the same three files (see the exercise README): an
invoices CSV, a line-items CSV and a JSONL file carrying both.  The JSONL file
is canonical.  It is the only source that records *physical invoice
occurrences*: where an invoice identifier has been reused, the JSONL carries
two records with that id, each with its own line items.  The line-items CSV
cannot express that -- it keys lines to an ``invoice_id``, which for a reused
id is ambiguous.

We therefore build occurrences from JSONL and use the CSVs purely as a
cross-check.  In particular we never infer the line/invoice association from
the numeric portion of a line id.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from .models import InvoiceOccurrence, LineItem, UnitBasis


class DataError(Exception):
    pass


def parse_iso_date(raw: object) -> Optional[_dt.date]:
    """Strict ISO-8601 calendar date, or ``None``.

    ``None`` means *malformed*, and callers must treat that as a finding, not
    as a missing optional field.  ``2025-06-31`` and ``31/02/2024`` both come
    back as ``None``: the first is a real-looking date that does not exist, the
    second is the wrong format entirely.
    """
    if not isinstance(raw, str):
        return None
    try:
        return _dt.date.fromisoformat(raw)
    except ValueError:
        return None


def _unit_basis(raw: str) -> Optional[UnitBasis]:
    try:
        return UnitBasis(raw)
    except ValueError:
        return None


def load_occurrences(jsonl_path: str | Path) -> list[InvoiceOccurrence]:
    records = []
    with Path(jsonl_path).open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise DataError(f"{jsonl_path}:{lineno}: bad JSON: {exc}") from exc

    # Occurrence index is assigned in file order, which is the only ordering
    # the source gives us for two records sharing an id.
    seen: Counter[str] = Counter()
    occurrences: list[InvoiceOccurrence] = []
    for rec in records:
        invoice_id = rec["invoice_id"]
        idx = seen[invoice_id]
        seen[invoice_id] += 1
        occurrence_id = f"{invoice_id}#{idx}"

        lines: list[LineItem] = []
        for li in rec["line_items"]:
            sd_raw = li["service_date"]
            lines.append(
                LineItem(
                    line_id=li["line_id"],
                    invoice_id=invoice_id,
                    line_no=int(li["line_no"]),
                    service_date_raw="" if sd_raw is None else str(sd_raw),
                    service_date=parse_iso_date(sd_raw),
                    description=li["description"],
                    quantity=int(li["quantity"]),
                    unit_basis_as_billed=_unit_basis(li["unit_basis_as_billed"]),
                    unit_basis_raw=li["unit_basis_as_billed"],
                    unit_price_cents=int(li["unit_price_cents"]),
                    line_total_cents=int(li["line_total_cents"]),
                )
            )

        occurrences.append(
            InvoiceOccurrence(
                occurrence_id=occurrence_id,
                invoice_id=invoice_id,
                occurrence_index=idx,
                hospital_id=rec["hospital_id"],
                contract_number=rec["contract_number"],
                invoice_date_raw=str(rec["invoice_date"]),
                invoice_date=parse_iso_date(rec["invoice_date"]),
                patient_id=rec["patient_id"],
                facility_code=rec["facility_code"],
                plan_tier=rec["plan_tier"],
                admission_date_raw=str(rec.get("admission_date")),
                discharge_date_raw=str(rec.get("discharge_date")),
                invoice_total_cents=int(rec["invoice_total_cents"]),
                line_items=tuple(lines),
            )
        )

    line_ids = [li.line_id for o in occurrences for li in o.line_items]
    if len(set(line_ids)) != len(line_ids):
        raise DataError("line ids are not unique across invoice occurrences")
    return occurrences


def cross_check_against_csv(
    occurrences: list[InvoiceOccurrence],
    invoices_csv: str | Path,
    line_items_csv: str | Path,
) -> list[str]:
    """Compare the JSONL-derived view against the two CSVs.

    Returns a list of human-readable discrepancies.  An empty list means the
    three files agree, which is what licenses us to use JSONL alone.
    """
    problems: list[str] = []

    with Path(invoices_csv).open(newline="", encoding="utf-8") as fh:
        csv_invoices = list(csv.DictReader(fh))
    with Path(line_items_csv).open(newline="", encoding="utf-8") as fh:
        csv_lines = list(csv.DictReader(fh))

    if len(csv_invoices) != len(occurrences):
        problems.append(
            f"invoice row count: csv={len(csv_invoices)} jsonl={len(occurrences)}"
        )

    jsonl_lines = {li.line_id: li for o in occurrences for li in o.line_items}
    if len(csv_lines) != len(jsonl_lines):
        problems.append(
            f"line row count: csv={len(csv_lines)} jsonl={len(jsonl_lines)}"
        )

    for row in csv_lines:
        li = jsonl_lines.get(row["line_id"])
        if li is None:
            problems.append(f"line {row['line_id']} present in csv, absent from jsonl")
            continue
        for csv_key, value in (
            ("invoice_id", li.invoice_id),
            ("service_date", li.service_date_raw),
            ("description", li.description),
            ("quantity", str(li.quantity)),
            ("unit_basis_as_billed", li.unit_basis_raw),
            ("unit_price_cents", str(li.unit_price_cents)),
            ("line_total_cents", str(li.line_total_cents)),
        ):
            if row[csv_key] != value:
                problems.append(
                    f"line {row['line_id']}.{csv_key}: csv={row[csv_key]!r} "
                    f"jsonl={value!r}"
                )

    # Invoice-header comparison has to be done id-wise because a reused id maps
    # to two CSV rows and two occurrences; compare the multisets.
    def header_key(d: dict[str, str]) -> tuple:
        return (
            d["contract_number"], d["invoice_date"], d["patient_id"],
            d["facility_code"], d["plan_tier"], d["invoice_total_cents"],
        )

    csv_by_id: dict[str, list[tuple]] = defaultdict(list)
    for row in csv_invoices:
        csv_by_id[row["invoice_id"]].append(header_key(row))
    jsonl_by_id: dict[str, list[tuple]] = defaultdict(list)
    for o in occurrences:
        jsonl_by_id[o.invoice_id].append(
            (
                o.contract_number, o.invoice_date_raw, o.patient_id,
                o.facility_code, o.plan_tier, str(o.invoice_total_cents),
            )
        )
    for invoice_id in set(csv_by_id) | set(jsonl_by_id):
        if sorted(csv_by_id.get(invoice_id, [])) != sorted(jsonl_by_id.get(invoice_id, [])):
            problems.append(f"invoice {invoice_id}: header mismatch between csv and jsonl")

    return problems
