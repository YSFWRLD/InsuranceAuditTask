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
H3_FILE = REPO / "outputs" / "hospital_3" / "submission.csv"
H4_FILE = REPO / "outputs" / "hospital_4" / "submission.csv"
H5_FILE = REPO / "outputs" / "hospital_5" / "submission.csv"


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

def _files(tmp_path):
    h2, h3, h4, h5 = (tmp_path / f"h{n}.csv" for n in (2, 3, 4, 5))
    write_hospital_submission([result("INV-H2-2", True, 900), result("INV-H2-1")], h2, template=TEMPLATE)
    write_hospital_submission([result("INV-H3-1", True, 800)], h3, template=TEMPLATE)
    write_hospital_submission([result("INV-H4-1", True, 700)], h4, template=TEMPLATE)
    write_hospital_submission([result("INV-H5-1"), result("INV-H5-2", True, None)], h5, template=TEMPLATE)
    return {"hospital_2": h2, "hospital_3": h3, "hospital_4": h4, "hospital_5": h5}


def test_scored_hospitals_are_2_3_4_and_5():
    assert SCORED_HOSPITALS == ("hospital_2", "hospital_3", "hospital_4", "hospital_5")


def test_combined_rows_are_the_hospital_file_rows_unchanged(tmp_path):
    files = _files(tmp_path)
    combined = tmp_path / "submission.csv"
    counts = combine_submissions(files, combined)
    assert counts == {"hospital_2": 2, "hospital_3": 1, "hospital_4": 1, "hospital_5": 2}
    body = rows(combined)[1:]
    assert body[:2] == rows(files["hospital_2"])[1:]
    assert body[2:3] == rows(files["hospital_3"])[1:]
    assert body[3:4] == rows(files["hospital_4"])[1:]
    assert body[4:] == rows(files["hospital_5"])[1:]
    assert [r[0] for r in body] == ["INV-H2-1", "INV-H2-2", "INV-H3-1", "INV-H4-1", "INV-H5-1", "INV-H5-2"]


def test_hospital_1_is_never_scored(tmp_path):
    h1 = tmp_path / "h1.csv"
    write_hospital_submission([result("INV-H1-1")], h1, template=TEMPLATE)
    files = _files(tmp_path)
    assert "hospital_1" in UNSCORED_HOSPITALS and "hospital_1" not in SCORED_HOSPITALS
    with pytest.raises(ValueError, match="not scored"):
        combine_submissions({"hospital_1": h1, **files}, tmp_path / "out.csv")


def test_missing_scored_hospital_and_bad_header_are_refused(tmp_path):
    files = _files(tmp_path)
    with pytest.raises(ValueError, match="missing"):
        combine_submissions({"hospital_2": files["hospital_2"], "hospital_4": files["hospital_4"]},
                            tmp_path / "out.csv")
    bad = tmp_path / "bad.csv"
    bad.write_text("invoice_id,flagged\nX,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header"):
        combine_submissions({**files, "hospital_4": bad}, tmp_path / "out.csv")


def test_duplicate_invoice_ids_are_refused(tmp_path):
    files = _files(tmp_path)
    dup = tmp_path / "dup.csv"
    dup.write_text(",".join(SUBMISSION_COLUMNS) + "\nA,0,,,10,0.50\nA,0,,,10,0.50\n",
                   encoding="utf-8")
    with pytest.raises(ValueError, match="more than once"):
        combine_submissions({**files, "hospital_2": dup}, tmp_path / "out.csv")


# -------------------------------------------------- committed output checks

committed = pytest.mark.skipif(
    not (COMBINED.exists() and H2_FILE.exists() and H3_FILE.exists() and H4_FILE.exists() and H5_FILE.exists()),
    reason="run `python -m src.main submission` first")


@committed
def test_committed_hospital_subsets_equal_their_files_exactly():
    h2, h3, h4, h5, combined = rows(H2_FILE), rows(H3_FILE), rows(H4_FILE), rows(H5_FILE), rows(COMBINED)
    assert combined[0] == h2[0] == h3[0] == h4[0] == h5[0] == list(SUBMISSION_COLUMNS)
    assert [r for r in combined[1:] if r[0].startswith("INV-H2-")] == h2[1:]
    assert [r for r in combined[1:] if r[0].startswith("INV-H3-")] == h3[1:]
    assert [r for r in combined[1:] if r[0].startswith("INV-H4-")] == h4[1:]
    assert [r for r in combined[1:] if r[0].startswith("INV-H5-")] == h5[1:]
    # Nothing but hospitals 2, 3, 4 and 5 is in the combined file.
    assert len(combined) - 1 == (len(h2) - 1) + (len(h3) - 1) + (len(h4) - 1) + (len(h5) - 1)


@committed
def test_committed_h3_rows_appear_exactly_once_one_per_invoice_number():
    import json

    ids = {json.loads(line)["invoice_id"]
           for line in (REPO / "invoices" / "hospital_3_invoices.jsonl").read_text(encoding="utf-8").splitlines()
           if line.strip()}
    h3 = [r[0] for r in rows(COMBINED)[1:] if r[0].startswith("INV-H3-")]
    assert len(h3) == len(set(h3)) == len(ids) == 932
    assert set(h3) == ids


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
        assert invoice_id[:7] in {"INV-H2-", "INV-H3-", "INV-H4-", "INV-H5-"}, "only hospitals 2-5 are scored"
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
