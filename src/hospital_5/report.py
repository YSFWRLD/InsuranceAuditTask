"""Hospital 5: artifacts, outputs and the semantic contribution report.

Everything written here is derived from the pipeline's results and the stored
reviews; nothing is timestamped, so rerunning ``audit h5`` on the same inputs
rewrites byte-identical files.

The contribution report reruns the same audit with each stage switched on in
turn, on the same data, so what normalisation, contextual resolution, Jev's
missing-word review and financial equivalence each add is measured -- not
asserted.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from ..shared.submission import SUBMISSION_COLUMNS, UNCERTAINTY_COLUMNS
from . import semantic as S
from .audit import (
    ARTIFACTS, BAND_VALUES, BLANK_REASONS, EVIDENCE_BASIS, OUTPUTS, AuditPolicy, H5InvoiceResult, H5Pipeline,
)
from .contract import rule_counts, write_contract_rules
from .matcher import AMBIGUOUS, MATCHED, MATCHER_VERSION, UNKNOWN, cluster_descriptions
from .normalization import NORMALIZATION_VERSION, Lexicon, build_lexicon

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "submission_template.csv"


def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _blank(value) -> object:
    return "" if value is None else value


def _money(v) -> str:
    return "—" if v is None else f"{v:,}"


# ==========================================================================
# Identity artifacts
# ==========================================================================

_AMBIGUITY_REASONS = {
    "missing_discriminator": "only one contracted service fits, but the text omits its qualifier, specialty or "
                             "concept, so an uncontracted service of the same shape is not ruled out by text",
    "tied_candidates": "several contracted services fit the text equally",
    "unrecognised_token": "a word in the description could not be read by any accepted normalisation",
    "contradiction": "a word that was read contradicts every contracted service",
    "no_recognised_words": "no word in the description could be read",
}


def cluster_records(pipeline: H5Pipeline) -> list[dict]:
    by_key: dict[str, list] = defaultdict(list)
    for rl in pipeline.resolved:
        by_key[rl.match.identity_key].append(rl)
    clusters = cluster_descriptions((rl.line.description for rl in pipeline.resolved), pipeline.matcher)
    records = []
    for key in sorted(clusters):
        c, rls = clusters[key], by_key[key]
        m = c["matches"][0]
        records.append({
            "identity_key": key,
            "raw_examples": c["raw_descriptions"][:8],
            "distinct_raw_descriptions": len(c["raw_descriptions"]),
            "normalized_descriptions": c["normalized_descriptions"][:8],
            "line_occurrences": len(rls),
            "invoice_count": len({rl.occurrence.invoice_id for rl in rls}),
            "structural_status": m.status,
            "method": m.method,
            "selected_service": m.service,
            "words_read": list(m.words_read),
            "unread_tokens": list(m.unread_tokens),
            "contextual_tokens": m.contextual,
            "candidates": [{"service": x.service, "unit_basis": x.unit_basis, "matching_fields": list(x.matching_fields),
                            "missing_words": list(x.missing_words)} for x in m.candidates],
            "line_identity": dict(sorted(Counter(f"{rl.status}:{rl.identity.method}" for rl in rls).items())),
            "resolved_services": dict(sorted(Counter(rl.service for rl in rls if rl.service).items())),
            "ambiguity_reason": _AMBIGUITY_REASONS.get(m.method),
        })
    return records


def identity_counts(pipeline: H5Pipeline, records: Optional[list[dict]] = None) -> dict[str, int]:
    records = records if records is not None else cluster_records(pipeline)
    resolved = pipeline.resolved
    by_status = Counter(r["structural_status"] for r in records)
    methods = Counter(rl.identity.method for rl in resolved)
    return {
        "distinct_raw_descriptions": len({rl.line.description for rl in resolved}),
        "identity_clusters": len(records),
        "matched_clusters": by_status.get(MATCHED, 0),
        "ambiguous_clusters": by_status.get(AMBIGUOUS, 0),
        "unknown_clusters": by_status.get(UNKNOWN, 0),
        "total_line_occurrences": len(resolved),
        "matched_line_occurrences": sum(rl.status == MATCHED for rl in resolved),
        "matched_by_text": methods.get("text", 0),
        "matched_by_contextual_resolution": methods.get("text_contextual", 0),
        "matched_by_unit_basis_tiebreak": methods.get("unit_basis_tiebreak", 0),
        "matched_by_jev_missing_word": methods.get("jev_missing_word", 0),
        "matched_by_unit_basis_tiebreak_after_review": methods.get("unit_basis_tiebreak_after_review", 0),
        "ambiguous_line_occurrences": sum(rl.status == AMBIGUOUS for rl in resolved),
        "ambiguous_closed_by_jev": methods.get("jev_ambiguous_contracted", 0),
        "unknown_line_occurrences": sum(rl.status == UNKNOWN for rl in resolved),
        "unknown_by_jev": methods.get("jev_none_of_the_above", 0),
    }


def _provenance(pipeline: H5Pipeline) -> dict:
    out = {"contract_fingerprint": pipeline.contract.fingerprint, "normalization_version": NORMALIZATION_VERSION,
           "matcher_version": MATCHER_VERSION, "lexicon": pipeline.lexicon.name}
    if pipeline.semantics is not None:
        from .workflow import lexicon_sha256
        out.update({"lexicon_sha256": lexicon_sha256(pipeline.lexicon), "jev_model": pipeline.semantics.config.model,
                    "normalization_template": S.normalization_template().version,
                    "missing_word_template": S.missing_word_template().version})
    return out


def write_identity_artifacts(pipeline: H5Pipeline, directory: Path = ARTIFACTS) -> dict[str, int]:
    """description_clusters.json, service_mappings.json, unresolved.json."""
    records = cluster_records(pipeline)
    counts = identity_counts(pipeline, records)
    prov = _provenance(pipeline)
    _write_json(directory / "description_clusters.json", {**prov, "counts": counts, "clusters": records})
    _write_json(directory / "service_mappings.json", {
        **prov,
        "note": "structural_status is what text says. line_identity counts how each line was finally identified: "
                "text, contextual resolution, the unit-basis tie-break, or a gated Jev missing-word decision "
                "(per billed unit basis).",
        "mappings": {r["identity_key"]: {"structural_status": r["structural_status"], "method": r["method"],
                                         "service": r["selected_service"],
                                         "candidates": [x["service"] for x in r["candidates"]],
                                         "line_identity": r["line_identity"],
                                         "resolved_services": r["resolved_services"]} for r in records}})
    unresolved = []
    for r in records:
        still = sum(n for k, n in r["line_identity"].items() if not k.startswith("MATCHED"))
        if not still:
            continue
        families = sorted({f for x in r["candidates"] for f in pipeline.contract.rules_touching(x["service"])})
        unresolved.append({
            "identity_key": r["identity_key"], "structural_status": r["structural_status"], "method": r["method"],
            "reason": r["ambiguity_reason"], "raw_examples": r["raw_examples"][:5],
            "line_occurrences": r["line_occurrences"], "line_occurrences_still_unresolved": still,
            "invoice_count": r["invoice_count"], "line_identity": r["line_identity"],
            "candidates": [x["service"] for x in r["candidates"]], "candidate_rule_families": families})
    unresolved.sort(key=lambda u: (-u["line_occurrences_still_unresolved"], -u["invoice_count"], u["identity_key"]))
    _write_json(directory / "unresolved.json", {
        **prov, "ranking": "line occurrences still unresolved after every stage, then affected invoices",
        "count": len(unresolved), "unresolved": unresolved})
    return counts


# ==========================================================================
# Predictions, findings, traces
# ==========================================================================

PREDICTION_COLUMNS = SUBMISSION_COLUMNS + UNCERTAINTY_COLUMNS + (
    "provisional_expected_total_cents", "lines", "lines_identity_resolved",
    "lines_semantic_ambiguous_but_financially_resolved", "lines_financially_ambiguous", "lines_unknown_service",
    "blank_reasons")


def write_predictions(results: list[H5InvoiceResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PREDICTION_COLUMNS)
        for h in sorted(results, key=lambda h: h.result.invoice_id):
            r, s = h.result, h.line_status_counts()
            w.writerow([
                r.invoice_id, 1 if r.flagged else 0, "|".join(r.error_categories),
                _blank(r.expected_total_cents), r.billed_total_cents, f"{r.confidence:.2f}",
                r.confidence_band, int(r.pricing_complete), int(r.correction_reconstructable),
                _blank(r.maximum_contractually_payable_total_cents), "|".join(r.occurrence_ids),
                " ; ".join(r.uncertainty_reasons), _blank(h.provisional_expected_total_cents),
                sum(s.values()), s.get("identity_resolved", 0),
                s.get("semantic_ambiguous_but_financially_resolved", 0), s.get("financially_ambiguous", 0),
                s.get("unknown_service", 0), " | ".join(h.blank_reasons)])


def write_findings(results: list[H5InvoiceResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["invoice_id", "occurrence_id", "line_id", "category", "evidence_basis", "clause_reference",
                    "blocks_reconstruction", "detail"])
        for h in results:
            for a in h.occurrences:
                for f in a.findings:
                    basis, ref = EVIDENCE_BASIS[f.category]
                    w.writerow([h.result.invoice_id, a.occurrence.occurrence_id, f.line_id or "", f.category,
                                basis, ref, int(f.blocks_reconstruction), f.detail])


def _drop_empty(obj):
    if isinstance(obj, dict):
        out = {k: _drop_empty(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {}, False, "")}
    if isinstance(obj, list):
        return [_drop_empty(v) for v in obj]
    return obj


def _compact_pricing(p: dict, quantity: int) -> dict:
    """One candidate's pricing, without what is stated elsewhere."""
    p = dict(p)
    p.pop("service", None)                        # the key under which it is stored names it
    if p.get("repeat") == "no":
        p.pop("repeat")
    if p.get("payable_quantities") == [quantity]:
        p.pop("payable_quantities")
    stages = p.get("stages_cents") or {}
    if p.get("plausible_unit_rates_cents") == [stages.get("discount")]:
        p.pop("plausible_unit_rates_cents")
    if "discount" in p:                           # the tiers are in contract_rules.json
        p["discount"] = {k: v for k, v in p["discount"].items() if k not in ("tiers", "mode")}
    return p


