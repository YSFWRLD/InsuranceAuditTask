"""Hospital 5: Jev as a bounded reviewer.

Jev is used for exactly two kinds of narrow judgment, each a TypeSafe
SystemOne *choice* question over a state that holds only the evidence for that
one judgment:

``normalization_safety``      (prompt 001) is one proposed token -> contract
                              word mapping safe everywhere, safe only in
                              context, or unsafe?
``missing_word_resolution``   (prompt 002) which contracted candidate does an
                              under-specified description refer to -- one of
                              them, several equally, or none?

Jev never proposes, never prices and never audits.  Candidate proposals come
from ``normalization.py``; pricing and every contract rule are Python in
``audit.py``.  No price, rate, quantity, total, invoice id or patient id is
ever placed in a Jev state.

Two ways in, one way through (as for Hospital 2):

    direct API     ``http_transport`` sends each request and stores the answer
    Playground     ``export_requests`` writes the exact request bodies;
                   ``import_results`` reads the answers back

Both go through ``validate_answer`` and are stored as review records keyed by
the SHA-256 of the exact request body.  The gates in this module -- not Jev's
choice -- decide what is accepted, and every threshold is a named,
configurable setting.

Staleness: a review counts only if its request hash and its input fingerprint
(contract, normalisation version, matcher version where relevant, template
version, model) match the request the current code would send.  The audit
refuses to run on missing or stale reviews instead of silently reusing them.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from .contract import H5Contract
from .matcher import AMBIGUOUS, MATCHER_VERSION, MissingWordDecision, StructuralMatch, unit_basis_tiebreak
from .normalization import (
    CONTEXT_REQUIRED, NORMALIZATION_VERSION, SAFE_GLOBAL, UNSAFE, ContractVocabulary, NormalizationCandidate,
    colliding_words,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO_ROOT / "artifacts" / "hospital_5"
PROMPTS = REPO_ROOT / "prompts" / "hospital_5"
NORMALIZATION_TEMPLATE = PROMPTS / "001_jev_normalization_review.md"
MISSING_WORD_TEMPLATE = PROMPTS / "002_jev_missing_word_resolution.md"

CANDIDATES_FILE = ARTIFACTS / "normalization_candidates.json"
NORMALIZATION_REVIEWS = ARTIFACTS / "jev_normalization_reviews.jsonl"
NORMALIZATION_EXPORT = ARTIFACTS / "jev_normalization_requests.json"
NORMALIZATION_RESULTS = ARTIFACTS / "jev_normalization_results.json"
MISSING_WORD_REVIEWS = ARTIFACTS / "jev_missing_word_reviews.jsonl"
MISSING_WORD_EXPORT = ARTIFACTS / "jev_missing_word_requests.json"
MISSING_WORD_RESULTS = ARTIFACTS / "jev_missing_word_results.json"
PROBE_REVIEWS = ARTIFACTS / "jev_probe_reviews.jsonl"
SAFE_VOCAB_FILE = ARTIFACTS / "safe_global_vocab.json"
CONTEXT_VOCAB_FILE = ARTIFACTS / "context_required_vocab.json"
REJECTED_VOCAB_FILE = ARTIFACTS / "rejected_vocab.json"
MISSING_WORD_DECISIONS_FILE = ARTIFACTS / "missing_word_decisions.json"

DEFAULT_JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_JEV_MODEL = "jev-1.13.0"

NORMALIZATION_OPTIONS = (SAFE_GLOBAL, CONTEXT_REQUIRED, UNSAFE)
AMBIGUOUS_OPTION = "ambiguous_contracted_service"
NONE_OPTION = "none_of_the_above_or_unknown"


class SemanticError(Exception):
    pass


class StaleSemanticArtifacts(SemanticError):
    """Reviews are missing or were made for different inputs."""


# ==========================================================================
# Configuration
# ==========================================================================

def _env(name: str) -> Optional[str]:
    """A setting from the environment; an empty value counts as unset."""
    value = os.environ.get(name, "").strip()
    return value or None


def _probability(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise SemanticError(f"{name} must be a number in [0, 1], got {raw!r}") from None
    if not 0 <= value <= 1:
        raise SemanticError(f"{name} must be a number in [0, 1], got {raw!r}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    if not raw.isdigit() or int(raw) <= 0:
        raise SemanticError(f"{name} must be a positive integer, got {raw!r}")
    return int(raw)


@dataclass(frozen=True)
class GateConfig:
    """Acceptance thresholds.  Jev's choice is evidence; these decide.

    Defaults start conservatively for the decisions that change identity
    everywhere (a global expansion, a chosen service).  ``confidence`` is the
    number Jev reports, computed from how spread out the probabilities are; it
    is checked as well as the chosen option's probability, so a flat
    distribution cannot pass on its top option alone.
    """

    global_min_probability: float = 0.90
    global_min_confidence: float = 0.85
    context_min_probability: float = 0.80
    service_min_probability: float = 0.90
    service_min_confidence: float = 0.80
    closure_min_probability: float = 0.90
    unknown_min_probability: float = 0.90
    #: Clusters with more candidates than this are not put to Jev: the text
    #: is too weak for one bounded judgment to be meaningful.
    max_candidates: int = 8

    @classmethod
    def from_env(cls) -> "GateConfig":
        d = cls()
        return cls(
            global_min_probability=_probability("H5_JEV_GLOBAL_MIN_PROBABILITY", d.global_min_probability),
            global_min_confidence=_probability("H5_JEV_GLOBAL_MIN_CONFIDENCE", d.global_min_confidence),
            context_min_probability=_probability("H5_JEV_CONTEXT_MIN_PROBABILITY", d.context_min_probability),
            service_min_probability=_probability("H5_JEV_SERVICE_MIN_PROBABILITY", d.service_min_probability),
            service_min_confidence=_probability("H5_JEV_SERVICE_MIN_CONFIDENCE", d.service_min_confidence),
            closure_min_probability=_probability("H5_JEV_CLOSURE_MIN_PROBABILITY", d.closure_min_probability),
            unknown_min_probability=_probability("H5_JEV_UNKNOWN_MIN_PROBABILITY", d.unknown_min_probability),
            max_candidates=_positive_int("H5_JEV_MAX_CANDIDATES", d.max_candidates),
        )


@dataclass(frozen=True)
class JevConfig:
    """Everything external, from the environment.  No credential is stored."""

    model: str = DEFAULT_JEV_MODEL
    api_url: str = DEFAULT_JEV_API_URL
    api_key: Optional[str] = None
    timeout_seconds: int = 60
    max_attempts: int = 3
    workers: int = 4
    gates: GateConfig = field(default_factory=GateConfig)

    @classmethod
    def from_env(cls) -> "JevConfig":
        return cls(
            model=_env("JEV_MODEL") or DEFAULT_JEV_MODEL,
            api_url=_env("JEV_API_URL") or DEFAULT_JEV_API_URL,
            api_key=_env("TYPESAFE_API_KEY") or _env("JEV_API_KEY"),
            timeout_seconds=_positive_int("H5_JEV_TIMEOUT", 60),
            max_attempts=_positive_int("H5_JEV_MAX_ATTEMPTS", 3),
            workers=_positive_int("H5_JEV_WORKERS", 4),
            gates=GateConfig.from_env(),
        )

    def readiness(self) -> dict[str, str]:
        """What is configured, without ever revealing a credential."""
        return {
            "jev_api": f"ready ({self.api_url})" if self.api_key else "not configured: set TYPESAFE_API_KEY",
            "jev_playground": "always available (*-export / *-import)",
            "jev_model": self.model,
            "gates": json.dumps(self.gates.__dict__, sort_keys=True),
        }


# ==========================================================================
# Templates (prompts/hospital_5/)
# ==========================================================================

@dataclass(frozen=True)
class JevTemplate:
    name: str
    version: str
    question_id: str
    body: dict


def load_template(path: Path) -> JevTemplate:
    """Read the one ```json block of a versioned runtime prompt file."""
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, flags=re.S)
    if len(blocks) != 1:
        raise SemanticError(f"{path.name}: needs exactly one ```json block")
    body = json.loads(blocks[0])
    q = body.get("question") or {}
    ins = q.get("instructions")
    if q.get("type") != "choice" or not isinstance(ins, dict) or set(ins) != {"question", "focus"}:
        raise SemanticError(f"{path.name}: question must be a choice with instructions {{question, focus}}")
    if not isinstance(body.get("state_static"), dict) or not body.get("question_id"):
        raise SemanticError(f"{path.name}: needs question_id and state_static")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return JevTemplate(path.stem, f"{path.stem}@{digest}", body["question_id"], body)


