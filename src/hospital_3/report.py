"""Hospital 3: artifacts, outputs, and the contribution and sensitivity report.

Everything written here is derived from the pipeline's results, the contract
package and the committed reviews; nothing is timestamped, so rerunning
``audit h3`` on the same inputs rewrites byte-identical files.

The contribution report reruns the same audit with each identity stage
switched on in turn, on the same data, so what the H3 lexicon, the ENT
reading, Jev's missing-word review and financial equivalence each add is
measured -- not asserted.  The sensitivity section reruns the final audit
under each alternative contract reading (``audit.AuditPolicy``), so how much
each reading moves the submission is measured too.
"""

from __future__ import annotations

import csv
import dataclasses
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from ..shared.submission import SUBMISSION_COLUMNS, UNCERTAINTY_COLUMNS
from . import semantic as S
from .audit import (
    ARTIFACTS, BAND_VALUES, BLANK_REASONS, EVIDENCE_BASIS, OUTPUTS, AuditPolicy, H3InvoiceResult, H3Pipeline,
)
from .contract import AMENDMENT_1, APPENDIX_B, rule_counts, write_contract_rules
from .matcher import AMBIGUOUS, MATCHED, MATCHER_VERSION, UNKNOWN, H3Matcher, cluster_descriptions
from .normalization import NORMALIZATION_VERSION, Lexicon, acronym_evidence, build_lexicon, normalize_description

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
    "unrecognised_token": "a word in the description could not be read",
    "contradiction": "a word that was read contradicts every contracted service",
    "no_recognised_words": "no word in the description could be read",
}


def _descriptions(pipeline: H3Pipeline) -> list[str]:
    return [li.description for o in pipeline.occurrences for li in o.line_items]


def cluster_records(pipeline: H3Pipeline) -> list[dict]:
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
            "candidates": [{"service": x.service, "unit_basis": x.unit_basis, "missing_fields": list(x.missing_fields),
                            "missing_words": list(x.missing_words)} for x in m.candidates],
            "line_identity": dict(sorted(Counter(f"{rl.status}:{rl.identity.method}" for rl in rls).items())),
            "resolved_services": dict(sorted(Counter(rl.service for rl in rls if rl.service).items())),
            "ambiguity_reason": _AMBIGUITY_REASONS.get(m.method),
        })
    return records


def identity_counts(pipeline: H3Pipeline, records: Optional[list[dict]] = None) -> dict[str, int]:
    records = records if records is not None else cluster_records(pipeline)
    resolved = pipeline.resolved
    by_status = Counter(r["structural_status"] for r in records)
    methods = Counter(rl.identity.method for rl in resolved)
    return {
        "distinct_raw_descriptions": len({rl.line.description for rl in resolved}),
        "distinct_normalized_descriptions": len({normalize_description(rl.line.description) for rl in resolved}),
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
        "lines_reading_ent_in_context": sum(1 for rl in resolved if rl.match.contextual.get("ent", {}).get("surviving")),
    }


def _provenance(pipeline: H3Pipeline) -> dict:
    return {"contract_fingerprint": pipeline.contract.fingerprint, "normalization_version": NORMALIZATION_VERSION,
            "matcher_version": MATCHER_VERSION, "lexicon": pipeline.lexicon.name,
            "missing_word_template": S.load_template().version, "jev_model": S.JevConfig.from_env().model}


def vocabulary_evidence(pipeline: H3Pipeline) -> dict:
    lex = pipeline.lexicon
    ev = {t: {"classification": e.classification, "distinct_descriptions": e.descriptions, "readings": e.readings,
              **({"reason": e.reason} if e.reason else {}),
              **({"slot_conflicts": list(e.slot_conflicts)} if e.slot_conflicts else {})}
          for t, e in sorted(lex.evidence.items())}
    surviving: dict[str, Counter] = defaultdict(Counter)
    for rl in pipeline.resolved:
        for tok, rec in rl.match.contextual.items():
            surviving[tok][" | ".join(rec["surviving"]) or "(unread: no reading fits)"] += 1
    for tok, cnt in surviving.items():
        ev[tok]["lines_by_surviving_reading"] = dict(sorted(cnt.items()))
    return {
        "note": "Every reading is derived by letter rules from Hospital 3's own contract vocabulary; the "
                "classification is decided by Hospital 3's own descriptions (normalization.py). No other "
                "hospital's abbreviation table or review is consulted.",
        "counts": dict(Counter(e.classification for e in lex.evidence.values())),
        "tokens": ev,
        "clinical_acronyms": acronym_evidence(pipeline.contract, _descriptions(pipeline), lex),
    }


