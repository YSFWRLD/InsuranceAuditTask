"""Command-line entry point.  Orchestration only: no audit logic lives here.

    python -m src.main audit h1          audit every invoice; write predictions
    python -m src.main evaluate h1       score against the development split
                                         (and the locked holdout, while frozen)
    python -m src.main submission        submission.csv in the template format

Development evidence (reads labels; not needed to produce predictions):

    python -m src.main split h1          the locked dev/holdout split (refuses
                                         to redraw an existing one)
    python -m src.main freeze h1 [--reason TEXT]
    python -m src.main research h1 temporal|ablation

Only Hospital 1 is implemented, so ``h1`` is the only accepted hospital.
"""

from __future__ import annotations

import argparse
from collections import Counter

from .hospital_1 import audit as h1_audit
from .hospital_1 import evaluation as h1_evaluation
from .hospital_1.matcher import write_match_audit
from .shared.data import cross_check_against_csv
from .shared.submission import write_findings, write_predictions, write_submission

REPO_ROOT = h1_audit.REPO_ROOT
OUTPUTS = REPO_ROOT / "outputs"
H1_OUTPUTS = OUTPUTS / "hospital_1"
TEMPLATE = REPO_ROOT / "submission_template.csv"


def _run_h1() -> tuple[h1_audit.Pipeline, list]:
    pipeline = h1_audit.Pipeline()
    problems = cross_check_against_csv(
        pipeline.occurrences, h1_audit.INVOICES_CSV, h1_audit.LINES_CSV
    )
    if problems:
        raise SystemExit(
            "JSONL and CSV sources disagree; refusing to audit:\n  "
            + "\n  ".join(problems[:20])
        )
    for warning in pipeline.rules.warnings:
        print("contract parser warning:", warning)
    return pipeline, pipeline.run()


def cmd_audit(_args) -> None:
    pipeline, results = _run_h1()
    write_predictions(results, H1_OUTPUTS / "predictions.csv")
    write_findings(results, H1_OUTPUTS / "findings.csv")
    write_match_audit(
        (rl.match for rl in pipeline.resolved),
        h1_evaluation.ARTIFACTS / "service_matches.csv",
    )
    statuses = Counter(rl.match.status.value for rl in pipeline.resolved)
    print(f"invoices: {len(results)}  flagged: {sum(r.flagged for r in results)}")
    print(
        "corrected total declined as unreconstructable: "
        f"{sum(r.expected_total_cents is None for r in results)}"
    )
    print(f"line matching: {dict(statuses)}")
    print(f"written to {H1_OUTPUTS} and {h1_evaluation.ARTIFACTS}")


def cmd_evaluate(_args) -> None:
    results = h1_audit.Pipeline().run()
    report = h1_evaluation.evaluation_report(results)
    path = H1_OUTPUTS / "evaluation.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"written to {path}")


def cmd_submission(_args) -> None:
    # Hospital 1 is the only implemented hospital.  It is the labelled
    # development hospital and is not scored by the exercise; its rows are
    # written so that the submission path is exercised end to end.
    _, results = _run_h1()
    path = OUTPUTS / "submission.csv"
    n = write_submission(results, path, template=TEMPLATE)
    print(f"{n} rows (hospital_1 only; no other hospital is implemented)")
    print(f"written to {path}")


def cmd_split(args) -> None:
    try:
        manifest = h1_evaluation.create_split(force=args.force)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to redraw the split: {exc}") from None
    print(f"invoice ids: {manifest['n_invoice_ids']}  dev: {manifest['n_dev']}  "
          f"holdout: {manifest['n_holdout']}")
    print(f"written to {h1_evaluation.SPLIT_MANIFEST}")


def cmd_freeze(args) -> None:
    record = h1_evaluation.freeze(reason=args.reason)
    print(f"frozen at {record['combined_sha256'][:16]}  "
          f"predictions {record['predictions_sha256'][:16]}")
    print(f"written to {h1_evaluation.FREEZE_FILE}")


def cmd_research(args) -> None:
    if args.study == "temporal":
        report, name = h1_evaluation.temporal_report(), "temporal_evaluation.md"
    else:
        report, name = h1_evaluation.ablation_report(), "ablation.md"
    path = h1_evaluation.RESEARCH_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"written to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def hospital_command(name: str, func):
        p = sub.add_parser(name)
        p.add_argument("hospital", choices=["h1"])
        p.set_defaults(func=func)
        return p

    hospital_command("audit", cmd_audit)
    hospital_command("evaluate", cmd_evaluate)
    sub.add_parser("submission").set_defaults(func=cmd_submission)
    hospital_command("split", cmd_split).add_argument("--force", action="store_true")
    hospital_command("freeze", cmd_freeze).add_argument("--reason")
    hospital_command("research", cmd_research).add_argument(
        "study", choices=["temporal", "ablation"]
    )

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