def normalization_template() -> JevTemplate:
    t = load_template(NORMALIZATION_TEMPLATE)
    if set(t.body["question"].get("criteria", {})) != set(NORMALIZATION_OPTIONS):
        raise SemanticError(f"{NORMALIZATION_TEMPLATE.name}: criteria must be exactly {NORMALIZATION_OPTIONS}")
    return t


def missing_word_template() -> JevTemplate:
    t = load_template(MISSING_WORD_TEMPLATE)
    q = t.body["question"]
    if set(q.get("fixed_criteria", {})) != {AMBIGUOUS_OPTION, NONE_OPTION} or "service_option" not in q:
        raise SemanticError(f"{MISSING_WORD_TEMPLATE.name}: needs service_option and the two fixed criteria")
    return t


# ==========================================================================
# Requests
# ==========================================================================

def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


@dataclass(frozen=True)
class JevRequest:
    """One request, one question, one judgment."""

    kind: str
    review_id: str
    template_version: str
    model: str
    question_id: str
    state: dict
    question: dict
    #: What the review depends on besides the request body.
    fingerprint: dict

    @property
    def options(self) -> tuple[str, ...]:
        return tuple(self.question["criteria"])

    @property
    def body(self) -> dict:
        """Exactly what TypeSafe SystemOne receives (and what the Playground export holds)."""
        return {"state": self.state, "model": self.model, "questions": {self.question_id: self.question}}

    @property
    def state_sha256(self) -> str:
        return sha256_of(self.state)

    @property
    def question_sha256(self) -> str:
        return sha256_of({self.question_id: self.question})

    @property
    def request_sha256(self) -> str:
        return sha256_of(self.body)

    @property
    def fingerprint_sha256(self) -> str:
        return sha256_of(self.fingerprint)


