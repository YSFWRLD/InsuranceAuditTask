"""The hospital submission files, the combined submission, and the committed outputs."""

import csv
import math
from pathlib import Path

import pytest

from src.shared.models import AuditResult
from src.shared.submission import SUBMISSION_COLUMNS
from src.submission import (
    SCORED_HOSPITALS,
    UNSCORED_HOSPITALS,
    combine_submissions,
    write_hospital_submission,
)

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "submission_template.csv"
COMBINED = REPO / "outputs" / "submission.csv"
H2_FILE = REPO / "outputs" / "hospital_2" / "submission.csv"
H2_PREDICTIONS = REPO / "outputs" / "hospital_2" / "predictions.csv"


def result(invoice_id, flagged=False, expected=None, billed=1000, confidence=0.9):
    return AuditResult(
        invoice_id=invoice_id,
        occurrence_ids=[f"{invoice_id}#0"],
        flagged=flagged,
        expected_total_cents=expected,
        billed_total_cents=billed,
        confidence=confidence,
    )


def rows(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


# ----------------------------------------------------------------- mechanics

def test_combined_rows_are_the_hospital_file_rows_unchanged(tmp_path):
    h2 = tmp_path / "h2.csv"
    write_hospital_submission(
        [result("INV-H2-2", True, 900), result("INV-H2-1")], h2, template=TEMPLATE)
    combined = tmp_path / "submission.csv"
    counts = combine_submissions({"hospital_2": h2}, combined)
    assert counts == {"hospital_2": 2}
    assert combined.read_bytes() == h2.read_bytes()
    assert [r[0] for r in rows(combined)[1:]] == ["INV-H2-1", "INV-H2-2"]


def test_hospital_1_is_never_scored(tmp_path):
    h1 = tmp_path / "h1.csv"
    write_hospital_submission([result("INV-H1-1")], h1, template=TEMPLATE)
    assert "hospital_1" in UNSCORED_HOSPITALS and "hospital_1" not in SCORED_HOSPITALS
    with pytest.raises(ValueError, match="not scored"):
        combine_submissions({"hospital_1": h1}, tmp_path / "out.csv")


def test_missing_scored_hospital_and_bad_header_are_refused(tmp_path):
    with pytest.raises(ValueError, match="missing"):
        combine_submissions({}, tmp_path / "out.csv")
    bad = tmp_path / "bad.csv"
    bad.write_text("invoice_id,flagged\nX,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        combine_submissions({"hospital_2": bad}, tmp_path / "out.csv")


def test_duplicate_invoice_ids_are_refused(tmp_path):
    dup = tmp_path / "dup.csv"
    dup.write_text(",".join(SUBMISSION_COLUMNS) + "\nA,0,,,10,0.50\nA,0,,,10,0.50\n",
                   encoding="utf-8")
    with pytest.raises(ValueError, match="more than once"):
        combine_submissions({"hospital_2": dup}, tmp_path / "out.csv")


# -------------------------------------------------- committed output checks

committed = pytest.mark.skipif(
    not (COMBINED.exists() and H2_FILE.exists()),
    reason="run `python -m src.main submission` first")


@committed
def test_committed_h2_subset_equals_h2_file_exactly():
    h2 = rows(H2_FILE)
    combined = rows(COMBINED)
    assert combined[0] == h2[0] == list(SUBMISSION_COLUMNS)
    subset = [r for r in combined[1:] if r[0].startswith("INV-H2-")]
    assert subset == h2[1:]
    # Nothing but hospital_2 is in the combined file today.
    assert len(combined) == len(h2)


@committed
def test_committed_submission_is_well_formed():
    header, *body = rows(COMBINED)
    assert tuple(header) == SUBMISSION_COLUMNS
    ids = [r[0] for r in body]
    assert len(ids) == len(set(ids)), "invoice ids must be unique"
    assert ids == sorted(ids), "rows must be in deterministic invoice-id order"
    for r in body:
        assert len(r) == len(SUBMISSION_COLUMNS)
        invoice_id, flagged, category, expected, billed, confidence = r
        assert invoice_id.startswith("INV-H2-"), "only hospital_2 is scored and implemented"
        assert flagged in {"0", "1"}
        assert (category != "") == (flagged == "1")
        assert expected == "" or (expected.isdigit() and str(int(expected)) == expected)
        assert billed.isdigit() and str(int(billed)) == billed
        c = float(confidence)
        assert not math.isnan(c) and 0.0 <= c <= 1.0
        assert all(v.strip().lower() not in {"nan", "null", "none"} for v in r)


@committed
def test_committed_h2_counts_and_agreement_with_predictions():
    body = rows(H2_FILE)[1:]
    assert len(body) == 1125
    assert sum(r[1] == "1" for r in body) == 74
    with H2_PREDICTIONS.open(newline="", encoding="utf-8") as fh:
        predictions = [[d[c] for c in SUBMISSION_COLUMNS] for d in csv.DictReader(fh)]
    assert predictions == body
