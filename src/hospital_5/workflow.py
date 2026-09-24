"""Hospital 5: the semantic workflow, stage by stage.

    1. corpus         every raw description on every physical line
    2. candidates     broad normalisation proposals (``normalization.py``)
    3. review         one Jev question per proposal (``semantic.py``)
    4. lexicon        gated reviews -> global and contextual readings
    5. clusters       structural matching; what text and the unit-basis
                      tie-break leave unresolved, grouped by identity key and
                      billed basis
    6. review         one Jev question per unresolved cluster
    7. decisions      gated missing-word outcomes, consumed by line identity

``load_semantics`` rebuilds 2-7 from the current contract, corpus, templates
and model and checks every stored review against the request it would send
now.  With ``strict=True`` (the audit) a missing or stale review is an error.
The contribution report asks for the intermediate lexicons explicitly, so the
deterministic baseline and each added stage can be measured on the same data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..shared.data import load_occurrences
from ..shared.models import InvoiceOccurrence
from . import semantic as S
from .contract import H5Contract, parse_contract
from .matcher import MATCHER_VERSION, H5Matcher, MissingWordDecision
from .normalization import (
    CONTEXT_REQUIRED, NORMALIZATION_VERSION, SAFE_GLOBAL, UNSAFE, ContractVocabulary, Lexicon,
    NormalizationCandidate, build_lexicon, corpus_tokens, propose_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
JSONL = REPO_ROOT / "invoices" / "hospital_5_invoices.jsonl"


def lexicon_sha256(lex: Lexicon) -> str:
    return S.sha256_of({"global": {k: list(v) for k, v in sorted(lex.global_map.items())},
                        "contextual": {k: [list(r) for r in v] for k, v in sorted(lex.contextual.items())}})


def line_keys(occurrences: list[InvoiceOccurrence]) -> list[tuple[str, str]]:
    """(raw description, billed unit basis) per physical line: the only line
    fields the semantic stages ever see."""
    return [(li.description, li.unit_basis_raw) for o in occurrences for li in o.line_items]


@dataclass
class Semantics:
    contract: H5Contract
    config: S.JevConfig
    candidates: list[NormalizationCandidate]
    normalization_requests: list[S.JevRequest]
    normalization_decisions: dict[str, dict]
    lexicon: Lexicon
    global_lexicon: Lexicon
    missing_word_requests: list[S.JevRequest]
    missing_word_decisions: dict[tuple[str, str], MissingWordDecision]
    missing_word_answers: dict[str, dict]

    @property
    def normalization_complete(self) -> bool:
        return len(self.normalization_decisions) == len(self.normalization_requests)

    @property
    def missing_word_complete(self) -> bool:
        return len(self.missing_word_answers) == len(self.missing_word_requests)


def normalization_candidates(contract: H5Contract, occurrences: list[InvoiceOccurrence]) -> list[NormalizationCandidate]:
    descriptions = [raw for raw, _ in line_keys(occurrences)]
    return propose_candidates(corpus_tokens(descriptions), ContractVocabulary.from_contract(contract))


def load_semantics(contract: Optional[H5Contract] = None, occurrences: Optional[list[InvoiceOccurrence]] = None,
                   config: Optional[S.JevConfig] = None, *, strict: bool = True,
                   normalization_reviews: Path = S.NORMALIZATION_REVIEWS,
                   missing_word_reviews: Path = S.MISSING_WORD_REVIEWS) -> Semantics:
    contract = contract or parse_contract()
    occurrences = occurrences if occurrences is not None else load_occurrences(JSONL)
    config = config or S.JevConfig.from_env()
    candidates = normalization_candidates(contract, occurrences)
    n_requests = S.normalization_requests(contract, candidates, S.normalization_template(), config.model)
    n_reviews = S.load_reviews(normalization_reviews)
    if strict:
        S.require_current(n_requests, n_reviews, "normalisation reviews",
                          "`python -m src.main semantic h5 normalize-run` (or normalize-export / normalize-import)")
    decisions = S.normalization_decisions(candidates, n_requests, n_reviews, config.gates)
    gated = {cid: d["decision"] for cid, d in decisions.items()}
    lexicon = build_lexicon(gated, candidates)
    global_lexicon = build_lexicon(gated, candidates, include_contextual=False)

    matcher = H5Matcher(contract, lexicon)
    clusters = S.missing_word_clusters(line_keys(occurrences), matcher, config.gates.max_candidates)
    mw_requests = S.missing_word_requests(clusters, contract, S.missing_word_template(), config.model)
    mw_reviews = S.load_reviews(missing_word_reviews)
    if strict:
        if len(decisions) != len(n_requests):
            raise S.StaleSemanticArtifacts("normalisation reviews incomplete")
        S.require_current(mw_requests, mw_reviews, "missing-word reviews",
                          "`python -m src.main semantic h5 missing-word-run` (or missing-word-export / -import)")
    current = {r.review_id: mw_reviews[r.review_id] for r in mw_requests if S.is_current(mw_reviews.get(r.review_id), r)}
    mw_decisions = S.missing_word_decisions(mw_requests, mw_reviews, config.gates)
    return Semantics(contract, config, candidates, n_requests, decisions, lexicon, global_lexicon,
                     mw_requests, mw_decisions, {rid: rec["answer"] for rid, rec in current.items()})


#: Constructed stress tests: descriptions that do not occur in the corpus,
#: put to Jev through the same template to show it can refuse to invent a
#: missing word.  Their reviews are stored separately and never used by the audit.
PROBES: tuple[tuple[str, str], ...] = (
    # Supervised Palliative Consultation and Supervised Vascular Consultation
    # are both "supervised ... consultation" on a per-procedure basis: only
    # the missing specialty could separate them.
    ("SUPV CONSULT", "per_procedure"),
)


def probe_requests(sem: Semantics) -> list[S.JevRequest]:
    from .normalization import normalize_description

    matcher = H5Matcher(sem.contract, sem.lexicon)
    template = S.missing_word_template()
    fp = {**S.missing_word_fingerprint(sem.contract, template, sem.config.model), "constructed_probe": True}
    out = []
    for desc, basis in PROBES:
        m = matcher.match(desc)
        cluster = S.MissingWordCluster(m.identity_key, basis, m, (normalize_description(desc),), 1)
        out.append(S.missing_word_request(cluster, sem.contract, template, sem.config.model, fp))
    return out


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------

def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def provenance(sem: Semantics) -> dict:
    return {"contract_fingerprint": sem.contract.fingerprint, "normalization_version": NORMALIZATION_VERSION,
            "matcher_version": MATCHER_VERSION, "model": sem.config.model,
            "normalization_template": S.normalization_template().version,
            "missing_word_template": S.missing_word_template().version,
            "gates": sem.config.gates.__dict__}


def write_candidates(sem: Semantics, path: Path = S.CANDIDATES_FILE) -> None:
    _write_json(path, {
        **provenance(sem),
        "note": "Deterministic proposals only; nothing here is accepted until reviewed and gated. Generated "
                "from the contract vocabulary and the description corpus; no price, quantity or total is read.",
        "count": len(sem.candidates),
        "candidates": [{"candidate_id": c.candidate_id, "token": c.token, "proposed": c.proposed,
                        "slots": list(c.slots), "heuristics": list(c.heuristics)} for c in sem.candidates]})


def write_vocab_artifacts(sem: Semantics, directory: Path = S.ARTIFACTS) -> dict[str, int]:
    """safe_global_vocab.json, context_required_vocab.json, rejected_vocab.json."""
    by_decision: dict[str, list[dict]] = {SAFE_GLOBAL: [], CONTEXT_REQUIRED: [], UNSAFE: []}
    by_id = {c.candidate_id: c for c in sem.candidates}
    for cid in sorted(sem.normalization_decisions):
        d, c = sem.normalization_decisions[cid], by_id[cid]
        by_decision[d["decision"]].append({
            "candidate_id": cid, "token": c.token, "proposed": c.proposed, "heuristics": list(c.heuristics),
            "jev_choice": d["choice"], "probabilities": d["probabilities"], "confidence": d["confidence"],
            "gate_reason": d["reason"]})
    prov = provenance(sem)
    _write_json(directory / "safe_global_vocab.json", {
        **prov, "note": "Accepted as safe everywhere by review and gate. The token expands to this reading "
                        "globally only if no other reading of it was accepted (see lexicon.global_map).",
        "count": len(by_decision[SAFE_GLOBAL]), "lexicon_global_map": {k: " ".join(v) for k, v in
                                                                      sorted(sem.lexicon.global_map.items())},
        "entries": by_decision[SAFE_GLOBAL]})
    _write_json(directory / "context_required_vocab.json", {
        **prov, "note": "Accepted only as one of several readings; the surrounding words of each description "
                        "must eliminate the others (structural resolution in the matcher).",
        "count": len(by_decision[CONTEXT_REQUIRED]),
        "lexicon_contextual": {k: [" ".join(r) for r in v] for k, v in sorted(sem.lexicon.contextual.items())},
        "entries": by_decision[CONTEXT_REQUIRED]})
    _write_json(directory / "rejected_vocab.json", {
        **prov, "note": "Never used for matching.", "count": len(by_decision[UNSAFE]),
        "entries": by_decision[UNSAFE]})
    return {k: len(v) for k, v in by_decision.items()}


def write_missing_word_decisions(sem: Semantics, path: Path = S.MISSING_WORD_DECISIONS_FILE) -> dict[str, int]:
    rows = []
    for r in sem.missing_word_requests:
        key, basis = r.review_id.rsplit(" | ", 1)
        d = sem.missing_word_decisions.get((key, basis))
        rows.append({
            "review_id": r.review_id, "identity_key": key, "billed_unit_basis": basis,
            "occurrence_count": r.state["description"]["occurrence_count"],
            "normalized_examples": r.state["description"]["normalized_examples"],
            "candidates": [c["service_name"] for c in r.state["candidates"]],
            "jev_answer": sem.missing_word_answers.get(r.review_id),
            "outcome": d.outcome if d else "not_reviewed", "service": d.service if d else None,
            "gate_reason": d.reason if d else None})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    _write_json(path, {**provenance(sem), "counts": counts, "decisions": rows})
    return counts