def normalization_fingerprint(contract: H5Contract, template: JevTemplate, model: str) -> dict:
    return {"contract": contract.fingerprint, "normalization_version": NORMALIZATION_VERSION,
            "template": template.version, "model": model}


def missing_word_fingerprint(contract: H5Contract, template: JevTemplate, model: str) -> dict:
    """What a missing-word review depends on besides its request body.

    The reviewed lexicon is deliberately not part of it: everything the
    lexicon can change about a question -- the words read, the unread tokens,
    the candidates, the examples, the occurrence count, even whether the
    cluster exists -- is inside the request body, whose SHA-256 is checked
    separately.  A lexicon change that leaves a question byte-identical does
    not make its answer stale."""
    return {"contract": contract.fingerprint, "normalization_version": NORMALIZATION_VERSION,
            "matcher_version": MATCHER_VERSION, "template": template.version, "model": model}


def normalization_request(cand: NormalizationCandidate, collisions: list[str], vocab: ContractVocabulary,
                          template: JevTemplate, model: str, fingerprint: dict) -> JevRequest:
    state = copy.deepcopy(template.body["state_static"])
    state.update({
        "contract_vocabulary": vocab.as_state(),
        "candidate_under_review": {
            "invoice_token": cand.token,
            "proposed_normalization": cand.proposed,
            "proposed_word_slots": list(cand.slots),
            "generated_by": list(cand.heuristics),
        },
        "colliding_contract_words": collisions,
    })
    return JevRequest("normalization", cand.candidate_id, template.version, model, template.question_id,
                      state, copy.deepcopy(template.body["question"]), fingerprint)


def normalization_requests(contract: H5Contract, candidates: list[NormalizationCandidate], template: JevTemplate,
                           model: str) -> list[JevRequest]:
    vocab = ContractVocabulary.from_contract(contract)
    fp = normalization_fingerprint(contract, template, model)
    return [normalization_request(c, colliding_words(c.token, candidates), vocab, template, model, fp)
            for c in candidates]


# -- missing word ------------------------------------------------------------