def write_identity_artifacts(pipeline: H3Pipeline, directory: Path = ARTIFACTS) -> dict[str, int]:
    """vocabulary_evidence.json, description_clusters.json, service_mappings.json, unresolved.json."""
    records = cluster_records(pipeline)
    counts = identity_counts(pipeline, records)
    prov = _provenance(pipeline)
    _write_json(directory / "vocabulary_evidence.json", {**prov, **vocabulary_evidence(pipeline)})
    _write_json(directory / "description_clusters.json", {**prov, "counts": counts, "clusters": records})
    _write_json(directory / "service_mappings.json", {
        **prov,
        "note": "structural_status is what text says. line_identity counts how each line was finally identified: "
                "text, contextual resolution, the unit-basis tie-break, or a gated Jev missing-word decision "
                "(per billed unit basis). Identity never depends on a date or a price.",
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
# Amendment summary
# ==========================================================================

def amendment_summary(pipeline: H3Pipeline, results: list[H3InvoiceResult]) -> dict:
    """How the amendment date split the lines, from the line traces."""
    c = pipeline.contract
    by_line = {o.trace["line_id"]: o for h in results for a in h.occurrences for o in a.outcomes}
    period = Counter()
    amended = Counter()
    added = []
    cross = Counter()
    for rl in pipeline.resolved:
        o = by_line[rl.line_id]
        docs = tuple(o.rate_documents)
        period[" + ".join(d or "not contracted" for d in docs) or "no contract price (identity not settled)"] += 1
        if rl.service and c.is_amended(rl.service):
            amended[docs[0] if len(docs) == 1 else "either (malformed date)"] += 1
            if (rl.service_date and rl.occurrence.invoice_date and rl.service_date < c.amendment_date
                    <= rl.occurrence.invoice_date):
                cross["service_before_invoice_on_or_after"] += 1
        if any(c.is_added(s) for s in rl.possible_services):
            added.append({"line_id": rl.line_id, "invoice_id": rl.occurrence.invoice_id,
                          "service_date": rl.line.service_date_raw, "status": rl.status,
                          "method": rl.identity.method, "service": rl.service,
                          "findings": o.trace.get("findings", [])})
    pre = [a for a in added if "service_not_contracted_on_date" in a["findings"]]
    return {
        "effective_date": c.amendment_effective,
        "basis": "Service Date (A1.1.2); the invoice date is never used for pricing",
        "lines_by_rate_source": dict(sorted(period.items())),
        "lines_of_amended_services_by_rate_source": dict(sorted(amended.items())),
        "amended_service_lines_dated_before_the_amendment_on_invoices_issued_after": cross[
            "service_before_invoice_on_or_after"],
        "lines_of_added_services": len(added),
        "lines_of_added_services_before_effective_date": len(pre),
        "added_service_lines": added,
    }


# ==========================================================================
# Predictions, findings, traces
# ==========================================================================

PREDICTION_COLUMNS = SUBMISSION_COLUMNS + UNCERTAINTY_COLUMNS + (
    "provisional_expected_total_cents", "lines", "lines_identity_resolved",
    "lines_semantic_ambiguous_but_financially_resolved", "lines_financially_ambiguous", "lines_unknown_service",
    "blank_reasons")


def write_predictions(results: list[H3InvoiceResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PREDICTION_COLUMNS)
        for h in sorted(results, key=lambda h: h.result.invoice_id):
            r = h.result
            s = Counter(o.financial_status for o in h.represented.outcomes)
            w.writerow([
                r.invoice_id, 1 if r.flagged else 0, "|".join(r.error_categories),
                _blank(r.expected_total_cents), r.billed_total_cents, f"{r.confidence:.2f}",
                r.confidence_band, int(r.pricing_complete), int(r.correction_reconstructable),
                _blank(r.maximum_contractually_payable_total_cents), "|".join(r.occurrence_ids),
                " ; ".join(r.uncertainty_reasons), _blank(h.provisional_expected_total_cents),
                sum(s.values()), s.get("identity_resolved", 0),
                s.get("semantic_ambiguous_but_financially_resolved", 0), s.get("financially_ambiguous", 0),
                s.get("unknown_service", 0), " | ".join(h.blank_reasons)])


def write_findings(results: list[H3InvoiceResult], path: Path) -> None:
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
    p.pop("service", None)
    if p.get("repeat") == "no":
        p.pop("repeat")
    if p.get("payable_quantities") == [quantity]:
        p.pop("payable_quantities")
    stages = p.get("stages_cents") or {}
    if p.get("plausible_unit_rates_cents") == [stages.get("discount")]:
        p.pop("plausible_unit_rates_cents")
    if "discount" in p:
        p["discount"] = {k: v for k, v in p["discount"].items() if k not in ("tiers", "mode")}
    return p


def compact_trace(t: dict) -> dict:
    """A line trace as written to ``pricing_traces.jsonl``: the in-memory trace
    without repetition; empty, null or false fields are dropped."""
    t = dict(t)
    ident = dict(t.get("identity") or {})
    if ident.get("status") == MATCHED and ident.get("candidates") == [ident.get("service")]:
        ident.pop("candidates")
    t["identity"] = ident
    qty = (t.get("billed") or {}).get("quantity")
    p = t.get("pricing")
    if p and "rate_period" in p and "candidates" not in p:
        p = _compact_pricing(p, qty)
        p.pop("possible_line_totals_cents", None)
        t["pricing"] = p
    elif p and "candidates" in p:
        t["pricing"] = {"candidates": {s: _compact_pricing(c, qty) for s, c in p["candidates"].items()}}
    return _drop_empty(t)


def write_traces(results: list[H3InvoiceResult], path: Path) -> int:
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


def coverage(results: list[H3InvoiceResult]) -> dict:
    res = [h.result for h in results]
    lines = Counter(o.financial_status for h in results for o in h.represented.outcomes)
    return {
        "invoice_rows": len(res),
        "flagged": sum(r.flagged for r in res),
        "pricing_complete": sum(r.pricing_complete for r in res),
        "correction_reconstructable": sum(r.correction_reconstructable for r in res),
        "expected_total_submitted": sum(r.expected_total_cents is not None for r in res),
        "expected_total_blank": sum(r.expected_total_cents is None for r in res),
        "rows_exact_with_an_ambiguous_line": sum(
            1 for h in results if h.result.expected_total_cents is not None
            and any(rl.status == AMBIGUOUS for rl in h.represented.lines)),
        "represented_lines_by_financial_status": dict(sorted(lines.items())),
    }


def blank_report(results: list[H3InvoiceResult]) -> dict:
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
        rows.append(("rate in force (by Service Date)", " or ".join(x or "not contracted" for x in p["rate_period"])))
        rows.append(("stages: base → bundle → facility → tier → premium → discount",
                     " → ".join(_money(st[k]) for k in ("base", "bundle", "facility", "tier", "premium", "discount"))))
        rows.append(("plausible unit rates", ", ".join(map(_money, p["plausible_unit_rates_cents"]))))
    elif p.get("rate_period") is not None:
        rows.append(("rate in force (by Service Date)", " or ".join(x or "not contracted" for x in p["rate_period"])))
    elif p.get("candidates"):
        rows.append(("candidate unit rates", "; ".join(
            f"{s}: {', '.join(map(_money, c.get('plausible_unit_rates_cents', []))) or 'not contracted'}"
            for s, c in p["candidates"].items())))
    rows += [
        ("possible corrected totals", ", ".join(_money(v) for v in t["possible_line_totals_cents"])),
        ("financial status", t["financial_status"]),
        ("findings", ", ".join(f"`{f}`" for f in t["findings"]) or "none"),
    ]
    if t["uncertainty"]:
        rows.append(("uncertainty", " ; ".join(t["uncertainty"])))
    return [f"**{t['line_id']}**", "", "| | |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows], ""]


def audit_report(pipeline: H3Pipeline, results: list[H3InvoiceResult], counts: dict[str, int],
                 amendment: dict) -> str:
    res = [h.result for h in results]
    row_cats = Counter(c for r in res for c in r.error_categories)
    occ_cats = Counter(c for h in results for a in h.occurrences for c in dict.fromkeys(f.category for f in a.findings))
    bands = Counter(r.confidence_band for r in res)
    reused = [h for h in results if len(h.occurrences) > 1]
    cov, blanks = coverage(results), blank_report(results)
    c = pipeline.contract
    out = [
        "# Hospital 3 - audit report",
        "",
        "Generated by `python -m src.main audit h3`. **There are no Hospital 3 labels.** Nothing below is an "
        "accuracy figure. Service identity is deterministic matching over a lexicon derived from Hospital 3's own "
        "contract vocabulary, plus gated Jev missing-word decisions read from committed review files; the audit "
        "itself calls nothing. Every price and rule is Python.",
        "",
        f"Contract `{c.contract_number}` ({c.effective_from} to {c.effective_to}): three documents, precedence "
        f"{' > '.join(c.precedence)}. Amendment No. 1 effective {c.amendment_effective} **by Service Date**. "
        f"Fingerprint `{c.fingerprint[:16]}`, matcher `{MATCHER_VERSION}`, normalisation `{NORMALIZATION_VERSION}`. "
        "The stage-by-stage account of what each identity step added, and the sensitivity of the result to each "
        "alternative contract reading, is in `semantic_contribution.md`.",
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
        f"| patients | {len({o.patient_id for o in pipeline.occurrences})} |",
        "",
        "## Service identity",
        "",
        "| count | value |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in counts.items()],
        "",
        "## Amendment No. 1",
        "",
        "| | lines |",
        "|---|---|",
        *[f"| rate source: {k} | {v} |" for k, v in amendment["lines_by_rate_source"].items()],
        *[f"| amended service, priced under: {k} | {v} |"
          for k, v in amendment["lines_of_amended_services_by_rate_source"].items()],
        f"| amended service dated before {c.amendment_effective} on an invoice issued on or after it "
        f"(priced under Appendix B) | {amendment['amended_service_lines_dated_before_the_amendment_on_invoices_issued_after']} |",
        f"| lines of the two added services | {amendment['lines_of_added_services']} |",
        f"| of which dated before {c.amendment_effective} (not contracted) | "
        f"{amendment['lines_of_added_services_before_effective_date']} |",
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
        "full trace of every line is in `artifacts/hospital_3/pricing_traces.jsonl`.",
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


def write_outputs(pipeline: H3Pipeline, results: list[H3InvoiceResult], *, template: Path = TEMPLATE,
                  outputs: Path = OUTPUTS, artifacts: Path = ARTIFACTS) -> dict:
    """Every Hospital 3 artifact and output, deterministically."""
    from ..submission import write_hospital_submission

    write_contract_rules(pipeline.contract, artifacts / "contract_rules.json")
    counts = write_identity_artifacts(pipeline, artifacts)
    config = S.JevConfig.from_env()
    requests = S.missing_word_requests(pipeline.contract, pipeline.occurrences, pipeline.matcher, config)
    S.write_request_bodies(requests, artifacts / "jev_missing_word_request_bodies.json")
    S.write_decisions(requests, S.load_reviews(), config.gates, artifacts / "missing_word_decisions.json")
    n_traces = write_traces(results, artifacts / "pricing_traces.jsonl")
    amendment = amendment_summary(pipeline, results)
    _write_json(artifacts / "amendment_summary.json", amendment)
    _write_json(artifacts / "reconstruction_coverage.json", coverage(results))
    _write_json(artifacts / "blank_reasons.json", blank_report(results))
    write_predictions(results, outputs / "predictions.csv")
    write_findings(results, outputs / "findings.csv")
    rows = write_hospital_submission([h.result for h in results], outputs / "submission.csv", template=template)
    (outputs / "audit_report.md").write_text(audit_report(pipeline, results, counts, amendment), encoding="utf-8")
    return {"counts": counts, "traces": n_traces, "rows": rows}


# ==========================================================================
# Contribution and sensitivity report
# ==========================================================================

def _stage_metrics(pipeline: H3Pipeline, results: list[H3InvoiceResult]) -> dict:
    counts = identity_counts(pipeline)
    res = [h.result for h in results]
    return {
        "identity_clusters": counts["identity_clusters"],
        "identified_lines": counts["matched_line_occurrences"],
        "ambiguous_lines": counts["ambiguous_line_occurrences"],
        "unknown_lines": counts["unknown_line_occurrences"],
        "lines_by_method": dict(sorted(Counter(rl.identity.method for rl in pipeline.resolved).items())),
        "pricing_complete": sum(r.pricing_complete for r in res),
        "invoices_reconstructable": sum(r.correction_reconstructable for r in res),
        "expected_total_blanks": sum(r.expected_total_cents is None for r in res),
        "flagged": sum(r.flagged for r in res),
        "invoices_exact_with_an_ambiguous_line": sum(
            1 for h in results if h.result.expected_total_cents is not None
            and any(rl.status == AMBIGUOUS for rl in h.represented.lines)),
    }


def _rows(results: list[H3InvoiceResult]) -> dict[str, tuple]:
    return {h.result.invoice_id: (h.result.flagged, tuple(h.result.error_categories), h.result.expected_total_cents)
            for h in results}


def _decisions_for(lexicon: Lexicon, pipeline: H3Pipeline) -> dict:
    """Gated decisions for the questions ``lexicon`` would produce, answered
    only from stored reviews with a byte-identical request.  Nothing is asked;
    a question never asked stays unresolved."""
    matcher = H3Matcher(pipeline.contract, lexicon)
    return S.load_decisions(pipeline.contract, pipeline.occurrences, matcher, strict=False)


def contribution_stages(final: Optional[H3Pipeline] = None) -> list[tuple[str, str, dict, H3Pipeline, list]]:
    """(name, description, metrics, pipeline, results) for each cumulative stage,
    then the ablations, all on the same data."""
    final = final or H3Pipeline(check_csv=False)
    no_fe = AuditPolicy(financial_equivalence=False)
    no_acronym = build_lexicon(final.contract, _descriptions(final), include_acronyms=False)
    specs = [
        ("exact_contract_words", "exact contract words only (reference suffixes stripped, punctuation collapsed); "
         "structural matching and the unit-basis tie-break; no review; Hospital 4's reconstruction rule",
         Lexicon.exact_only(), {}, no_fe),
        ("h3_orthographic_lexicon", "+ Hospital 3's own letter-rule lexicon (global and contextual tokens), "
         "without the ENT acronym", no_acronym, {}, no_fe),
        ("ent_in_context", "+ `ent -> otolaryngologic`, read only where a contracted service fits",
         final.lexicon, {}, no_fe),
        ("jev_missing_word_resolution", "+ gated Jev missing-word decisions per cluster and billed basis",
         final.lexicon, final.decisions, no_fe),
        ("financial_equivalence", "+ financial equivalence and dependency-aware uncertainty (FINAL)",
         final.lexicon, final.decisions, AuditPolicy()),
        ("ablation_no_jev", "FINAL without the Jev missing-word decisions (the deterministic audit)",
         final.lexicon, {}, AuditPolicy()),
        ("ablation_no_ent", "FINAL without the ENT reading (questions it would change are answered only from "
         "stored reviews with identical requests; none is asked)", no_acronym,
         _decisions_for(no_acronym, final), AuditPolicy()),
    ]
    out = []
    for name, desc, lex, decisions, policy in specs:
        p = H3Pipeline(lexicon=lex, decisions=decisions, policy=policy, check_csv=False)
        results = p.run()
        out.append((name, desc, _stage_metrics(p, results), p, results))
    return out


#: (name, the alternative reading, policy) -- each run once against FINAL.
SENSITIVITY = (
    ("exclusion_after_trigger_only", "Section 9: only a Service Date on or after the trigger's counts",
     {"exclusion_both_directions": False}),
    ("exclusion_day_n_excluded", "Section 9: day N itself is outside the window",
     {"exclusion_window_inclusive": False}),
    ("discount_unit_split", "clause 6.1: each unit takes the tier its own position exceeds",
     {"discount_mode": "unit_split"}),
    ("day_aggregate_counts_repeats", "clause 4.1 / Section 7: repeats of a Service that day add to the aggregate",
     {"repeats_count_toward_day_aggregate": True}),
    ("cap_excess_priced_at_cap", "Section 7: units above the cap are simply not payable",
     {"cap_breach_blocks_reconstruction": False}),
    ("uncontracted_on_date_blank", "A1.3: decline to price a pre-effective added service instead of 0",
     {"uncontracted_on_date_payable_zero": False}),
)


def sensitivity(final: H3Pipeline, final_results: list[H3InvoiceResult]) -> list[dict]:
    base = _rows(final_results)
    out = []
    for name, reading, kwargs in SENSITIVITY:
        p = H3Pipeline(lexicon=final.lexicon, decisions=final.decisions,
                       policy=dataclasses.replace(AuditPolicy(), **kwargs), check_csv=False)
        rows = _rows(p.run())
        changed = sorted(i for i in base if base[i] != rows[i])
        out.append({"run": name, "alternative_reading": reading,
                    "flagged": sum(r[0] for r in rows.values()),
                    "reconstructable": sum(r[2] is not None for r in rows.values()),
                    "rows_changed": len(changed), "changed_rows": changed})
    return out


def contribution_report(stages, sens: list[dict]) -> tuple[str, dict]:
    names = [s[0] for s in stages]
    metrics = {s[0]: s[2] for s in stages}
    cumulative = names[:5]
    final_results = stages[names.index("financial_equivalence")][4]
    final_pipeline = stages[names.index("financial_equivalence")][3]
    blanks = blank_report(final_results)
    config = S.JevConfig.from_env()
    requests = S.missing_word_requests(final_pipeline.contract, final_pipeline.occurrences, final_pipeline.matcher,
                                       config)
    outcomes = Counter(d.outcome for d in final_pipeline.decisions.values())
    doc = {"stages": {s[0]: {"description": s[1], **s[2]} for s in stages},
           "missing_word_review": {"questions": len(requests),
                                   "lines_in_reviewed_clusters": sum(r.state["description"]["occurrence_count"]
                                                                     for r in requests),
                                   "gated_outcomes": dict(sorted(outcomes.items()))},
           "sensitivity": sens,
           "final_blank_reasons": {k: v for k, v in blanks.items() if k != "rows"}}
    keys = ["identity_clusters", "identified_lines", "ambiguous_lines", "unknown_lines", "pricing_complete",
            "invoices_reconstructable", "expected_total_blanks", "invoices_exact_with_an_ambiguous_line", "flagged"]

    def delta(a: str, b: str, k: str) -> str:
        d = metrics[b][k] - metrics[a][k]
        return f"{d:+d}" if d else "0"

    fe = metrics["financial_equivalence"]
    lines = [
        "# Hospital 3 - identity contribution and sensitivity report",
        "",
        "Generated by `python -m src.main audit h3`. Every stage is the same audit on the same data with one more "
        "identity step switched on, so each difference below is caused by that step alone. The first four stages "
        "use Hospital 4's reconstruction rule (a total is proven only if every line's identity is MATCHED and "
        "every input is settled; a malformed date blocks); the fifth switches to financial equivalence. There are "
        "no Hospital 3 labels: these are coverage counts, not accuracy.",
        "",
        "## Stages",
        "",
        "| metric | " + " | ".join(f"`{n}`" for n in cumulative) + " |",
        "|---|" + "---|" * len(cumulative),
        *[f"| {k} | " + " | ".join(str(metrics[n][k]) for n in cumulative) + " |" for k in keys],
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
    mw = doc["missing_word_review"]
    lines += [
        "",
        "## Jev missing-word review",
        "",
        f"{mw['questions']} questions, one per cluster (identity key × billed unit basis), covering "
        f"{mw['lines_in_reviewed_clusters']} lines. Gated outcomes: {mw['gated_outcomes']}. The gates were fixed "
        "before any Hospital 3 review was run (Hospital 5's, after its pre-commit review): a service needs P >= "
        "0.90 and confidence >= 0.80 and may be missing at most one name slot; two or more candidates may be "
        "closed to the contracted set at P(any candidate) + P(ambiguous) >= 0.90; there is no single-candidate "
        "closure. Per-cluster answers and outcomes are in `artifacts/hospital_3/missing_word_decisions.json`.",
        "",
        f"Effect with financial equivalence (`ablation_no_jev` -> FINAL): identified lines "
        f"{fe['identified_lines'] - metrics['ablation_no_jev']['identified_lines']:+d}, reconstructable invoices "
        f"{fe['invoices_reconstructable'] - metrics['ablation_no_jev']['invoices_reconstructable']:+d}.",
        "",
        "## The ENT reading",
        "",
        f"Without `ent -> otolaryngologic` (`ablation_no_ent`): reconstructable invoices "
        f"{metrics['ablation_no_ent']['invoices_reconstructable']}, identified lines "
        f"{metrics['ablation_no_ent']['identified_lines']} (FINAL: {fe['invoices_reconstructable']} and "
        f"{fe['identified_lines']}). It is a human-reviewed contextual interpretation, never a global alias: "
        "`ent` is read only where a contracted Otolaryngologic service fits the other words. The corpus "
        "evidence and the review record are in `artifacts/hospital_3/vocabulary_evidence.json` under "
        "`clinical_acronyms`.",
        "",
        "## Ablations",
        "",
        "| run | identified lines | ambiguous lines | reconstructable | blanks |",
        "|---|---|---|---|---|",
        *[f"| `{n}` | {metrics[n]['identified_lines']} | {metrics[n]['ambiguous_lines']} | "
          f"{metrics[n]['invoices_reconstructable']} | {metrics[n]['expected_total_blanks']} |" for n in names],
        "",
        "## Sensitivity to the contract readings",
        "",
        "Each run is FINAL with exactly one reading switched to its alternative (`audit.AuditPolicy`). "
        "`rows changed` counts submission rows whose flag, categories or expected total differ from FINAL.",
        "",
        f"| run | alternative reading | flagged | reconstructable | rows changed |",
        "|---|---|---|---|---|",
        f"| FINAL | — | {fe['flagged']} | {fe['invoices_reconstructable']} | — |",
        *[f"| `{s['run']}` | {s['alternative_reading']} | {s['flagged']} | {s['reconstructable']} | "
          f"{s['rows_changed']}" + (f" ({', '.join(s['changed_rows'][:6])}{', ...' if len(s['changed_rows']) > 6 else ''})"
                                    if s["changed_rows"] else "") + " |" for s in sens],
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
        "amount, or where no reading has a contract price.",
    ]
    return "\n".join(lines).rstrip("\n") + "\n", doc


def write_contribution_report(final: Optional[H3Pipeline] = None, outputs: Path = OUTPUTS,
                              artifacts: Path = ARTIFACTS) -> str:
    stages = contribution_stages(final)
    names = [s[0] for s in stages]
    fin_p, fin_r = stages[names.index("financial_equivalence")][3:5]
    text, doc = contribution_report(stages, sensitivity(fin_p, fin_r))
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "semantic_contribution.md").write_text(text, encoding="utf-8")
    _write_json(artifacts / "semantic_contribution.json", doc)
    return text
