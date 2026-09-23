"""Command-line entry point.  Orchestration only: no audit logic lives here.

    python -m src.main audit h1          audit every invoice; write predictions
    python -m src.main audit h2          (offline: reads persisted service mappings)
    python -m src.main audit h4          (offline, deterministic; writes artifacts too)
    python -m src.main evaluate h1       score against the development split
                                         (and the locked holdout, while frozen)
    python -m src.main submission        outputs/hospital_{2,4}/submission.csv and
                                         the combined outputs/submission.csv (scored
                                         hospitals only: hospital_2, hospital_4;
                                         offline)

Hospital 2 semantic service identity (run deliberately, never by an audit):

    python -m src.main semantic h2 prepare      contract rules, clusters, mappings
    python -m src.main semantic h2 status       configuration readiness + counts
    python -m src.main semantic h2 classify     OpenRouter classifier, per pending
                                                cluster, bounded retries (OPENROUTER_API_KEY)
    python -m src.main semantic h2 jev          Jev by API (JEV_API_KEY, JEV_API_URL)
    python -m src.main semantic h2 jev-export   Playground state + questions JSON
    python -m src.main semantic h2 jev-import   apply jev_results.json (Playground)
    python -m src.main semantic h2 rebuild      recompute final decisions

Development evidence (reads labels; not needed to produce predictions):

    python -m src.main split h1          the locked dev/holdout split (refuses
                                         to redraw an existing one)
    python -m src.main freeze h1 [--reason TEXT]
    python -m src.main research h1 temporal|ablation

Hospitals 1, 2 and 4 are implemented; ``h1``, ``h2`` and ``h4`` are the accepted
hospitals.  Hospital 4 has no semantic stage and no labels.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from .hospital_1 import audit as h1_audit
from .hospital_1 import evaluation as h1_evaluation
from .hospital_1.matcher import write_match_audit
from .hospital_2 import audit as h2_audit
from .hospital_4 import audit as h4_audit
from .hospital_2 import contract as h2_contract
from .hospital_2 import semantic as h2_semantic
from .hospital_2.matcher import H2Matcher, cluster_descriptions
from .shared.data import cross_check_against_csv, load_occurrences
from .shared.submission import write_findings, write_predictions
from .submission import combine_submissions, write_hospital_submission

REPO_ROOT = h1_audit.REPO_ROOT
OUTPUTS = REPO_ROOT / "outputs"
H1_OUTPUTS = OUTPUTS / "hospital_1"
H2_OUTPUTS = OUTPUTS / "hospital_2"
H4_OUTPUTS = OUTPUTS / "hospital_4"
TEMPLATE = REPO_ROOT / "submission_template.csv"


# --------------------------------------------------------------------------
# Hospital 1
# --------------------------------------------------------------------------

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


def cmd_audit(args) -> None:
    if args.hospital == "h2":
        _audit_h2()
        return
    if args.hospital == "h4":
        _audit_h4()
        return
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


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------

def cmd_submission(_args) -> None:
    # Only scored hospitals go into the submission.  Hospital 1 is the
    # labelled development hospital and is not scored, so it is left out.
    # Hospitals 2 and 4 are the scored hospitals implemented; 3 and 5 are not.
    _, results = _run_h2()
    h2_file = H2_OUTPUTS / "submission.csv"
    write_hospital_submission([r.result for r in results], h2_file, template=TEMPLATE)
    h4_pipeline = h4_audit.H4Pipeline()
    h4_file = H4_OUTPUTS / "submission.csv"
    write_hospital_submission([r.result for r in h4_pipeline.run()], h4_file, template=TEMPLATE)
    path = OUTPUTS / "submission.csv"
    counts = combine_submissions({"hospital_2": h2_file, "hospital_4": h4_file}, path)
    for name, file in (("hospital_2", h2_file), ("hospital_4", h4_file)):
        print(f"{file}: {counts[name]} rows")
    print(f"{path}: {sum(counts.values())} rows {counts} (hospital_1 is not scored; "
          "hospitals 3 and 5 are not implemented)")


# --------------------------------------------------------------------------
# Hospital 2
# --------------------------------------------------------------------------

def _run_h2():
    problems = cross_check_against_csv(
        load_occurrences(h2_audit.JSONL), h2_audit.INVOICES_CSV, h2_audit.LINES_CSV
    )
    if problems:
        raise SystemExit("hospital_2 JSONL and CSV disagree:\n  " + "\n  ".join(problems[:20]))
    try:
        pipeline = h2_audit.H2Pipeline()
    except h2_audit.StaleMappingsError as exc:
        raise SystemExit(str(exc)) from None
    return pipeline, pipeline.run()


def _print_counts(counts: dict) -> None:
    width = max(len(k) for k in counts)
    for key, value in counts.items():
        print(f"  {key:<{width}}  {value:>6}   {h2_audit.COUNT_EXPLANATIONS.get(key, '')}")


def _audit_h2() -> None:
    pipeline, results = _run_h2()
    res = [r.result for r in results]
    h2_audit.write_predictions(results, H2_OUTPUTS / "predictions.csv")
    h2_audit.write_findings(results, H2_OUTPUTS / "findings.csv")
    write_hospital_submission(res, H2_OUTPUTS / "submission.csv", template=TEMPLATE)
    n_traces = h2_audit.write_traces(results, h2_audit.ARTIFACTS / "pricing_traces.jsonl")
    (H2_OUTPUTS / "audit_report.md").write_text(
        h2_audit.audit_report(pipeline, results), encoding="utf-8")
    print(f"invoice occurrences: {len(pipeline.occurrences)}  invoice numbers (rows): {len(res)}  "
          f"flagged rows: {sum(r.flagged for r in res)}")
    print(f"pricing_complete: {sum(r.pricing_complete for r in res)}  "
          f"correction_reconstructable: {sum(r.correction_reconstructable for r in res)}  "
          f"expected_total_cents blank: {sum(r.expected_total_cents is None for r in res)}")
    print("identity:")
    _print_counts(h2_audit.identity_counts(pipeline))
    print(f"pricing traces for flagged invoices: {n_traces}")
    print(f"written to {H2_OUTPUTS}")


def _audit_h4() -> None:
    pipeline = h4_audit.H4Pipeline()
    results = pipeline.run()
    info = h4_audit.write_outputs(pipeline, results, template=TEMPLATE)
    res = [r.result for r in results]
    print(f"invoice occurrences: {len(pipeline.occurrences)}  invoice numbers (rows): {info['rows']}  "
          f"flagged rows: {sum(r.flagged for r in res)}")
    print(f"pricing_complete: {sum(r.pricing_complete for r in res)}  "
          f"correction_reconstructable: {sum(r.correction_reconstructable for r in res)}  "
          f"expected_total_cents blank: {sum(r.expected_total_cents is None for r in res)}")
    print("identity:")
    _print_counts(info["counts"])
    print(f"pricing traces (every line): {info['traces']}")
    print(f"written to {H4_OUTPUTS} and {h4_audit.ARTIFACTS}")


def _h2_clusters(contract):
    occurrences = load_occurrences(h2_audit.JSONL)
    return cluster_descriptions(h2_semantic.descriptions_of(occurrences), H2Matcher(contract))


def _save_mappings(doc: dict) -> None:
    h2_semantic.write_json(h2_semantic.MAPPINGS_FILE, doc)
    h2_semantic.write_json(h2_semantic.UNRESOLVED_FILE, h2_semantic.unresolved_report(doc))


def _write_jev_batch(state: dict, questions: dict) -> None:
    h2_semantic.write_json(h2_semantic.JEV_STATE_FILE, state)
    h2_semantic.write_json(h2_semantic.JEV_QUESTIONS_FILE, questions)


def cmd_semantic(args) -> None:
    config = h2_semantic.SemanticConfig.from_env()
    contract = h2_contract.parse_contract()

    if args.step == "prepare":
        h2_contract.write_contract_rules(contract, h2_semantic.ARTIFACTS / "contract_rules.json")
        clusters = _h2_clusters(contract)
        h2_semantic.write_json(
            h2_semantic.CLUSTERS_FILE, h2_semantic.clusters_document(clusters, contract))
        doc = h2_semantic.prepare_mappings(clusters, contract, config, h2_semantic.load_mappings())
        _save_mappings(doc)
        print(f"contract rules: {h2_contract.rule_counts(contract)}")
        print("clusters:")
        _print_counts(h2_semantic.semantic_counts(doc))
        for note in doc["invalidated"]:
            print("invalidated:", note)
        return

    doc = h2_semantic.load_mappings()
    if not h2_semantic.mappings_are_current(doc, contract):
        raise SystemExit("service mappings missing or stale; run `semantic h2 prepare` first")

    if args.step == "status":
        for key, value in config.readiness().items():
            print(f"  {key:<16} {value}")
        print("clusters:")
        _print_counts(h2_semantic.semantic_counts(doc))
        return

    if args.step == "classify":
        if not config.openrouter_api_key:
            raise SystemExit(
                "OPENROUTER_API_KEY is not set. No classifier call was made and no "
                "mapping was changed; semantic-pending clusters stay pending.")
        stats = h2_semantic.run_classifier(
            doc, _h2_clusters(contract), contract, config,
            h2_semantic.openrouter_transport(config),
            limit=args.limit, retry_failed=args.retry_failed)
        _save_mappings(doc)
        print(f"classifier ({config.classifier_model}), per cluster: {stats}")

    elif args.step == "jev":
        try:
            transport = h2_semantic.jev_http_transport(config)
        except h2_semantic.SemanticError as exc:
            raise SystemExit(f"{exc}. No Jev call was made and no mapping was changed.") from None
        try:
            stats = h2_semantic.run_jev(doc, contract, config, transport)
        except h2_semantic.SemanticError as exc:
            raise SystemExit(str(exc)) from None
        _save_mappings(doc)
        print(f"Jev ({config.jev_model}) by API: {stats}")

    elif args.step == "jev-export":
        state, questions = h2_semantic.export_jev_batch(doc, contract, config)
        _write_jev_batch(state, questions)
        _save_mappings(doc)
        print(f"{len(questions)} Jev case(s) -> {h2_semantic.JEV_STATE_FILE.name}, "
              f"{h2_semantic.JEV_QUESTIONS_FILE.name}")
        if not questions:
            print("nothing to verify: no classifier decision awaits Jev")

    elif args.step == "jev-import":
        path = args.file or h2_semantic.JEV_RESULTS_FILE
        if not path.exists():
            raise SystemExit(f"{path} not found; nothing imported")
        try:
            stats = h2_semantic.import_jev_results(
                doc, json.loads(path.read_text(encoding="utf-8")), config)
        except (h2_semantic.SemanticError, json.JSONDecodeError) as exc:
            raise SystemExit(str(exc)) from None
        _save_mappings(doc)
        print(f"Jev results imported: {stats}")

    elif args.step == "rebuild":
        _save_mappings(h2_semantic.refinalize(doc, config.jev_threshold))

    print("clusters now:")
    _print_counts(h2_semantic.semantic_counts(doc))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def load_environment(path: Path = REPO_ROOT / ".env") -> bool:
    """Load the project-root ``.env`` once, at startup.

    Optional: a missing file is not an error, and only the semantic steps
    need its keys.  Variables already set in the real OS environment take
    precedence (``override=False``).
    """
    return load_dotenv(path, override=False)


def main() -> None:
    load_environment()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def hospital_command(name: str, func, hospitals=("h1",)):
        p = sub.add_parser(name)
        p.add_argument("hospital", choices=list(hospitals))
        p.set_defaults(func=func)
        return p

    hospital_command("audit", cmd_audit, ("h1", "h2", "h4"))
    hospital_command("evaluate", cmd_evaluate)
    sub.add_parser("submission").set_defaults(func=cmd_submission)
    hospital_command("split", cmd_split).add_argument("--force", action="store_true")
    hospital_command("freeze", cmd_freeze).add_argument("--reason")
    hospital_command("research", cmd_research).add_argument(
        "study", choices=["temporal", "ablation"]
    )
    semantic = hospital_command("semantic", cmd_semantic, ("h2",))
    semantic.add_argument(
        "step", choices=["prepare", "status", "classify", "jev", "jev-export", "jev-import", "rebuild"])
    semantic.add_argument("--limit", type=int, default=None)
    semantic.add_argument("--retry-failed", action="store_true")
    semantic.add_argument("--file", type=Path, default=None)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