def compact_trace(t: dict) -> dict:
    """A line trace as written to ``pricing_traces.jsonl``: the same content
    as the in-memory trace without repetition.  A text-matched line drops its
    one-item candidate list; per-candidate pricing drops what the contract
    rules or the billed fields already state; empty, null or false fields are
    dropped (absent means none / false / "no")."""
    t = dict(t)
    ident = dict(t.get("identity") or {})
    if ident.get("status") == MATCHED and ident.get("candidates") == [ident.get("service")]:
        ident.pop("candidates")
    t["identity"] = ident
    qty = (t.get("billed") or {}).get("quantity")
    p = t.get("pricing")
    if p and "stages_cents" in p:
        p = _compact_pricing(p, qty)
        p.pop("possible_line_totals_cents", None)    # stated once, at the top level
        t["pricing"] = p
    elif p and "candidates" in p:
        t["pricing"] = {"candidates": {s: _compact_pricing(c, qty) for s, c in p["candidates"].items()}}
    return _drop_empty(t)


def write_traces(results: list[H5InvoiceResult], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for h in results:
            for a in h.occurrences:
                for t in a.traces:
                    fh.write(json.dumps({"invoice_id": h.result.invoice_id, "occurrence_id": a.occurrence.occurrence_id,
                                         **compact_trace(t)}, separators=(",", ":"), ensure_ascii=False) + "\n")
                    n += 1
    return n


def coverage(results: list[H5InvoiceResult]) -> dict:
    res = [h.result for h in results]
    lines = Counter(o.financial_status for h in results for o in h.represented.outcomes)
    invoices_fe = sum(1 for h in results if h.result.expected_total_cents is not None
                      and h.line_status_counts().get("semantic_ambiguous_but_financially_resolved"))
    return {
        "invoice_rows": len(res),
        "flagged": sum(r.flagged for r in res),
        "pricing_complete": sum(r.pricing_complete for r in res),
        "correction_reconstructable": sum(r.correction_reconstructable for r in res),
        "expected_total_submitted": sum(r.expected_total_cents is not None for r in res),
        "expected_total_blank": sum(r.expected_total_cents is None for r in res),
        "rows_exact_via_financial_equivalence": invoices_fe,
        "represented_lines_by_financial_status": dict(sorted(lines.items())),
    }


def blank_report(results: list[H5InvoiceResult]) -> dict:
    counts = Counter(b for h in results for b in h.blank_reasons)
    primary = Counter(h.blank_reasons[0] for h in results if h.blank_reasons)
    return {
        "note": "An invoice can carry several reasons; 'primary' counts each blank once, under its most specific "
                "reason (the order of audit.BLANK_REASONS).",
        "blank_rows": sum(1 for h in results if h.result.expected_total_cents is None),
        "by_reason": dict(counts.most_common()),
        "by_primary_reason": dict(primary.most_common()),
        "rows": {h.result.invoice_id: h.blank_reasons for h in results if h.blank_reasons},
    }


# ==========================================================================
# Audit report
# ==========================================================================

def _line_block(t: dict) -> list[str]:
    ident, billed = t["identity"], t["billed"]
    rows = [
        ("description", f"`{t['raw_description']}` → `{t['identity_key']}`"),
        ("identity", f"{ident['status']} ({ident['method']})" + (f": **{ident['service']}**" if ident["service"] else "")),
        ("candidates", ", ".join(ident["candidates"]) or "none"),
        ("billed", f"{billed['quantity']} × {billed['unit_basis']} @ {_money(billed['unit_price_cents'])} = "
                   f"{_money(billed['line_total_cents'])} on {billed['service_date']}"),
    ]
    p = t.get("pricing") or {}
    if "stages_cents" in p:
        st = p["stages_cents"]
        rows.append(("stages: base → bundle → facility → tier → premium → discount",
                     " → ".join(_money(st[k]) for k in ("base", "bundle", "facility", "tier", "premium", "discount"))
                     + f" (multipliers {' / '.join(p['multipliers'])})"))
        rows.append(("plausible unit rates", ", ".join(map(_money, p["plausible_unit_rates_cents"]))))
    elif p.get("candidates"):
        rows.append(("candidate unit rates", "; ".join(f"{s}: {', '.join(map(_money, c['plausible_unit_rates_cents']))}"
                                                       for s, c in p["candidates"].items())))
    rows += [
        ("possible corrected totals", ", ".join(_money(v) for v in t["possible_line_totals_cents"])),
        ("financial status", t["financial_status"]),
        ("findings", ", ".join(f"`{f}`" for f in t["findings"]) or "none"),
    ]
    if t["uncertainty"]:
        rows.append(("uncertainty", " ; ".join(t["uncertainty"])))
    return [f"**{t['line_id']}**", "", "| | |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows], ""]


def audit_report(pipeline: H5Pipeline, results: list[H5InvoiceResult], counts: dict[str, int]) -> str:
    res = [h.result for h in results]
    row_cats = Counter(c for r in res for c in r.error_categories)
    occ_cats = Counter(c for h in results for a in h.occurrences for c in dict.fromkeys(f.category for f in a.findings))
    bands = Counter(r.confidence_band for r in res)
    reused = [h for h in results if len(h.occurrences) > 1]
    cov, blanks = coverage(results), blank_report(results)
    c = pipeline.contract
    out = [
        "# Hospital 5 - audit report",
        "",
        "Generated by `python -m src.main audit h5`. **There are no Hospital 5 labels.** Nothing below is an "
        "accuracy figure. Service identity uses deterministic matching over a Jev-reviewed lexicon and gated "
        "Jev missing-word decisions read from committed review files; the audit itself calls nothing. Every "
        "price and rule is Python.",
        "",
        f"Contract `{c.contract_number}` ({c.effective_from} to {c.effective_to}), fingerprint `{c.fingerprint[:16]}`, "
        f"matcher `{MATCHER_VERSION}`, normalisation `{NORMALIZATION_VERSION}`. The stage-by-stage account of what "
        "each semantic step added is in `semantic_contribution.md`.",
        "",
        "## Contract rules parsed",
        "",
        "| family | count |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in rule_counts(c).items()],
        "",
        "## Coverage",
        "",
        "| | count |",
        "|---|---|",
        f"| invoice occurrences audited | {len(pipeline.occurrences)} |",
        f"| invoice numbers (submission rows) | {len(res)} |",
        f"| numbers used by more than one occurrence | {len(reused)} |",
        f"| line occurrences | {len(pipeline.resolved)} |",
        "",
        "## Service identity",
        "",
        "| count | value |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in counts.items()],
        "",
        "## Results",
        "",
        "| | count |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in cov.items() if not isinstance(v, dict)],
        *[f"| represented lines: `{k}` | {v} |" for k, v in cov["represented_lines_by_financial_status"].items()],
        "",
        "| confidence band | invoices |",
        "|---|---|",
        *[f"| {b} ({BAND_VALUES[b]}) | {bands.get(b, 0)} |" for b in ("high", "medium", "low")],
        "",
        "Confidence is an evidence band for review priority, not a calibrated probability. Jev's probabilities "
        "are evidence about one description and are not this number.",
        "",
        "## Why totals are left blank",
        "",
        "| reason | rows (any) | rows (primary) |",
        "|---|---|---|",
        *[f"| {text} | {blanks['by_reason'].get(text, 0)} | {blanks['by_primary_reason'].get(text, 0)} |"
          for _, text in BLANK_REASONS if blanks["by_reason"].get(text)],
        "",
        "## Findings by category",
        "",
        "`rows` counts submitted rows carrying the category; `occurrences` counts physical invoice records.",
        "",
        "| category | rows | occurrences | evidence basis | clause |",
        "|---|---|---|---|---|",
        *[f"| `{k}` | {row_cats.get(k, 0)} | {n} | {EVIDENCE_BASIS[k][0]} | {EVIDENCE_BASIS[k][1]} |"
          for k, n in sorted(occ_cats.items(), key=lambda kv: (-kv[1], kv[0]))],
        "",
        "## Reused invoice numbers",
        "",
        "Each row represents the later occurrence. The earlier occurrence stays in every hospital-wide index and "
        "its findings are in `findings.csv`.",
        "",
        "| invoice number | represented occurrence | row categories | earlier occurrence findings |",
        "|---|---|---|---|",
        *[f"| {h.result.invoice_id} | {h.represented.occurrence.occurrence_id} | "
          f"{', '.join(h.result.error_categories) or 'none'} | "
          + "; ".join(f"{a.occurrence.occurrence_id}: {', '.join(dict.fromkeys(f.category for f in a.findings)) or 'none'}"
                      for a in h.occurrences[:-1]) + " |" for h in reused],
        "",
        "## Flagged invoices, line by line",
        "",
        "For each flagged invoice: the row, then every line that carries a finding. Amounts are in cents. The "
        "full trace of every line is in `artifacts/hospital_5/pricing_traces.jsonl`.",
        "",
    ]
    for h in results:
        r = h.result
        if not r.flagged:
            continue
        a = h.represented
        out += [
            f"### {r.invoice_id}",
            "",
            f"Occurrence `{a.occurrence.occurrence_id}`, patient {a.occurrence.patient_id}, facility "
            f"{a.occurrence.facility_code}, tier {a.occurrence.plan_tier}, invoice date {a.occurrence.invoice_date_raw}, "
            f"contract `{a.occurrence.contract_number}`. Billed {_money(r.billed_total_cents)}; expected "
            f"{_money(r.expected_total_cents)}; reconstructable {int(r.correction_reconstructable)}; confidence "
            f"{r.confidence:.2f} ({r.confidence_band})." + (f" Blank because: {'; '.join(h.blank_reasons)}."
                                                             if h.blank_reasons else ""),
            "",
            "Categories: " + ", ".join(f"`{x}`" for x in r.error_categories),
            "",
        ]
        invoice_level = [f for f in a.findings if f.line_id is None]
        if invoice_level:
            out += [*[f"- `{f.category}` ({EVIDENCE_BASIS[f.category][1]}): {f.detail}" for f in invoice_level], ""]
        for t in a.traces:
            if t.get("findings"):
                out += _line_block(t)
    return "\n".join(out).rstrip("\n") + "\n"


def write_outputs(pipeline: H5Pipeline, results: list[H5InvoiceResult], *, template: Path = TEMPLATE,
                  outputs: Path = OUTPUTS, artifacts: Path = ARTIFACTS) -> dict:
    """Every Hospital 5 artifact and output, deterministically."""
    from ..submission import write_hospital_submission
    from .workflow import write_candidates, write_missing_word_decisions, write_vocab_artifacts

    write_contract_rules(pipeline.contract, artifacts / "contract_rules.json")
    if pipeline.semantics is not None:
        write_candidates(pipeline.semantics, artifacts / "normalization_candidates.json")
        write_vocab_artifacts(pipeline.semantics, artifacts)
        write_missing_word_decisions(pipeline.semantics, artifacts / "missing_word_decisions.json")
    counts = write_identity_artifacts(pipeline, artifacts)
    n_traces = write_traces(results, artifacts / "pricing_traces.jsonl")
    _write_json(artifacts / "reconstruction_coverage.json", coverage(results))
    _write_json(artifacts / "blank_reasons.json", blank_report(results))
    write_predictions(results, outputs / "predictions.csv")
    write_findings(results, outputs / "findings.csv")
    rows = write_hospital_submission([h.result for h in results], outputs / "submission.csv", template=template)
    (outputs / "audit_report.md").write_text(audit_report(pipeline, results, counts), encoding="utf-8")
    return {"counts": counts, "traces": n_traces, "rows": rows}


# ==========================================================================
# Semantic contribution report
# ==========================================================================

def _stage_metrics(pipeline: H5Pipeline, results: list[H5InvoiceResult]) -> dict:
    counts = identity_counts(pipeline)
    res = [h.result for h in results]
    fe_lines = sum(1 for h in results for o in h.represented.outcomes
                   if o.financial_status == "semantic_ambiguous_but_financially_resolved")
    return {
        "raw_descriptions": counts["distinct_raw_descriptions"],
        "identity_clusters": counts["identity_clusters"],
        "matched_clusters": counts["matched_clusters"],
        "ambiguous_clusters": counts["ambiguous_clusters"],
        "unknown_clusters": counts["unknown_clusters"],
        "identified_lines": counts["matched_line_occurrences"],
        "ambiguous_lines": counts["ambiguous_line_occurrences"],
        "unknown_lines": counts["unknown_line_occurrences"],
        "lines_by_method": dict(sorted(Counter(rl.identity.method for rl in pipeline.resolved).items())),
        "invoices_reconstructable": sum(r.correction_reconstructable for r in res),
        "expected_total_blanks": sum(r.expected_total_cents is None for r in res),
        "flagged": sum(r.flagged for r in res),
        "semantic_ambiguous_but_financially_resolved_lines": fe_lines,
        "ambiguous_lines_whose_candidates_price_identically": sum(
            1 for h in results for o in h.represented.outcomes if o.candidates_agree),
        "ambiguous_lines_with_two_or_more_candidates": sum(
            1 for h in results for rl in h.represented.lines if rl.status == AMBIGUOUS and len(rl.identity.candidates) > 1),
        "invoices_exact_with_an_ambiguous_line": sum(
            1 for h in results if h.result.expected_total_cents is not None
            and any(rl.status == AMBIGUOUS for rl in h.represented.lines)),
    }


def _decisions_without_review_closure(sem):
    """The decisions minus every "closed to the contracted candidates" outcome:
    what the tie-break after review adds is measured against this."""
    return {k: d for k, d in sem.missing_word_decisions.items() if d.outcome != "ambiguous_contracted"}


def _without_human_review(sem) -> tuple[Lexicon, dict]:
    """The lexicon and missing-word decisions as they would be with no human
    review override.  Each question that lexicon produces is answered from the
    stored reviews by exact request body -- including reviews retired because
    an override made their question unnecessary.  Nothing is asked."""
    from ..shared.data import load_occurrences
    from .matcher import H5Matcher
    from .workflow import JSONL, line_keys

    by_id = {c.candidate_id: c for c in sem.candidates}
    n_reviews = S.load_reviews(S.NORMALIZATION_REVIEWS)
    gated = {r.review_id: S.gate_normalization(by_id[r.review_id], n_reviews[r.review_id]["answer"],
                                               sem.config.gates, overrides={})[0]
             for r in sem.normalization_requests}
    lexicon = build_lexicon(gated, sem.candidates)
    clusters = S.missing_word_clusters(line_keys(load_occurrences(JSONL)), H5Matcher(sem.contract, lexicon),
                                       sem.config.gates.max_candidates)
    requests = S.missing_word_requests(clusters, sem.contract, S.missing_word_template(), sem.config.model)
    stored = S.load_reviews(S.MISSING_WORD_REVIEWS)
    decisions = {}
    for r in requests:
        rec = stored.get(r.review_id)
        if rec is not None and rec["request_sha256"] == r.request_sha256:
            key, basis = r.review_id.rsplit(" | ", 1)
            decisions[(key, basis)] = S.gate_missing_word(r, rec["answer"], sem.config.gates)
    return lexicon, decisions


def contribution_stages(semantics=None) -> list[tuple[str, str, dict, H5Pipeline, list]]:
    """(name, description, metrics, pipeline, results) for each cumulative stage,
    plus two ablations, all on the same data."""
    from .workflow import load_semantics

    sem = semantics or load_semantics(strict=True)
    no_fe = AuditPolicy(financial_equivalence=False)
    specs = [
        ("deterministic_baseline", "exact contract words only (suffixes stripped, punctuation collapsed); "
         "structural matching and the unit-basis tie-break; no review; Hospital 4's reconstruction rule",
         Lexicon.exact_only(), {}, no_fe),
        ("safe_global_normalization", "+ tokens Jev judged safe everywhere (after gating)",
         sem.global_lexicon, {}, no_fe),
        ("contextual_normalization", "+ context-required tokens, resolved by the surrounding words",
         sem.lexicon, {}, no_fe),
        ("jev_missing_word_resolution", "+ gated Jev missing-word decisions per cluster and billed basis",
         sem.lexicon, sem.missing_word_decisions, no_fe),
        ("financial_equivalence", "+ financial equivalence and dependency-aware uncertainty (FINAL)",
         sem.lexicon, sem.missing_word_decisions, AuditPolicy()),
        ("ablation_no_jev_missing_word", "FINAL without the Jev missing-word decisions",
         sem.lexicon, {}, AuditPolicy()),
        ("ablation_no_tiebreak_after_review", "FINAL without the unit-basis tie-break after a review closed a "
         "cluster to its contracted candidates", sem.lexicon, _decisions_without_review_closure(sem), AuditPolicy()),
        ("ablation_without_human_review_overrides", "FINAL without artifacts/hospital_5/human_review_overrides.json "
         "(ENT read in context; BD rejected)", *_without_human_review(sem), AuditPolicy()),
        ("ablation_no_financial_equivalence_no_jev", "reviewed lexicon only; Hospital 4's reconstruction rule",
         sem.lexicon, {}, no_fe),
    ]
    out = []
    for name, desc, lex, decisions, policy in specs:
        p = H5Pipeline(lexicon=lex, decisions=decisions, policy=policy, check_csv=False)
        p.semantics = sem
        results = p.run()
        out.append((name, desc, _stage_metrics(p, results), p, results))
    return out


def _jev_missing_word_summary(sem) -> dict:
    outcomes = Counter(d.outcome for d in sem.missing_word_decisions.values())
    choices = Counter(a["choice"] for a in sem.missing_word_answers.values())
    return {
        "clusters_reviewed": len(sem.missing_word_answers),
        "lines_in_reviewed_clusters": sum(r.state["description"]["occurrence_count"] for r in sem.missing_word_requests),
        "gated_outcomes": dict(sorted(outcomes.items())),
        "raw_choices": {"specific_service": sum(n for k, n in choices.items()
                                                 if k not in (S.AMBIGUOUS_OPTION, S.NONE_OPTION)),
                        "ambiguous_contracted_service": choices.get(S.AMBIGUOUS_OPTION, 0),
                        "none_of_the_above_or_unknown": choices.get(S.NONE_OPTION, 0)},
    }


def _normalization_summary(sem) -> dict:
    gated = Counter(d["decision"] for d in sem.normalization_decisions.values())
    raw = Counter(d["choice"] for d in sem.normalization_decisions.values())
    return {"proposals_reviewed": len(sem.normalization_decisions),
            "raw_choices": dict(sorted(raw.items())), "gated_decisions": dict(sorted(gated.items())),
            "tokens_global": len(sem.lexicon.global_map), "tokens_contextual": len(sem.lexicon.contextual)}


def contribution_report(stages, sem) -> tuple[str, dict]:
    names = [s[0] for s in stages]
    metrics = {s[0]: s[2] for s in stages}
    final_results = stages[names.index("financial_equivalence")][4]
    blanks = blank_report(final_results)
    doc = {"stages": {s[0]: {"description": s[1], **s[2]} for s in stages},
           "normalization_review": _normalization_summary(sem), "missing_word_review": _jev_missing_word_summary(sem),
           "final_blank_reasons": {k: v for k, v in blanks.items() if k != "rows"}}
    keys = ["identity_clusters", "matched_clusters", "ambiguous_clusters", "unknown_clusters", "identified_lines",
            "ambiguous_lines", "unknown_lines", "invoices_reconstructable", "expected_total_blanks",
            "semantic_ambiguous_but_financially_resolved_lines", "invoices_exact_with_an_ambiguous_line", "flagged"]
    cumulative = names[:5]

    def delta(a: str, b: str, k: str) -> str:
        d = metrics[b][k] - metrics[a][k]
        return f"{d:+d}" if d else "0"

    lines = [
        "# Hospital 5 - semantic contribution report",
        "",
        "Generated by `python -m src.main semantic h5 report` (and by `audit h5`). Every stage is the same audit "
        "on the same data with one more semantic step switched on, so each difference below is caused by that "
        "step alone. The first four stages use Hospital 4's reconstruction rule (a total is proven only if every "
        "line's identity is MATCHED and every input is settled; a malformed date blocks); the fifth switches to "
        "financial equivalence. There are no Hospital 5 labels: these are coverage counts, not accuracy.",
        "",
        "## Stages",
        "",
        "| metric | " + " | ".join(f"`{n}`" for n in cumulative) + " |",
        "|---|" + "---|" * len(cumulative),
        *[f"| {k} | " + " | ".join(str(metrics[n][k]) for n in cumulative) + " |" for k in ["raw_descriptions"] + keys],
        "",
        "Change from the previous stage:",
        "",
        "| metric | " + " | ".join(f"`{n}`" for n in cumulative[1:]) + " |",
        "|---|" + "---|" * (len(cumulative) - 1),
        *[f"| {k} | " + " | ".join(delta(a, b, k) for a, b in zip(cumulative, cumulative[1:])) + " |" for k in keys],
        "",
        "Stage descriptions:",
        "",
        *[f"- `{s[0]}`: {s[1]}" for s in stages],
        "",
        "Line identity by method, per stage:",
        "",
        "| method | " + " | ".join(f"`{n}`" for n in cumulative) + " |",
        "|---|" + "---|" * len(cumulative),
    ]
    methods = sorted({m for n in cumulative for m in metrics[n]["lines_by_method"]})
    lines += [f"| {m} | " + " | ".join(str(metrics[n]["lines_by_method"].get(m, 0)) for n in cumulative) + " |"
              for m in methods]
    nr, mw = doc["normalization_review"], doc["missing_word_review"]
    base, glob, ctx, jev, fe = (metrics[n] for n in cumulative)
    lines += [
        "",
        "## Jev normalisation review",
        "",
        f"{nr['proposals_reviewed']} proposals, one question each. Raw choices: {nr['raw_choices']}. After the "
        f"gates: {nr['gated_decisions']}. Lexicon: {nr['tokens_global']} global tokens, {nr['tokens_contextual']} "
        "contextual tokens. See `artifacts/hospital_5/{safe_global,context_required,rejected}_vocab.json`.",
        "",
        "## Jev missing-word review",
        "",
        f"{mw['clusters_reviewed']} clusters (identity key × billed unit basis) covering "
        f"{mw['lines_in_reviewed_clusters']} lines, one question each. Raw choices: {mw['raw_choices']}. Gated "
        f"outcomes: {mw['gated_outcomes']}.",
        "",
        "A service is accepted only when Jev chose it with P >= 0.90 and confidence >= 0.80, and the description "
        "evidences all but at most one of its name slots. A single-candidate closure rule (which counted Jev's "
        "mass on `ambiguous_contracted_service` as support for the only candidate) was tried after the first run "
        "and removed in review: with one candidate that mass is the reviewer hedging over the missing word, not "
        "confirming the service. The four clusters it would have accepted stay unresolved (see the decision log).",
        "",
        f"One cluster was closed to its two contracted candidates, and the unit basis then separates them "
        f"(the tie-break after review). Without it: reconstructable invoices "
        f"{metrics['ablation_no_tiebreak_after_review']['invoices_reconstructable']}, identified lines "
        f"{metrics['ablation_no_tiebreak_after_review']['identified_lines']}.",
        "",
        "## Human-reviewed overrides",
        "",
        "`artifacts/hospital_5/human_review_overrides.json` holds two human-reviewed decisions with provenance. "
        "`ent -> otolaryngologic` is allowed in context only: ENT is read as the specialty where a contracted "
        "Otolaryngologic service fits the other words (`SUPV ENT SPCM ANLY`, `CONT ENT TRANSP SVC`) and stays "
        "unread elsewhere. `bd -> bedside` is rejected. Neither is global, and no closure rule is involved: the "
        "descriptions they resolve carry the discriminator, abbreviated. Without the file: reconstructable "
        f"invoices {metrics['ablation_without_human_review_overrides']['invoices_reconstructable']}, identified "
        f"lines {metrics['ablation_without_human_review_overrides']['identified_lines']} (FINAL: "
        f"{fe['invoices_reconstructable']} and {fe['identified_lines']}).",
        "",
        f"Effect with Hospital 4's reconstruction rule (stage 3 -> 4): identified lines "
        f"{delta(cumulative[2], cumulative[3], 'identified_lines')}, reconstructable invoices "
        f"{delta(cumulative[2], cumulative[3], 'invoices_reconstructable')}. Effect with financial equivalence "
        f"(ablation without Jev -> FINAL): identified lines "
        f"{fe['identified_lines'] - metrics['ablation_no_jev_missing_word']['identified_lines']:+d}, reconstructable "
        f"invoices {fe['invoices_reconstructable'] - metrics['ablation_no_jev_missing_word']['invoices_reconstructable']:+d}.",
        "",
        "## Financial equivalence",
        "",
        f"Stage 4 -> 5: reconstructable invoices {delta(cumulative[3], cumulative[4], 'invoices_reconstructable')}; "
        f"{fe['semantic_ambiguous_but_financially_resolved_lines']} lines are semantically ambiguous but "
        f"financially resolved; {fe['invoices_exact_with_an_ambiguous_line']} submitted totals contain such a line. "
        "Financial equivalence also removes blanks caused by uncertainty that does not reach the amount: a "
        "malformed date on a service whose price does not depend on the day (or whose possible days all price "
        "the same), and a possible repeat, trigger or partner that every reading prices the same.",
        "",
        f"Measured without the Jev missing-word stage (where ambiguous lines remain), financial equivalence moves "
        f"reconstructable invoices from {metrics['ablation_no_financial_equivalence_no_jev']['invoices_reconstructable']}"
        f" to {metrics['ablation_no_jev_missing_word']['invoices_reconstructable']} and resolves "
        f"{metrics['ablation_no_jev_missing_word']['semantic_ambiguous_but_financially_resolved_lines']} ambiguous "
        "lines financially: without a review, every ambiguous line keeps an uncontracted reading, which has no "
        "contract price. Even if every ambiguous line were treated as certainly contracted, its candidates would "
        f"price identically on {metrics['ablation_no_jev_missing_word']['ambiguous_lines_whose_candidates_price_identically']}"
        f" of the {metrics['ablation_no_jev_missing_word']['ambiguous_lines_with_two_or_more_candidates']} "
        "ambiguous lines that have two or more candidates (a line with one candidate trivially agrees with "
        "itself and is not counted). Structural candidates differ in base rate, multipliers or unit basis, so "
        "equivalence across services is rare in this contract; the mechanism is kept because it is correct, "
        "not because it is productive here.",
        "",
        "Ablations:",
        "",
        "| run | identified lines | ambiguous lines | reconstructable | blanks |",
        "|---|---|---|---|---|",
        *[f"| `{n}` | {metrics[n]['identified_lines']} | {metrics[n]['ambiguous_lines']} | "
          f"{metrics[n]['invoices_reconstructable']} | {metrics[n]['expected_total_blanks']} |"
          for n in names],
        "",
        "## Final: why the remaining totals are blank",
        "",
        f"{fe['invoices_reconstructable']} of {fe['invoices_reconstructable'] + fe['expected_total_blanks']} "
        f"invoice rows carry a proven expected total; {fe['expected_total_blanks']} are blank.",
        "",
        "| reason | rows (any) | rows (primary) |",
        "|---|---|---|",
        *[f"| {text} | {blanks['by_reason'].get(text, 0)} | {blanks['by_primary_reason'].get(text, 0)} |"
          for _, text in BLANK_REASONS if blanks["by_reason"].get(text)],
        "",
        "Every blank above is a case where some still-possible reading of the invoice changes the corrected "
        "amount, or where no reading has a contract price. None is blank merely because a service name is "
        "ambiguous: an ambiguous line whose readings all price the same is counted as resolved.",
    ]
    return "\n".join(lines).rstrip("\n") + "\n", doc


def write_contribution_report(outputs: Path = OUTPUTS, artifacts: Path = ARTIFACTS, semantics=None) -> str:
    from .workflow import load_semantics

    sem = semantics or load_semantics(strict=True)
    stages = contribution_stages(sem)
    text, doc = contribution_report(stages, sem)
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "semantic_contribution.md").write_text(text, encoding="utf-8")
    _write_json(artifacts / "semantic_contribution.json", doc)
    return text
