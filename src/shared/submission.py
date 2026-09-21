"""Audit results -> CSV.

Three files, all hospital-independent because they read only ``AuditResult``:

``write_submission``   exactly the columns of ``submission_template.csv``
``write_predictions``  the same columns plus the ones honesty requires --
                       confidence band, whether a corrected total was produced,
                       whether it can be vouched for, the contractual ceiling,
                       the physical occurrences, and the uncertainty reasons
``write_findings``     one row per finding, with the line it came from

``expected_total_cents`` is written EMPTY where the corrected figure cannot be
recovered from the invoice.  A blank is a claim of ignorance; a number would
be a claim of knowledge.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .models import AuditResult

SUBMISSION_COLUMNS = (
    "invoice_id",
    "flagged",
    "error_category",
    "expected_total_cents",
    "billed_total_cents",
    "confidence",
)

UNCERTAINTY_COLUMNS = (
    "confidence_band",
    "pricing_complete",
    "correction_reconstructable",
    "maximum_contractually_payable_total_cents",
    "physical_occurrence_ids",
    "uncertainty_reasons",
)


def _blank_if_none(value: int | None) -> int | str:
    return "" if value is None else value


def _submission_values(r: AuditResult) -> list:
    return [
        r.invoice_id,
        1 if r.flagged else 0,
        "|".join(r.error_categories),
        _blank_if_none(r.expected_total_cents),
        r.billed_total_cents,
        f"{r.confidence:.2f}",
    ]


def _uncertainty_values(r: AuditResult) -> list:
    return [
        r.confidence_band,
        int(r.pricing_complete),
        int(r.correction_reconstructable),
        _blank_if_none(r.maximum_contractually_payable_total_cents),
        "|".join(r.occurrence_ids),
        " ; ".join(r.uncertainty_reasons),
    ]


def write_submission(
    results: Iterable[AuditResult], path: Path, *, template: Path
) -> int:
    """Write the challenge submission.  Returns the number of rows.

    The header is taken from the template file and checked against what this
    module writes, so a change to either is caught rather than shipped.
    """
    with template.open(newline="", encoding="utf-8") as fh:
        expected = tuple(next(csv.reader(fh)))
    if expected != SUBMISSION_COLUMNS:
        raise ValueError(
            f"{template} has columns {expected}; this writer produces {SUBMISSION_COLUMNS}"
        )
    rows = sorted(results, key=lambda r: r.invoice_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(SUBMISSION_COLUMNS)
        for r in rows:
            writer.writerow(_submission_values(r))
    return len(rows)


def write_predictions(results: Iterable[AuditResult], path: Path) -> None:
    """Submission columns followed by the uncertainty columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(SUBMISSION_COLUMNS + UNCERTAINTY_COLUMNS)
        for r in sorted(results, key=lambda r: r.invoice_id):
            writer.writerow(_submission_values(r) + _uncertainty_values(r))


def write_findings(results: Iterable[AuditResult], path: Path) -> None:
    """Machine-readable, one row per finding, for downstream triage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["invoice_id", "line_id", "category", "detail"])
        for r in sorted(results, key=lambda r: r.invoice_id):
            for f in r.findings:
                writer.writerow([r.invoice_id, f.line_id or "", f.category, f.detail])
