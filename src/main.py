"""Command-line entry point.  Orchestration only: no audit logic lives here.

    python -m src.main audit h1          audit every invoice; write predictions
    python -m src.main audit h2          (offline: reads persisted service mappings)
    python -m src.main audit h3          (offline: reads committed, gated Jev missing-word
                                         reviews and refuses stale ones; writes artifacts too)
    python -m src.main audit h4          (offline, deterministic; writes artifacts too)
    python -m src.main audit h5          (offline: reads committed, reviewed Jev decisions
                                         and refuses stale ones; writes artifacts too)
    python -m src.main evaluate h1       score against the development split
                                         (and the locked holdout, while frozen)
    python -m src.main submission        outputs/hospital_{2,3,4,5}/submission.csv and
                                         the combined outputs/submission.csv (scored
                                         hospitals only: hospital_2, hospital_3,
                                         hospital_4, hospital_5; offline)

Hospital 2 semantic service identity (run deliberately, never by an audit):

    python -m src.main semantic h2 prepare      contract rules, clusters, mappings
    python -m src.main semantic h2 status       configuration readiness + counts
    python -m src.main semantic h2 classify     OpenRouter classifier, per pending
                                                cluster, bounded retries (OPENROUTER_API_KEY)
    python -m src.main semantic h2 jev          Jev by API (JEV_API_KEY, JEV_API_URL)
    python -m src.main semantic h2 jev-export   Playground state + questions JSON
    python -m src.main semantic h2 jev-import   apply jev_results.json (Playground)
    python -m src.main semantic h2 rebuild      recompute final decisions

Hospital 3 semantic review (Jev only, missing-word questions; run deliberately,
never by an audit):

    python -m src.main semantic h3 status               readiness, review coverage, staleness
    python -m src.main semantic h3 missing-word-run     Jev by API, one question per cluster
    python -m src.main semantic h3 missing-word-export  Playground request bodies
    python -m src.main semantic h3 missing-word-import  apply jev_missing_word_results.json

Hospital 5 semantic review (Jev only; run deliberately, never by an audit):

    python -m src.main semantic h5 status               readiness, review coverage, staleness
    python -m src.main semantic h5 candidates           normalisation proposals -> artifact
    python -m src.main semantic h5 normalize-run        Jev by API, one question per proposal
    python -m src.main semantic h5 normalize-export     Playground request bodies
    python -m src.main semantic h5 normalize-import     apply jev_normalization_results.json
    python -m src.main semantic h5 missing-word-run     Jev by API, one question per cluster
    python -m src.main semantic h5 missing-word-export  Playground request bodies
    python -m src.main semantic h5 missing-word-import  apply jev_missing_word_results.json
    python -m src.main semantic h5 probe                the constructed stress-test case(s)
    python -m src.main semantic h5 report               the semantic contribution report

Development evidence (reads labels; not needed to produce predictions):

    python -m src.main split h1          the locked dev/holdout split (refuses
                                         to redraw an existing one)
    python -m src.main freeze h1 [--reason TEXT]
    python -m src.main research h1 temporal|ablation

Hospitals 1 to 5 are all implemented; ``h1`` to ``h5`` are the accepted hospitals.
Hospital 4 has no semantic stage; Hospitals 2 to 5 have no labels.
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
from .hospital_3 import audit as h3_audit
from .hospital_3 import report as h3_report
from .hospital_3 import semantic as h3_semantic
from .hospital_4 import audit as h4_audit
from .hospital_5 import audit as h5_audit
from .hospital_5 import report as h5_report
from .hospital_5 import semantic as h5_semantic
from .hospital_5 import workflow as h5_workflow
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
H3_OUTPUTS = OUTPUTS / "hospital_3"
H4_OUTPUTS = OUTPUTS / "hospital_4"
H5_OUTPUTS = OUTPUTS / "hospital_5"
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
    if args.hospital == "h3":
        _audit_h3()
        return
    if args.hospital == "h4":
        _audit_h4()
        return
    if args.hospital == "h5":
        _audit_h5()
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
    # Hospitals 2, 3, 4 and 5 are the scored hospitals.
    _, results = _run_h2()
    h2_file = H2_OUTPUTS / "submission.csv"
    write_hospital_submission([r.result for r in results], h2_file, template=TEMPLATE)
    h3_file = H3_OUTPUTS / "submission.csv"
    write_hospital_submission([r.result for r in _run_h3()[1]], h3_file, template=TEMPLATE)
    h4_pipeline = h4_audit.H4Pipeline()
    h4_file = H4_OUTPUTS / "submission.csv"
    write_hospital_submission([r.result for r in h4_pipeline.run()], h4_file, template=TEMPLATE)
    h5_file = H5_OUTPUTS / "submission.csv"
    write_hospital_submission([r.result for r in _run_h5()[1]], h5_file, template=TEMPLATE)
    path = OUTPUTS / "submission.csv"
    files = {"hospital_2": h2_file, "hospital_3": h3_file, "hospital_4": h4_file, "hospital_5": h5_file}
    counts = combine_submissions(files, path)
    for name, file in files.items():
        print(f"{file}: {counts[name]} rows")
    print(f"{path}: {sum(counts.values())} rows {counts} (hospital_1 is not scored)")


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


def _run_h3():
    try:
        pipeline = h3_audit.H3Pipeline()
    except h3_semantic.StaleSemanticArtifacts as exc:
        raise SystemExit(f"hospital_3: {exc}") from None
    return pipeline, pipeline.run()


def _audit_h3() -> None:
    pipeline, results = _run_h3()
    info = h3_report.write_outputs(pipeline, results, template=TEMPLATE)
    res = [r.result for r in results]
    print(f"invoice occurrences: {len(pipeline.occurrences)}  invoice numbers (rows): {info['rows']}  "
          f"flagged rows: {sum(r.flagged for r in res)}")
    print(f"pricing_complete: {sum(r.pricing_complete for r in res)}  "
          f"correction_reconstructable: {sum(r.correction_reconstructable for r in res)}  "
          f"expected_total_cents blank: {sum(r.expected_total_cents is None for r in res)}")
    print("identity:")
    _print_counts(info["counts"])
    print(f"pricing traces (every line): {info['traces']}")
    # The contribution report reruns the audit with each identity stage switched
    # on in turn, and under each alternative contract reading, on the same data.
    h3_report.write_contribution_report(pipeline)
    print(f"written to {H3_OUTPUTS} (including semantic_contribution.md) and {h3_audit.ARTIFACTS}")


def cmd_semantic_h3(args) -> None:
    """Hospital 3's one Jev stage: missing-word questions.  Each run or import
    stores reviews; the gated decisions artifact is then rewritten from them."""
    step = args.step
    config = h3_semantic.JevConfig.from_env()
    pipeline = h3_audit.H3Pipeline(decisions={})
    requests = h3_semantic.missing_word_requests(pipeline.contract, pipeline.occurrences, pipeline.matcher, config)
    reviews = h3_semantic.load_reviews()

    def log(line: str) -> None:
        print("  " + line, flush=True)

    if step == "status":
        for key, value in config.readiness().items():
            print(f"  {key:<16} {value}")
        current, pending, stale = h3_semantic.partition(requests, reviews)
        print(f"  missing-word: {len(requests)} requests, {len(current)} current reviews, {len(pending)} pending "
              f"({len(stale)} stale)")
        return
    if step == "missing-word-run":
        try:
            transport = h3_semantic.http_transport(config)
        except h3_semantic.SemanticError as exc:
            raise SystemExit(f"{exc}. No Jev call was made and nothing was changed.") from None
        print("missing-word reviews:", h3_semantic.run_requests(requests, transport, workers=config.workers,
                                                                 log=log))
    elif step == "missing-word-export":
        n = h3_semantic.export_requests(requests, reviews)
        print(f"{n} pending request(s) -> {h3_semantic.MISSING_WORD_EXPORT}")
    elif step == "missing-word-import":
        path = args.file or h3_semantic.MISSING_WORD_RESULTS
        if not path.exists():
            raise SystemExit(f"{path} not found; nothing imported")
        try:
            print("missing-word results:", h3_semantic.import_results(
                json.loads(path.read_text(encoding="utf-8")), requests))
        except (h3_semantic.SemanticError, json.JSONDecodeError) as exc:
            raise SystemExit(str(exc)) from None
    counts = h3_semantic.write_decisions(requests, h3_semantic.load_reviews(), config.gates)
    print(f"missing-word decisions: {counts} -> {h3_semantic.MISSING_WORD_DECISIONS_FILE}")


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


def _run_h5():
    try:
        pipeline = h5_audit.H5Pipeline()
    except h5_semantic.StaleSemanticArtifacts as exc:
        raise SystemExit(f"hospital_5: {exc}") from None
    return pipeline, pipeline.run()


def _audit_h5() -> None:
    pipeline, results = _run_h5()
    info = h5_report.write_outputs(pipeline, results, template=TEMPLATE)
    res = [r.result for r in results]
    print(f"invoice occurrences: {len(pipeline.occurrences)}  invoice numbers (rows): {info['rows']}  "
          f"flagged rows: {sum(r.flagged for r in res)}")
    print(f"pricing_complete: {sum(r.pricing_complete for r in res)}  "
          f"correction_reconstructable: {sum(r.correction_reconstructable for r in res)}  "
          f"expected_total_cents blank: {sum(r.expected_total_cents is None for r in res)}")
    print("identity:")
    _print_counts(info["counts"])
    print(f"pricing traces (every line): {info['traces']}")
    # The stage-by-stage contribution report reruns the audit with each
    # semantic step switched on in turn, on the same data.
    h5_report.write_contribution_report(semantics=pipeline.semantics)
    print(f"written to {H5_OUTPUTS} (including semantic_contribution.md) and {h5_audit.ARTIFACTS}")


def cmd_semantic_h5(args) -> None:
    """Hospital 5's Jev stages.  Each run/import stores reviews; the derived
    vocabulary and decision artifacts are then rewritten from them."""
    step = args.step
    sem = h5_workflow.load_semantics(strict=False)
    config = sem.config

    def log(line: str) -> None:
        print("  " + line, flush=True)

    def transport():
        try:
            return h5_semantic.http_transport(config)
        except h5_semantic.SemanticError as exc:
            raise SystemExit(f"{exc}. No Jev call was made and nothing was changed.") from None

    def load_results(default: Path) -> object:
        path = args.file or default
        if not path.exists():
            raise SystemExit(f"{path} not found; nothing imported")
        return json.loads(path.read_text(encoding="utf-8"))

    def run_import(requests, default: Path, store: Path) -> dict:
        try:
            return h5_semantic.import_results(load_results(default), requests, store)
        except (h5_semantic.SemanticError, json.JSONDecodeError) as exc:
            raise SystemExit(str(exc)) from None

    if step == "status":
        for key, value in config.readiness().items():
            print(f"  {key:<16} {value}")
        for name, reqs, path in (("normalisation", sem.normalization_requests, h5_semantic.NORMALIZATION_REVIEWS),
                                 ("missing-word", sem.missing_word_requests, h5_semantic.MISSING_WORD_REVIEWS)):
            current, pending, stale = h5_semantic.partition(reqs, h5_semantic.load_reviews(path))
            print(f"  {name}: {len(reqs)} requests, {len(current)} current reviews, {len(pending)} pending "
                  f"({len(stale)} stale)")
        return
    if step == "report":
        h5_report.write_contribution_report()
        print(f"written to {H5_OUTPUTS / 'semantic_contribution.md'} and "
              f"{h5_audit.ARTIFACTS / 'semantic_contribution.json'}")
        return
    if step == "candidates":
        pass
    elif step == "normalize-run":
        print("normalisation reviews:", h5_semantic.run_requests(
            sem.normalization_requests, transport(), h5_semantic.NORMALIZATION_REVIEWS,
            workers=config.workers, log=log))
    elif step == "normalize-export":
        n = h5_semantic.export_requests(sem.normalization_requests,
                                        h5_semantic.load_reviews(h5_semantic.NORMALIZATION_REVIEWS),
                                        h5_semantic.NORMALIZATION_EXPORT)
        print(f"{n} pending request(s) -> {h5_semantic.NORMALIZATION_EXPORT}")
    elif step == "normalize-import":
        print("normalisation results:", run_import(sem.normalization_requests, h5_semantic.NORMALIZATION_RESULTS,
                                                   h5_semantic.NORMALIZATION_REVIEWS))
    else:
        # Missing-word questions are built from the reviewed lexicon, so they
        # cannot be asked before every normalisation review is current.
        if not sem.normalization_complete:
            raise SystemExit("normalisation reviews are incomplete or stale; run normalize-run (or "
                             "normalize-export / normalize-import) first")
        if step == "missing-word-run":
            print("missing-word reviews:", h5_semantic.run_requests(
                sem.missing_word_requests, transport(), h5_semantic.MISSING_WORD_REVIEWS,
                workers=config.workers, log=log))
        elif step == "missing-word-export":
            n = h5_semantic.export_requests(sem.missing_word_requests,
                                            h5_semantic.load_reviews(h5_semantic.MISSING_WORD_REVIEWS),
                                            h5_semantic.MISSING_WORD_EXPORT)
            print(f"{n} pending request(s) -> {h5_semantic.MISSING_WORD_EXPORT}")
        elif step == "missing-word-import":
            print("missing-word results:", run_import(sem.missing_word_requests, h5_semantic.MISSING_WORD_RESULTS,
                                                      h5_semantic.MISSING_WORD_REVIEWS))
        elif step == "probe":
            print("probe reviews:", h5_semantic.run_requests(
                h5_workflow.probe_requests(sem), transport(), h5_semantic.PROBE_REVIEWS, workers=1, log=log))

    sem = h5_workflow.load_semantics(strict=False)
    h5_workflow.write_candidates(sem)
    print(f"{len(sem.candidates)} normalisation proposals -> {h5_semantic.CANDIDATES_FILE}")
    print(f"gated vocabulary: {h5_workflow.write_vocab_artifacts(sem)}")
    if sem.normalization_complete:
        print(f"missing-word decisions: {h5_workflow.write_missing_word_decisions(sem)}")


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
    if args.hospital == "h3":
        if args.step not in H3_SEMANTIC_STEPS:
            raise SystemExit(f"semantic h3: unknown step {args.step!r}; choose from {', '.join(H3_SEMANTIC_STEPS)}")
        cmd_semantic_h3(args)
        return
    if args.hospital == "h5":
        if args.step not in H5_SEMANTIC_STEPS:
            raise SystemExit(f"semantic h5: unknown step {args.step!r}; choose from {', '.join(H5_SEMANTIC_STEPS)}")
        cmd_semantic_h5(args)
        return
    if args.step not in H2_SEMANTIC_STEPS:
        raise SystemExit(f"semantic h2: unknown step {args.step!r}; choose from {', '.join(H2_SEMANTIC_STEPS)}")
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

H2_SEMANTIC_STEPS = ("prepare", "status", "classify", "jev", "jev-export", "jev-import", "rebuild")
H3_SEMANTIC_STEPS = ("status", "missing-word-run", "missing-word-export", "missing-word-import")
H5_SEMANTIC_STEPS = ("status", "candidates", "normalize-run", "normalize-export", "normalize-import",
                     "missing-word-run", "missing-word-export", "missing-word-import", "probe", "report")

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

    hospital_command("audit", cmd_audit, ("h1", "h2", "h3", "h4", "h5"))
    hospital_command("evaluate", cmd_evaluate)
    sub.add_parser("submission").set_defaults(func=cmd_submission)
    hospital_command("split", cmd_split).add_argument("--force", action="store_true")
    hospital_command("freeze", cmd_freeze).add_argument("--reason")
    hospital_command("research", cmd_research).add_argument(
        "study", choices=["temporal", "ablation"]
    )
    semantic = hospital_command("semantic", cmd_semantic, ("h2", "h3", "h5"))
    semantic.add_argument("step", choices=list(dict.fromkeys(H2_SEMANTIC_STEPS + H3_SEMANTIC_STEPS
                                                             + H5_SEMANTIC_STEPS)))
    semantic.add_argument("--limit", type=int, default=None)
    semantic.add_argument("--retry-failed", action="store_true")
    semantic.add_argument("--file", type=Path, default=None)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
