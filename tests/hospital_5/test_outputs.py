"""Hospital 5 end to end: the committed outputs, determinism, no network."""

import csv
import json
import socket
from pathlib import Path

import pytest

from src.hospital_5 import report as R
from src.hospital_5.audit import H5Pipeline
from src.shared.data import load_occurrences
from src.shared.submission import SUBMISSION_COLUMNS

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "outputs" / "hospital_5"
ART = REPO / "artifacts" / "hospital_5"


def rows(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def pipeline_and_results():
    """The real pipeline, run with every socket refused: the audit reads the
    committed reviews and calls nothing."""
    original_connect = socket.socket.connect
    original_create = socket.create_connection

    def refuse(*args, **kwargs):
        raise AssertionError("the Hospital 5 audit tried to open a network connection")

    socket.socket.connect, socket.create_connection = refuse, refuse
    try:
        p = H5Pipeline()
        return p, p.run()
    finally:
        socket.socket.connect, socket.create_connection = original_connect, original_create


def test_every_invoice_number_has_exactly_one_row(pipeline_and_results):
    _, results = pipeline_and_results
    ids = {o.invoice_id for o in load_occurrences(REPO / "invoices" / "hospital_5_invoices.jsonl")}
    body = rows(OUT / "submission.csv")
    assert [r["invoice_id"] for r in body] == sorted(ids) and len(ids) == 1050
    assert {h.result.invoice_id for h in results} == ids


def test_submission_schema():
    with (OUT / "submission.csv").open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert tuple(header) == SUBMISSION_COLUMNS
    for r in rows(OUT / "submission.csv"):
        assert r["flagged"] in {"0", "1"} and (r["error_category"] != "") == (r["flagged"] == "1")
        assert r["expected_total_cents"] == "" or r["expected_total_cents"].isdigit()
        assert 0 <= float(r["confidence"]) <= 1


def test_outputs_are_deterministic(pipeline_and_results, tmp_path):
    p, results = pipeline_and_results
    R.write_outputs(p, results, outputs=tmp_path / "out", artifacts=tmp_path / "art")
    for name in ("submission.csv", "predictions.csv", "findings.csv", "audit_report.md"):
        assert (tmp_path / "out" / name).read_bytes() == (OUT / name).read_bytes(), name
    for name in ("pricing_traces.jsonl", "description_clusters.json", "blank_reasons.json",
                 "reconstruction_coverage.json", "safe_global_vocab.json", "missing_word_decisions.json"):
        assert (tmp_path / "art" / name).read_bytes() == (ART / name).read_bytes(), name


def test_blank_totals_are_explained_and_never_billed_fallbacks():
    for r in rows(OUT / "predictions.csv"):
        if r["expected_total_cents"] == "":
            assert r["blank_reasons"], r["invoice_id"]
            assert r["correction_reconstructable"] == "0"
        else:
            assert r["correction_reconstructable"] == "1"
        if r["flagged"] == "0" and r["expected_total_cents"]:
            # Unflagged and proven: the contract total must equal what was billed.
            assert r["expected_total_cents"] == r["billed_total_cents"], r["invoice_id"]


def test_predictions_agree_with_the_submission():
    sub = {r["invoice_id"]: r for r in rows(OUT / "submission.csv")}
    for r in rows(OUT / "predictions.csv"):
        assert {c: r[c] for c in SUBMISSION_COLUMNS} == sub[r["invoice_id"]]


def test_committed_counts_are_consistent():
    cov = json.loads((ART / "reconstruction_coverage.json").read_text(encoding="utf-8"))
    blanks = json.loads((ART / "blank_reasons.json").read_text(encoding="utf-8"))
    body = rows(OUT / "submission.csv")
    assert cov["invoice_rows"] == len(body) == 1050
    assert cov["expected_total_blank"] == sum(r["expected_total_cents"] == "" for r in body) == blanks["blank_rows"]
    assert cov["flagged"] == sum(r["flagged"] == "1" for r in body)


def test_contribution_report_is_committed_and_consistent():
    doc = json.loads((ART / "semantic_contribution.json").read_text(encoding="utf-8"))
    stages = doc["stages"]
    order = ["deterministic_baseline", "safe_global_normalization", "contextual_normalization",
             "jev_missing_word_resolution", "financial_equivalence"]
    assert list(stages)[:5] == order
    recon = [stages[s]["invoices_reconstructable"] for s in order]
    assert recon == sorted(recon)                                   # no stage loses a proven total
    cov = json.loads((ART / "reconstruction_coverage.json").read_text(encoding="utf-8"))
    assert stages["financial_equivalence"]["invoices_reconstructable"] == cov["correction_reconstructable"]
    assert doc["normalization_review"]["proposals_reviewed"] == 266
    assert doc["missing_word_review"]["clusters_reviewed"] == 33
    text = (OUT / "semantic_contribution.md").read_text(encoding="utf-8")
    assert "ablation_no_tiebreak_after_review" in text
    assert "jev_single_candidate_closure" not in (ART / "pricing_traces.jsonl").read_text(encoding="utf-8")


def test_vocabulary_artifacts_partition_the_proposals():
    n = sum(json.loads((ART / f).read_text(encoding="utf-8"))["count"]
            for f in ("safe_global_vocab.json", "context_required_vocab.json", "rejected_vocab.json"))
    assert n == json.loads((ART / "normalization_candidates.json").read_text(encoding="utf-8"))["count"] == 266


def test_no_label_file_and_no_other_participant_is_read():
    src = (REPO / "src" / "hospital_5")
    for f in src.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "labels/" not in text and "hospital_1_labels" not in text, f.name