def option_id(service: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", service.lower()).strip("_")


@dataclass(frozen=True)
class MissingWordCluster:
    """Lines sharing one identity key and one billed unit basis."""

    identity_key: str
    billed_unit_basis: str
    match: StructuralMatch
    normalized_examples: tuple[str, ...]
    occurrence_count: int

    @property
    def review_id(self) -> str:
        return f"{self.identity_key} | {self.billed_unit_basis}"


def needs_missing_word_review(match: StructuralMatch, billed_unit_basis: str, max_candidates: int) -> bool:
    """AMBIGUOUS after text and the unit-basis tie-break, with a bounded, non-empty candidate set."""
    return (match.status == AMBIGUOUS and unit_basis_tiebreak(match, billed_unit_basis) is None
            and 1 <= len(match.candidates) <= max_candidates)


def missing_word_clusters(lines: Iterable[tuple[str, str]], matcher, max_candidates: int) -> list[MissingWordCluster]:
    """``lines`` are (raw description, billed unit basis) pairs -- nothing else
    about a line is looked at."""
    from .normalization import normalize_description

    groups: dict[tuple[str, str], dict] = {}
    for raw, basis in lines:
        m = matcher.match(raw)
        if not needs_missing_word_review(m, basis, max_candidates):
            continue
        g = groups.setdefault((m.identity_key, basis), {"match": m, "normalized": Counter(), "n": 0})
        g["normalized"][normalize_description(raw)] += 1
        g["n"] += 1
    out = []
    for (key, basis), g in sorted(groups.items()):
        examples = tuple(n for n, _ in sorted(g["normalized"].items(), key=lambda kv: (-kv[1], kv[0]))[:5])
        out.append(MissingWordCluster(key, basis, g["match"], examples, g["n"]))
    return out


def _contradicted_services(match: StructuralMatch, contract: H5Contract, limit: int = 12) -> list[dict]:
    """Contracted services sharing a concept word with the description but ruled
    out by a word it carries -- so the reviewer sees the neighbourhood too."""
    words = set(match.words_read)
    cands = set(match.candidate_services)
    out = []
    for name in sorted(contract.services):
        s = contract.services[name]
        if name in cands or not (set(s.concept.split()) & words):
            continue
        slots = []
        q = words & contract.qualifiers
        if q and s.qualifier not in q:
            slots.append(f"qualifier ({', '.join(sorted(q))} vs {s.qualifier})")
        sp = words & contract.specialties
        if sp and s.specialty not in sp:
            slots.append(f"specialty ({', '.join(sorted(sp))} vs {s.specialty})")
        cw = words & contract.concept_words
        extra = sorted(cw - set(s.concept.split()))
        if extra:
            slots.append(f"concept ({', '.join(extra)} not in '{s.concept}')")
        out.append({"service_name": name, "contradicted_by": slots})
    return out[:limit]


def missing_word_request(cluster: MissingWordCluster, contract: H5Contract, template: JevTemplate, model: str,
                         fingerprint: dict) -> JevRequest:
    m = cluster.match
    q = template.body["question"]
    candidates, criteria = [], {}
    for c in m.candidates:
        oid = option_id(c.service)
        candidates.append({
            "option": oid, "service_name": c.service, "qualifier": c.qualifier, "specialty": c.specialty,
            "concept": c.concept, "contracted_unit_basis": c.unit_basis_text,
            "contracted_unit_basis_token": c.unit_basis,
            "matching_fields": list(c.matching_fields), "missing_fields": list(c.missing_fields),
            "contradicted_fields": [],
        })
        fill = {"service_name": c.service, "qualifier": c.qualifier, "specialty": c.specialty, "concept": c.concept}
        criteria[oid] = {k: v.format(**fill) for k, v in q["service_option"].items()}
    criteria.update(copy.deepcopy(q["fixed_criteria"]))
    state = copy.deepcopy(template.body["state_static"])
    state.update({
        "description": {
            "normalized_examples": list(cluster.normalized_examples),
            "words_read": list(m.words_read),
            "unread_tokens": list(m.unread_tokens),
            "occurrence_count": cluster.occurrence_count,
        },
        "billed_unit_basis": cluster.billed_unit_basis,
        "candidates": candidates,
        "contradicted_services": _contradicted_services(m, contract),
    })
    question = {"type": "choice", "instructions": copy.deepcopy(q["instructions"]), "criteria": criteria}
    return JevRequest("missing_word", cluster.review_id, template.version, model, template.question_id,
                      state, question, fingerprint)


def missing_word_requests(clusters: list[MissingWordCluster], contract: H5Contract, template: JevTemplate,
                          model: str) -> list[JevRequest]:
    fp = missing_word_fingerprint(contract, template, model)
    return [missing_word_request(c, contract, template, model, fp) for c in clusters]


# ==========================================================================
# Answers
# ==========================================================================

def validate_answer(item: object, options: tuple[str, ...], model: str) -> dict:
    """One choice answer, checked against the request's own options."""
    if not isinstance(item, dict):
        raise SemanticError("answer is not an object")
    if item.get("type") not in (None, "choice"):
        raise SemanticError(f"answer type {item.get('type')!r} is not 'choice'")
    choice = item.get("choice")
    if choice not in options:
        raise SemanticError(f"choice {choice!r} is not one of the request's options")
    probs = item.get("probabilities")
    if not isinstance(probs, dict) or set(probs) != set(options):
        raise SemanticError("probabilities must cover exactly the request's options")
    clean: dict[str, float] = {}
    for k in options:
        v = probs[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v or not 0 <= v <= 1:
            raise SemanticError(f"probability {k}={v!r} is not a number in [0, 1]")
        clean[k] = float(v)
    if abs(sum(clean.values()) - 1.0) > 0.02:
        raise SemanticError(f"probabilities sum to {sum(clean.values()):.3f}, not 1")
    if clean[choice] < max(clean.values()) - 1e-9:
        raise SemanticError(f"choice {choice} is not the most probable option")
    confidence = item.get("confidence")
    if confidence is not None and (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                                   or confidence != confidence or not 0 <= confidence <= 1):
        raise SemanticError(f"confidence {confidence!r} is not a number in [0, 1]")
    if item.get("model") not in (None, model):
        raise SemanticError(f"answer is for model {item.get('model')!r}, expected {model!r}")
    return {"choice": choice, "probabilities": clean,
            "confidence": None if confidence is None else float(confidence)}


def answer_from_response(body: object, request: JevRequest) -> tuple[dict, dict]:
    """(validated answer, raw answer) from a SystemOne response body."""
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise SemanticError("response has no 'answers' object")
    if body.get("model") not in (None, request.model):
        raise SemanticError(f"response is for model {body.get('model')!r}, expected {request.model!r}")
    raw = body["answers"].get(request.question_id)
    if raw is None or set(body["answers"]) != {request.question_id}:
        raise SemanticError(f"response answers {sorted(body['answers'])}, expected exactly {request.question_id!r}")
    return validate_answer(raw, request.options, request.model), raw


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def review_record(request: JevRequest, answer: dict, *, raw_answer: Optional[dict], usage: Optional[dict],
                  request_id: Optional[str], source: str) -> dict:
    """The full stored result: the answer, and everything needed to prove
    which exact request it answered."""
    return {
        "kind": request.kind,
        "review_id": request.review_id,
        "question_id": request.question_id,
        "template_version": request.template_version,
        "model": request.model,
        "fingerprint": request.fingerprint,
        "fingerprint_sha256": request.fingerprint_sha256,
        "request_sha256": request.request_sha256,
        "state_sha256": request.state_sha256,
        "question_sha256": request.question_sha256,
        "options": list(request.options),
        "answer": answer,
        "raw_answer": raw_answer,
        "usage": usage,
        "request_id": request_id,
        "source": source,
        "answered_utc": _now(),
    }


# ==========================================================================
# Review store
# ==========================================================================

def load_reviews(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SemanticError(f"{path.name}:{n}: bad JSON: {exc}") from exc
            out[rec["review_id"]] = rec
    return out


def save_reviews(path: Path, reviews: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for rid in sorted(reviews):
            fh.write(json.dumps(reviews[rid], sort_keys=True, ensure_ascii=False) + "\n")


def is_current(record: Optional[dict], request: JevRequest) -> bool:
    return (record is not None and record.get("request_sha256") == request.request_sha256
            and record.get("fingerprint_sha256") == request.fingerprint_sha256)


def partition(requests: list[JevRequest], reviews: dict[str, dict]) -> tuple[dict[str, dict], list[JevRequest], list[str]]:
    """(current reviews by id, requests still to ask, ids whose stored review is stale)."""
    current, pending, stale = {}, [], []
    for r in requests:
        rec = reviews.get(r.review_id)
        if is_current(rec, r):
            current[r.review_id] = rec
        else:
            pending.append(r)
            if rec is not None:
                stale.append(r.review_id)
    return current, pending, stale


def require_current(requests: list[JevRequest], reviews: dict[str, dict], what: str, how: str) -> dict[str, dict]:
    current, pending, stale = partition(requests, reviews)
    if pending:
        raise StaleSemanticArtifacts(
            f"{what}: {len(pending)} of {len(requests)} reviews are missing ({len(pending) - len(stale)}) or stale "
            f"({len(stale)}; first: {stale[:3]}). The stored reviews were made for different inputs, or never made. "
            f"Refusing to use them. Run {how}.")
    return current


# ==========================================================================
# Gates
# ==========================================================================

#: The shortest token that may be expanded globally.  A two-letter token
#: carries too little lexical evidence to fix one meaning everywhere: its
#: letters occur, in order, in many words, and the review state lists only
#: *contract* words as collisions, so a reviewer cannot see a common
#: non-contract reading (``bd`` is "bed", or "twice daily", far more often
#: than "bedside").  Such a token may still be read in context, where the
#: surrounding words must support the reading.
MIN_GLOBAL_TOKEN_LENGTH = 3

#: Human review overrides of individual reviewer answers: a committed,
#: human-authored file, one entry per proposal, each with its reason.  Kept
#: deliberately short: an entry records a known reviewer error or a reading
#: the reviewer's context-free question could not settle -- never a
#: preference.  An override may reject a reading or allow it *in context*; it
#: may never make a token global.
HUMAN_REVIEW_FILE = ARTIFACTS / "human_review_overrides.json"
_OVERRIDE_FIELDS = ("candidate_id", "token", "canonical", "decision", "scope", "status", "global", "reviewed",
                    "reason")


def load_review_overrides(path: Path = HUMAN_REVIEW_FILE) -> dict[str, tuple[str, str]]:
    """candidate id -> (decision, reason) from the human review file, validated."""
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, tuple[str, str]] = {}
    for e in doc.get("overrides", []):
        missing = [f for f in _OVERRIDE_FIELDS if f not in e]
        if missing:
            raise SemanticError(f"{path.name}: override {e.get('candidate_id')!r} lacks {missing}")
        if e["candidate_id"] != f"{e['token']}->{e['canonical']}":
            raise SemanticError(f"{path.name}: {e['candidate_id']!r} does not match token and canonical")
        if e["decision"] not in (CONTEXT_REQUIRED, UNSAFE) or e["global"] is not False:
            raise SemanticError(f"{path.name}: {e['candidate_id']!r} -- an override may reject or allow in context, "
                                "never make a token global")
        if e["status"] != "human_reviewed" or not e["reason"].strip():
            raise SemanticError(f"{path.name}: {e['candidate_id']!r} needs status human_reviewed and a reason")
        if e["candidate_id"] in out:
            raise SemanticError(f"{path.name}: {e['candidate_id']!r} listed twice")
        out[e["candidate_id"]] = (e["decision"], f"human review override ({e['reviewed']}, {e['scope']}): "
                                                 f"{e['reason']}")
    return out


def gate_normalization(cand: NormalizationCandidate, answer: dict, gates: GateConfig,
                       overrides: Optional[dict[str, tuple[str, str]]] = None) -> tuple[str, str]:
    """The accepted status of one proposal, and why.

    A recorded human override (``load_review_overrides``) comes first.  Then three
    invariants hold whatever the review says:

    * a proposal that adds a qualifier or specialty word is rejected -- it
      would let normalisation *create* identity evidence;
    * a phrase proposal is never global -- a token cannot everywhere stand
      for words it does not abbreviate;
    * a token shorter than ``MIN_GLOBAL_TOKEN_LENGTH`` is never global.
    """
    overrides = load_review_overrides() if overrides is None else overrides
    if cand.candidate_id in overrides:
        return overrides[cand.candidate_id]
    if cand.is_phrase and any(s != "concept" for s in cand.slots):
        return UNSAFE, "invariant: the phrase adds a qualifier or specialty word the token does not abbreviate"
    p, conf, choice = answer["probabilities"], answer["confidence"], answer["choice"]
    if (choice == SAFE_GLOBAL and p[SAFE_GLOBAL] >= gates.global_min_probability
            and (conf if conf is not None else 0.0) >= gates.global_min_confidence):
        if cand.is_phrase:
            return CONTEXT_REQUIRED, "judged safe, but a phrase expansion is only ever used in context (invariant)"
        if len(cand.token) < MIN_GLOBAL_TOKEN_LENGTH:
            return CONTEXT_REQUIRED, (f"judged safe, but a {len(cand.token)}-letter token is only ever read in "
                                      "context (invariant)")
        return SAFE_GLOBAL, (f"P(safe)={p[SAFE_GLOBAL]:.3f} >= {gates.global_min_probability}, "
                             f"confidence {conf:.3f} >= {gates.global_min_confidence}")
    if choice != UNSAFE and p[SAFE_GLOBAL] + p[CONTEXT_REQUIRED] >= gates.context_min_probability:
        why = "below the global bar" if choice == SAFE_GLOBAL else "context required"
        return CONTEXT_REQUIRED, (f"{why}: P(safe)+P(context)={p[SAFE_GLOBAL] + p[CONTEXT_REQUIRED]:.3f} "
                                  f">= {gates.context_min_probability}")
    return UNSAFE, f"rejected: choice {choice}, P(unsafe)={p[UNSAFE]:.3f}"


#: A review may supply at most this many missing name slots (of qualifier,
#: specialty, concept).  A description that names only a concept, or only a
#: qualifier, does not identify a service however confident the reviewer is:
#: the remaining uniqueness would come from the contract's vocabulary, not
#: from the description.
MAX_SLOTS_SUPPLIED_BY_REVIEW = 1


def gate_missing_word(request: JevRequest, answer: dict, gates: GateConfig) -> MissingWordDecision:
    """The accepted outcome of one missing-word review.

    * a service: Jev chose it, with P and confidence at their bars, and the
      description evidences all but at most one of its name slots;
    * closed to the contracted candidates (two or more): P(any candidate) +
      P(ambiguous) at the closure bar -- the ambiguous option then means what
      it says, "one of these contracted services";
    * unknown: Jev chose none-of-the-above at its bar;
    * otherwise unresolved.

    There is no closure for a single candidate.  With one candidate the
    ambiguous option cannot mean "one of several contracted services"; mass
    on it is the reviewer hedging over the missing word.  Adding that hedge to
    the candidate's probability would read doubt as confirmation.
    """
    cands = {c["option"]: c for c in request.state["candidates"]}
    services = {o: c["service_name"] for o, c in cands.items()}
    p, conf, choice = answer["probabilities"], answer["confidence"], answer["choice"]

    def supplied(option: str) -> int:
        return len(cands[option]["missing_fields"])

    if (choice in services and p[choice] >= gates.service_min_probability
            and (conf if conf is not None else 0.0) >= gates.service_min_confidence):
        if supplied(choice) > MAX_SLOTS_SUPPLIED_BY_REVIEW:
            return MissingWordDecision("unresolved", None,
                                       f"Jev chose {services[choice]!r} (P={p[choice]:.3f}), but the description "
                                       f"lacks {supplied(choice)} of its name slots; a review may supply at most "
                                       f"{MAX_SLOTS_SUPPLIED_BY_REVIEW}")
        return MissingWordDecision("service", services[choice],
                                   f"P={p[choice]:.3f} >= {gates.service_min_probability}, confidence {conf:.3f}")
    if choice == NONE_OPTION and p[NONE_OPTION] >= gates.unknown_min_probability:
        return MissingWordDecision("unknown", None, f"P(none)={p[NONE_OPTION]:.3f}")
    contracted = sum(p[o] for o in services) + p[AMBIGUOUS_OPTION]
    if (len(services) >= 2 and choice != NONE_OPTION and contracted >= gates.closure_min_probability
            and all(supplied(o) <= MAX_SLOTS_SUPPLIED_BY_REVIEW for o in services)):
        return MissingWordDecision("ambiguous_contracted", None,
                                   f"P(some contracted candidate)={contracted:.3f} >= {gates.closure_min_probability}; "
                                   f"no single candidate reached the bar (choice {choice}, P={p[choice]:.3f})")
    return MissingWordDecision("unresolved", None, f"no gate passed (choice {choice}, P={p[choice]:.3f})")


# ==========================================================================
# Transports, running, export and import
# ==========================================================================

#: A transport takes a request body and returns (response JSON, metadata).
Transport = Callable[[dict], tuple[dict, dict]]

_REQUEST_ID_HEADERS = ("x-request-id", "request-id", "x-amzn-requestid", "cf-ray")
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def http_transport(config: JevConfig, *, sleep: Callable[[float], None] = time.sleep) -> Transport:
    """Direct TypeSafe SystemOne adapter: ``POST`` with a bearer key.

    Bounded retries (``H5_JEV_MAX_ATTEMPTS``) on network errors, 429 and 5xx
    only; any other HTTP error is final.  The key is never logged or stored.
    """
    if not config.api_key:
        raise SemanticError("TYPESAFE_API_KEY (or JEV_API_KEY) is not set; use the export / import commands")

    def call(body: dict) -> tuple[dict, dict]:
        last: Optional[Exception] = None
        for attempt in range(1, config.max_attempts + 1):
            req = urllib.request.Request(
                config.api_url, data=json.dumps(body).encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.api_key}"})
            try:
                with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    data = json.loads(resp.read().decode("utf-8"))
                    rid = next((headers[h] for h in _REQUEST_ID_HEADERS if h in headers), None)
                    if isinstance(data, dict):
                        rid = data.get("id") or data.get("request_id") or rid
                    return data, {"request_id": rid, "attempts": attempt, "http_status": resp.status}
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_STATUS:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                    raise SemanticError(f"HTTP {exc.code} from Jev: {detail}") from None
                last = exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
            if attempt < config.max_attempts:
                sleep(2 ** (attempt - 1))
        raise SemanticError(f"Jev request failed after {config.max_attempts} attempts: {last}")

    return call


def run_requests(requests: list[JevRequest], transport: Transport, path: Path, *, workers: int = 1,
                 log: Callable[[str], None] = lambda s: None) -> dict:
    """Ask every request without a current review; store each answer as it arrives.

    The store is rewritten (sorted) after every answer, so an interrupted run
    keeps what it has.  A failed or malformed answer is reported, never stored.
    """
    reviews = load_reviews(path)
    _, pending, stale = partition(requests, reviews)
    lock = threading.Lock()
    failures: list[str] = []
    answered = 0

    def ask(req: JevRequest) -> tuple[JevRequest, dict, dict]:
        return req, *transport(req.body)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(ask, r) for r in pending]
        for fut in as_completed(futures):
            try:
                req, body, meta = fut.result()
                answer, raw = answer_from_response(body, req)
            except SemanticError as exc:
                failures.append(str(exc))
                log(f"failed: {exc}")
                continue
            rec = review_record(req, answer, raw_answer=raw, usage=body.get("usage"),
                                request_id=meta.get("request_id"), source="api")
            with lock:
                reviews[req.review_id] = rec
                save_reviews(path, reviews)
                answered += 1
            log(f"{answered}/{len(pending)} {req.review_id}: {answer['choice']}")
    return {"requests": len(requests), "already_current": len(requests) - len(pending),
            "asked": len(pending), "replaced_stale": len(stale), "answered": answered, "failed": failures}


def export_requests(requests: list[JevRequest], reviews: dict[str, dict], path: Path) -> int:
    """Write every pending request body for the Playground."""
    _, pending, _ = partition(requests, reviews)
    doc = {"kind": requests[0].kind if requests else None,
           "model": requests[0].model if requests else None,
           "template_version": requests[0].template_version if requests else None,
           "instructions": "Run each 'request' body in the TypeSafe Playground; return the answers in the "
                           "results format documented in the prompt file, copying each request_sha256.",
           "requests": [{"review_id": r.review_id, "request_sha256": r.request_sha256,
                         "question_id": r.question_id, "request": r.body} for r in pending]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(pending)


def import_results(raw: object, requests: list[JevRequest], path: Path) -> dict:
    """Playground path: validate every result against the *current* request,
    then store all of them -- or none."""
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), dict) or not raw["results"]:
        raise SemanticError("results file must be an object with a non-empty 'results' object")
    by_id = {r.review_id: r for r in requests}
    errors, plan = [], []
    for rid, item in sorted(raw["results"].items()):
        req = by_id.get(rid)
        if req is None:
            errors.append(f"{rid}: not a current request")
            continue
        if not isinstance(item, dict) or item.get("request_sha256") != req.request_sha256:
            errors.append(f"{rid}: request_sha256 does not match the current request; re-export")
            continue
        if raw.get("model") not in (None, req.model):
            errors.append(f"{rid}: results are for model {raw.get('model')!r}, expected {req.model!r}")
            continue
        try:
            answer = validate_answer({k: v for k, v in item.items() if k != "request_sha256"}, req.options, req.model)
        except SemanticError as exc:
            errors.append(f"{rid}: {exc}")
            continue
        plan.append((req, answer, item))
    if errors:
        raise SemanticError("results refused, nothing imported:\n  " + "\n  ".join(errors))
    reviews = load_reviews(path)
    for req, answer, item in plan:
        reviews[req.review_id] = review_record(req, answer, raw_answer=item, usage=None, request_id=None,
                                               source="playground_import")
    save_reviews(path, reviews)
    return {"imported": len(plan)}


# ==========================================================================
# Decisions from reviews
# ==========================================================================

def normalization_decisions(candidates: list[NormalizationCandidate], requests: list[JevRequest],
                            reviews: dict[str, dict], gates: GateConfig) -> dict[str, dict]:
    """candidate id -> {decision, reason, choice, probabilities, confidence} for current reviews."""
    by_id = {c.candidate_id: c for c in candidates}
    overrides = load_review_overrides()
    unknown = sorted(set(overrides) - set(by_id))
    if unknown:
        raise SemanticError(f"human review overrides name no current proposal: {unknown}")
    out = {}
    for req in requests:
        rec = reviews.get(req.review_id)
        if not is_current(rec, req):
            continue
        decision, reason = gate_normalization(by_id[req.review_id], rec["answer"], gates, overrides)
        out[req.review_id] = {"decision": decision, "reason": reason, **rec["answer"]}
    return out


def missing_word_decisions(requests: list[JevRequest], reviews: dict[str, dict],
                           gates: GateConfig) -> dict[tuple[str, str], MissingWordDecision]:
    out = {}
    for req in requests:
        rec = reviews.get(req.review_id)
        if is_current(rec, req):
            key, basis = req.review_id.rsplit(" | ", 1)
            out[(key, basis)] = gate_missing_word(req, rec["answer"], gates)
    return out
