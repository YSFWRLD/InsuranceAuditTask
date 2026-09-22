"""Hospital 1 evaluation: the no-label-leakage guards, the locked split, the metrics.

The first half are structural guards on the claim that the predictor cannot
see the answers.

These tests are cheap and blunt on purpose.  They are the difference between
"we did not use the labels" as a promise and as a property of the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The prediction path.  ``hospital_1/evaluation.py`` is deliberately absent:
#: it is the one module allowed to read labels, and nothing here may import it.
PREDICTION_MODULES = [
    "shared/models.py", "shared/data.py", "shared/money.py", "shared/submission.py",
    "hospital_1/contract.py", "hospital_1/matcher.py", "hospital_1/audit.py",
]


def test_the_prediction_module_list_is_complete():
    """A new module on the prediction path must be added above, or it goes unguarded.

    Hospital 1's prediction path is ``shared/`` plus ``hospital_1/``.  Other
    hospitals' packages are not on it, and a separate test below forbids any
    Hospital 1 prediction module from referring to them.
    """
    on_disk = {
        str(p.relative_to(ROOT / "src")).replace("\\", "/")
        for package in ("shared", "hospital_1")
        for p in (ROOT / "src" / package).rglob("*.py")
        if p.name != "__init__.py"
    }
    not_prediction = {"hospital_1/evaluation.py"}
    assert on_disk - not_prediction == set(PREDICTION_MODULES)


def _source(name: str) -> str:
    return (ROOT / "src" / name).read_text(encoding="utf-8")


def _code_only(name: str) -> str:
    """Source with comments and string literals removed.

    Prose may name a Hospital 1 service to explain why a rule exists; a
    docstring saying "53 lines might be Preoperative Immunologic Endoscopic
    Procedure" is documentation, not a hardcoded answer.  *Executable* code
    naming one would be the real thing, so that is what these tests check.
    """
    import io
    import tokenize

    kept: list[str] = []
    readline = io.StringIO(_source(name)).readline
    for tok in tokenize.generate_tokens(readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(tok.string)
    return " ".join(kept)


@pytest.mark.parametrize("module", PREDICTION_MODULES)
def test_prediction_modules_never_mention_labels(module):
    text = _code_only(module).lower()
    for forbidden in ("labels/", "dev_labels", "holdout", "is_erroneous", "error_categories\"]"):
        assert forbidden not in text, f"{module} refers to {forbidden!r}"


@pytest.mark.parametrize("module", PREDICTION_MODULES)
def test_prediction_modules_do_not_import_the_evaluator(module):
    text = _source(module)
    assert "evaluation" not in _code_only(module)
    assert "import evaluate" not in text


@pytest.mark.parametrize("module", PREDICTION_MODULES)
def test_no_invoice_patient_or_line_identifiers_are_hardcoded(module):
    """Guards against "if invoice_id == ..." creeping in to reproduce a label."""
    text = _code_only(module)
    for pattern, what in (
        (r"INV-H1-\d+", "an invoice id"),
        (r"PT-H1-\d+", "a patient id"),
        (r"H1-L\d+", "a line id"),
        (r"INS-H1-\d{4}-\d{4}", "the contract number"),
    ):
        assert not re.search(pattern, text), f"{module} hardcodes {what}"


@pytest.mark.parametrize("module", PREDICTION_MODULES)
def test_no_hospital_1_service_names_are_hardcoded(module):
    """Every rate, cap, premium and bundle must come from the parsed contract."""
    from src.hospital_1.contract import parse_contract

    rules = parse_contract(ROOT / "contracts" / "hospital_1" / "provider_services_agreement.md")
    text = _code_only(module)
    for name in rules.services:
        assert name not in text, f"{module} hardcodes the service {name!r}"


@pytest.mark.parametrize("module", PREDICTION_MODULES)
def test_no_hospital_2_to_5_data_is_referenced(module):
    text = _source(module)
    for n in (2, 3, 4, 5):
        assert f"hospital_{n}" not in text


def test_the_abbreviation_table_is_general_not_a_lookup_of_answers():
    """Each entry must be a word-level abbreviation, not a description mapping.

    A key containing a space would be a phrase, which is how a
    "this description means that service" table would have to be written.
    """
    from src.shared.data import load_occurrences
    from src.hospital_1.matcher import ABBREVIATIONS, normalize_description

    for short, long in ABBREVIATIONS.items():
        assert " " not in short and " " not in long, (short, long)
        assert short.isalpha() and long.isalpha(), (short, long)
        assert short != long

    # No key may be a whole billing description: that shape -- one description
    # in, one expansion out -- is exactly how an answer table would be smuggled
    # in under the name of an abbreviation.
    descriptions = {
        normalize_description(li.description)
        for occ in load_occurrences(ROOT / "invoices" / "hospital_1_invoices.jsonl")
        for li in occ.line_items
    }
    assert not (set(ABBREVIATIONS) & descriptions)


def test_results_do_not_depend_on_which_labels_exist():
    """Hiding every label file must not change a single prediction."""
    from src.hospital_1.audit import Pipeline

    before = {r.invoice_id: (r.flagged, tuple(r.error_categories), r.expected_total_cents)
              for r in Pipeline().run()}

    from src.hospital_1.evaluation import DEV_LABELS, HOLDOUT_LABELS, LABELS_CSV

    label_files = (LABELS_CSV, DEV_LABELS, HOLDOUT_LABELS)
    # Asserted, not skipped: a moved label file must fail this test loudly
    # rather than let it pass while hiding nothing.
    for path in label_files:
        assert path.exists(), path
    moved = []
    try:
        for path in label_files:
            hidden = path.with_suffix(".csv.hidden")
            path.rename(hidden)
            moved.append((hidden, path))
        after = {r.invoice_id: (r.flagged, tuple(r.error_categories), r.expected_total_cents)
                 for r in Pipeline().run()}
    finally:
        for hidden, path in moved:
            hidden.rename(path)

    assert before == after


# ---------------------------------------------------------------------------
# The locked split
# ---------------------------------------------------------------------------

def test_the_split_reproduces_exactly_from_its_seed(tmp_path):
    """Re-deriving the split elsewhere gives the same invoices and the same bytes.

    This is what makes the recorded split checkable rather than asserted.
    """
    import json

    from src.hospital_1.evaluation import SPLIT_MANIFEST, create_split

    recorded = json.loads(SPLIT_MANIFEST.read_text(encoding="utf-8"))
    again = create_split(
        split_dir=tmp_path / "split", manifest_path=tmp_path / "manifest.json", force=True
    )
    for key in ("dev_invoice_ids", "holdout_invoice_ids", "source_labels_sha256",
                "dev_file_sha256", "holdout_file_sha256", "n_dev", "n_holdout"):
        assert again[key] == recorded[key], key
    assert not set(recorded["dev_invoice_ids"]) & set(recorded["holdout_invoice_ids"])


def test_the_locked_split_refuses_to_be_redrawn():
    from src.hospital_1.evaluation import create_split

    with pytest.raises(FileExistsError):
        create_split()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_detection_attribution_and_money_are_scored_separately():
    from src.hospital_1.evaluation import Label, evaluate
    from src.shared.models import AuditResult, Finding

    def result(invoice_id, cats, expected):
        return AuditResult(
            invoice_id=invoice_id, occurrence_ids=[f"{invoice_id}#0"],
            billed_total_cents=100, flagged=bool(cats),
            findings=[Finding(category=c, detail="") for c in cats],
            expected_total_cents=expected,
        )

    def label(invoice_id, cats, expected):
        return Label(invoice_id, bool(cats), tuple(cats), expected, False)

    results = [
        result("A", ["daily_cap_exceeded"], None),       # TP, total declined
        result("B", ["unit_price_mismatch"], 90),        # TP, wrong category, total wrong
        result("C", ["premium_omitted"], 100),           # FP
        result("D", [], 100),                            # FN
        result("E", [], 100),                            # TN
    ]
    labels = {
        "A": label("A", ["daily_cap_exceeded"], 70),
        "B": label("B", ["unknown_service"], 100),
        "C": label("C", [], 100),
        "D": label("D", ["cross_invoice_duplicate"], 0),
        "E": label("E", [], 100),
    }
    ev = evaluate(results, labels, "synthetic")

    assert (ev.tp, ev.fp, ev.fn, ev.tn) == (2, 1, 1, 1)
    assert ev.per_category["daily_cap_exceeded"].tp == 1
    assert ev.per_category["unit_price_mismatch"].fp == 1
    assert ev.per_category["unknown_service"].fn == 1
    # A declined total is neither right nor wrong; it is counted apart.
    assert ev.total_declined == 1
    assert ev.total_compared == 4
    assert ev.total_exact == 2                      # C and E
    assert ev.mae_cents == (10 + 0 + 100 + 0) / 4
