"""Hospital 3 committed outputs: schema, determinism, and that the offline
audit reproduces them exactly."""

import csv
import json
import math
from pathlib import Path

import pytest

from src.hospital_3.audit import H3Pipeline
from src.hospital_3.report import PREDICTION_COLUMNS
from src.shared.submission import SUBMISSION_COLUMNS

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "outputs" / "hospital_3"
ART = REPO / "artifacts" / "hospital_3"


def rows(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


@pytest.fixture(scope="module")
def results():
    return H3Pipeline(check_csv=True).run()


def test_submission_schema_and_ranges():
    header, *body = rows(OUT / "submission.csv")
    assert tuple(header) == SUBMISSION_COLUMNS
    assert len(body) == 932
    ids = [r[0] for r in body]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    for invoice_id, flagged, category, exp, billed, conf in body:
        assert invoice_id.startswith("INV-H3-")
        assert flagged in {"0", "1"} and (category != "") == (flagged == "1")
        assert exp == "" or (exp.isdigit() and str(int(exp)) == exp)
        assert billed.isdigit()
        c = float(conf)
        assert not math.isnan(c) and 0 <= c <= 1


def test_offline_audit_reproduces_the_committed_submission(results):
    body = rows(OUT / "submission.csv")[1:]
    fresh = [[r.invoice_id, "1" if r.flagged else "0", "|".join(r.error_categories),
              "" if r.expected_total_cents is None else str(r.expected_total_cents), str(r.billed_total_cents),
              f"{r.confidence:.2f}"] for r in sorted((h.result for h in results), key=lambda r: r.invoice_id)]
    assert fresh == body


def test_audit_is_deterministic(results):
    again = H3Pipeline(check_csv=False).run()
    key = lambda hs: [(h.result.invoice_id, h.result.flagged, h.result.error_categories, h.result.expected_total_cents,
                       h.result.confidence) for h in hs]
    assert key(results) == key(again)


def test_predictions_agree_with_the_submission_and_explain_every_blank():
    sub = rows(OUT / "submission.csv")[1:]
    with (OUT / "predictions.csv").open(newline="", encoding="utf-8") as fh:
        pred = list(csv.DictReader(fh))
    assert tuple(pred[0]) == PREDICTION_COLUMNS
    assert [[p[c] for c in SUBMISSION_COLUMNS] for p in pred] == sub
    for p in pred:
        blank = p["expected_total_cents"] == ""
        assert blank == (p["correction_reconstructable"] == "0")
        assert blank == (p["blank_reasons"] != "")
        if p["pricing_complete"] == "0":
            assert blank


def test_committed_counts():
    cov = json.loads((ART / "reconstruction_coverage.json").read_text(encoding="utf-8"))
    assert (cov["invoice_rows"], cov["flagged"], cov["pricing_complete"], cov["correction_reconstructable"],
            cov["expected_total_blank"]) == (932, 70, 852, 848, 84)
    amendment = json.loads((ART / "amendment_summary.json").read_text(encoding="utf-8"))
    assert amendment["lines_of_added_services"] == 81
    assert amendment["lines_of_added_services_before_effective_date"] == 0


def test_every_line_has_a_trace():
    n = sum(1 for _ in (ART / "pricing_traces.jsonl").open(encoding="utf-8"))
    assert n == 11655
