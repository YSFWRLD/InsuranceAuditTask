"""Hospital 2: semantic service identity -- classifier, Jev verifier, mapping store.

This module answers exactly one question: *which contracted service, if any,
does an unresolved billing description refer to?*  It contains no pricing
logic and never sees a billed price, a line total, an invoice id or a patient
id.  Python makes every financial and contractual decision elsewhere.

Flow for one description cluster the deterministic matcher could not resolve:

    classifier (OpenRouter, default z-ai/glm-5.3-flash)
        -> structured JSON, strictly validated; bounded retries; never invented
    Jev verifier -- direct API (``semantic h2 jev``) or Playground batch
    (``jev-export`` / ``jev-import``); both go through one validator and gate
        -> P(ACCEPT), P(REJECT), P(UNCERTAIN)
    gate
        P(ACCEPT) >= 0.90  -> the classifier decision stands
        P(REJECT) >= 0.90  -> rejected; cluster stays unresolved
        otherwise          -> unresolved
        An accepted AMBIGUOUS or UNKNOWN stays AMBIGUOUS or UNKNOWN: accepting
        "I cannot tell" never becomes permission to pick a service.

Every decision is persisted in ``artifacts/hospital_2/service_mappings.json``
with its provenance, keyed to the contract fingerprint and matcher version.
Once there, the audit reads it offline; nothing here runs during an audit.

The prompts are not written in this file.  They are loaded from
``prompts/hospital_2/``, and their content hash is the prompt version, so the
documented prompt is by construction the one that was sent.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .contract import H2Contract
from .matcher import AMBIGUOUS, MATCHED, MATCHER_VERSION, UNKNOWN, DeterministicMatch

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO_ROOT / "artifacts" / "hospital_2"
PROMPTS = REPO_ROOT / "prompts" / "hospital_2"
MAPPINGS_FILE = ARTIFACTS / "service_mappings.json"
CLUSTERS_FILE = ARTIFACTS / "description_clusters.json"
UNRESOLVED_FILE = ARTIFACTS / "unresolved.json"
JEV_STATE_FILE = ARTIFACTS / "jev_state.json"
JEV_QUESTIONS_FILE = ARTIFACTS / "jev_questions.json"
JEV_RESULTS_FILE = ARTIFACTS / "jev_results.json"

CLASSIFIER_PROMPT_FILE = PROMPTS / "001_service_classifier.md"
#: 002_jev_verifier.md is the superseded prose version, kept as history.
JEV_PROMPT_FILE = PROMPTS / "003_jev_verifier.md"

STATUSES = (MATCHED, AMBIGUOUS, UNKNOWN)
JEV_OUTCOMES = ("ACCEPT", "REJECT", "UNCERTAIN")

#: TypeSafe SystemOne, the Jev API.  ``JEV_API_URL`` overrides it.
DEFAULT_JEV_API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_JEV_MODEL = "jev-1.13.0"


def _env(name: str) -> Optional[str]:
    """A setting from the environment, with an empty value treated as unset.

    A ``.env`` file with ``KEY=`` placeholders yields empty strings; those must
    fall back to the default rather than override it (or crash ``int("")``).
    """
    value = os.environ.get(name, "").strip()
    return value or None


class SemanticError(Exception):
    pass


class InvalidClassifierOutput(SemanticError):
    pass


# ==========================================================================
# Configuration
# ==========================================================================

@dataclass(frozen=True)
class SemanticConfig:
    """Everything external, from the environment.  No credential is ever stored."""

    openrouter_api_key: Optional[str]
    openrouter_base_url: str
    classifier_model: str
    max_attempts: int
    timeout_seconds: int
    jev_model: str
    jev_api_url: Optional[str]
    jev_api_key: Optional[str]
    jev_threshold: float

    @classmethod
    def from_env(cls) -> "SemanticConfig":
        return cls(
            openrouter_api_key=_env("OPENROUTER_API_KEY"),
            openrouter_base_url=_env("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            classifier_model=_env("H2_CLASSIFIER_MODEL") or "z-ai/glm-5.3-flash",
            max_attempts=int(_env("H2_CLASSIFIER_MAX_ATTEMPTS") or "3"),
            timeout_seconds=int(_env("H2_SEMANTIC_TIMEOUT") or "60"),
            jev_model=_env("JEV_MODEL") or _env("JEV_VERSION") or DEFAULT_JEV_MODEL,
            # Optional override; the TypeSafe SystemOne endpoint is the default.
            jev_api_url=_env("JEV_API_URL") or DEFAULT_JEV_API_URL,
            # TYPESAFE_API_KEY is preferred; JEV_API_KEY is accepted as a fallback.
            jev_api_key=_env("TYPESAFE_API_KEY") or _env("JEV_API_KEY"),
            jev_threshold=float(_env("JEV_THRESHOLD") or "0.90"),
        )

    def readiness(self) -> dict[str, str]:
        """What is configured, without ever revealing a credential."""
        return {
            "classifier": (f"ready ({self.classifier_model})" if self.openrouter_api_key
                           else f"not configured: set OPENROUTER_API_KEY (model {self.classifier_model})"),
            "jev_api": (f"ready ({self.jev_api_url})" if self.jev_api_key
                        else "not configured: set TYPESAFE_API_KEY"),
            "jev_playground": "always available (jev-export / jev-import)",
            "jev_model": self.jev_model,
        }


# ==========================================================================
# Prompts (loaded from prompts/hospital_2/)
# ==========================================================================

@dataclass(frozen=True)
class PromptTemplate:
    version: str
    system: str
    user: str

    def render(self, **values: str) -> tuple[str, str]:
        user = self.user
        for key, value in values.items():
            user = user.replace("{{" + key + "}}", value)
        leftover = re.findall(r"\{\{[a-z_]+\}\}", user)
        if leftover:
            raise SemanticError(f"prompt {self.version}: unfilled placeholders {leftover}")
        return self.system, user


def load_prompt(path: Path) -> PromptTemplate:
    """Read the ```system and ```user blocks of a versioned prompt file."""
    text = path.read_text(encoding="utf-8")
    blocks = dict(re.findall(r"```(system|user)\n(.*?)\n```", text, flags=re.S))
    if set(blocks) != {"system", "user"}:
        raise SemanticError(f"{path.name}: needs exactly one ```system and one ```user block")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return PromptTemplate(f"{path.stem}@{digest}", blocks["system"], blocks["user"])


# ==========================================================================
# Classifier
# ==========================================================================

CLASSIFIER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["selected_service", "status", "confidence", "evidence_clause_ids", "reason"],
    "properties": {
        "selected_service": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": list(STATUSES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_clause_ids": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
}


def _redact_amounts(text: str) -> str:
    """Contract rates are irrelevant to identity; keep them out of the prompt."""
    return re.sub(r"GBP [\d,]+\.\d{2}", "GBP [amount redacted]", text)


def candidate_block(det: DeterministicMatch, contract: H2Contract) -> str:
    """The candidates as the classifier sees them: name, clause, basis, wording."""
    rows = []
    for c in det.candidates:
        svc = contract.services[c.service]
        first_sentence = re.split(r"(?<=\.)\s+(?=[A-Z])", svc.source_text)[0]
        rows.append({
            "service": svc.name,
            "clause_id": svc.clause_id,
            "contractual_unit_basis": svc.unit_basis_text,
            "contract_text": _redact_amounts(first_sentence),
        })
    return json.dumps(rows, indent=2)


def render_classifier_prompt(
    cluster: dict, det: DeterministicMatch, contract: H2Contract, template: PromptTemplate
) -> tuple[str, str]:
    """Only identity evidence goes in: descriptions and candidates.  No price,
    no total, no invoice or patient id, and -- in this pass -- no billed unit
    basis.  The unit-basis tie-break is applied later, in Python, per line."""
    return template.render(
        raw_descriptions=json.dumps(cluster["raw_descriptions"][:5]),
        normalized_description=det.normalized_description,
        candidates=candidate_block(det, contract),
    )


def validate_classifier_output(obj: object, det: DeterministicMatch) -> dict:
    """Strict: exact keys, correct types, and internally consistent."""
    if not isinstance(obj, dict):
        raise InvalidClassifierOutput("output is not a JSON object")
    expected = set(CLASSIFIER_SCHEMA["required"])
    if set(obj) != expected:
        raise InvalidClassifierOutput(f"keys {sorted(obj)} != {sorted(expected)}")
    status = obj["status"]
    service = obj["selected_service"]
    confidence = obj["confidence"]
    clause_ids = obj["evidence_clause_ids"]
    if status not in STATUSES:
        raise InvalidClassifierOutput(f"status {status!r} not in {STATUSES}")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise InvalidClassifierOutput(f"confidence {confidence!r} not a number in [0, 1]")
    if not isinstance(obj["reason"], str) or not obj["reason"].strip():
        raise InvalidClassifierOutput("reason must be a non-empty string")
    if not isinstance(clause_ids, list) or not all(isinstance(c, str) for c in clause_ids):
        raise InvalidClassifierOutput("evidence_clause_ids must be a list of strings")
    allowed_services = {c.service for c in det.candidates}
    allowed_clauses = {c.clause_id for c in det.candidates}
    if status == MATCHED:
        if service not in allowed_services:
            raise InvalidClassifierOutput(f"MATCHED service {service!r} is not a candidate")
    elif service is not None:
        raise InvalidClassifierOutput(f"{status} must have selected_service null")
    stray = set(clause_ids) - allowed_clauses
    if stray:
        raise InvalidClassifierOutput(f"evidence clauses {sorted(stray)} are not candidate clauses")
    return {
        "selected_service": service,
        "status": status,
        "confidence": float(confidence),
        "evidence_clause_ids": clause_ids,
        "reason": obj["reason"].strip(),
    }


#: A transport takes (system, user) and returns the raw JSON text the model
#: produced.  Injectable so tests can simulate outages and malformed output
#: without a network.
Transport = Callable[[str, str], str]


def openrouter_transport(config: SemanticConfig) -> Transport:
    if not config.openrouter_api_key:
        raise SemanticError("OPENROUTER_API_KEY is not set")

    def call(system: str, user: str) -> str:
        body = {
            "model": config.classifier_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "service_identity", "strict": True, "schema": CLASSIFIER_SCHEMA},
            },
        }
        request = urllib.request.Request(
            f"{config.openrouter_base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SemanticError(f"unexpected provider envelope: {str(payload)[:200]}") from exc

    return call


def classify(
    cluster: dict,
    det: DeterministicMatch,
    contract: H2Contract,
    template: PromptTemplate,
    transport: Transport,
    *,
    model: str,
    max_attempts: int,
) -> dict:
    """One classifier decision, or a recorded failure.  Never a fabricated answer.

    The output is parsed with ``json.loads`` on the whole content -- no
    scraping JSON out of prose.  A malformed or inconsistent answer is retried
    a bounded number of times and then left unresolved.
    """
    system, user = render_classifier_prompt(cluster, det, contract, template)
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            content = transport(system, user)
            result = validate_classifier_output(json.loads(content), det)
            return {
                "model": model, "prompt_version": template.version, "status": "ok",
                "attempts": attempt, "result": result, "errors": errors,
                "completed_utc": _now(),
            }
        except (json.JSONDecodeError, InvalidClassifierOutput) as exc:
            errors.append(f"attempt {attempt}: invalid output: {exc}")
        except (urllib.error.URLError, TimeoutError, OSError, SemanticError) as exc:
            errors.append(f"attempt {attempt}: provider error: {exc}")
    return {
        "model": model, "prompt_version": template.version, "status": "failed",
        "attempts": max_attempts, "result": None, "errors": errors,
        "completed_utc": _now(),
    }


# ==========================================================================
# Jev verifier
# ==========================================================================
#
# Two ways in, one way through.
#
#   Playground   jev-export writes jev_state.json + jev_questions.json; a person
#                runs them in the Jev Playground and writes jev_results.json;
#                jev-import reads it.
#   Direct API   `semantic h2 jev` sends the same state and questions through
#                ``jev_http_transport`` and gets results back.
#
# Both hand their raw results to ``normalize_jev_results`` and then to
# ``apply_jev_results``.  There is exactly one validation and one gate.

JEV_QUESTION_TYPE = "choice"


@dataclass(frozen=True)
class JevQuestionTemplate:
    version: str
    instructions: str
    criteria: dict[str, str]

    def render(self, case_id: str) -> dict:
        return {
            "type": JEV_QUESTION_TYPE,
            "instructions": self.instructions.replace("{{case_id}}", case_id),
            "criteria": {k: v.replace("{{case_id}}", case_id) for k, v in self.criteria.items()},
        }


def load_jev_template(path: Path) -> JevQuestionTemplate:
    """Read the ```json question block of a versioned Jev prompt file."""
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, flags=re.S)
    if len(blocks) != 1:
        raise SemanticError(f"{path.name}: needs exactly one ```json question block")
    q = json.loads(blocks[0])
    if q.get("type") != JEV_QUESTION_TYPE or set(q.get("criteria", {})) != set(JEV_OUTCOMES):
        raise SemanticError(f"{path.name}: question must be type 'choice' with criteria {JEV_OUTCOMES}")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return JevQuestionTemplate(f"{path.stem}@{digest}", q["instructions"], q["criteria"])


def case_id_for(cluster_id_: str) -> str:
    """Deterministic: the case is named after the cluster it verifies."""
    return f"h2_{cluster_id_}"


def question_id_for(case_id: str) -> str:
    return f"verify_{case_id}"


def identity_evidence(record: dict) -> dict:
    """Whether the decision under verification consumed the billed unit basis.

    The billed basis is identity evidence only when a semantic step actually
    used it to break a tie, and then Jev must see it too: a judge cannot
    verify a decision without the evidence that produced it.  Otherwise it is
    withheld.  The current classifier never receives the billed basis, so
    today every case is ``False``; the deterministic per-line tie-break in the
    audit is not a semantic decision and is not sent to Jev.
    """
    if record.get("used_unit_basis_for_identity"):
        basis = record.get("billed_unit_basis")
        if not basis:
            raise SemanticError(
                f"{record['cluster_id']}: used_unit_basis_for_identity is set but no billed basis is recorded")
        return {"used_unit_basis_for_identity": True, "billed_unit_basis": basis}
    return {"used_unit_basis_for_identity": False}


def classifier_digest(classifier: dict, evidence: Optional[dict] = None) -> str:
    """Fingerprint of the exact decision a verdict was given for: the answer,
    plus the identity evidence it rested on."""
    payload = {"result": classifier["result"],
               "identity_evidence": evidence or {"used_unit_basis_for_identity": False}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def record_digest(record: dict) -> str:
    return classifier_digest(record["classifier"], identity_evidence(record))


def _candidates_for_record(record: dict, contract: H2Contract) -> list[dict]:
    rows = []
    for c in record["deterministic"]["candidates"]:
        svc = contract.services[c["service"]]
        rows.append({
            "service": svc.name,
            "clause_id": svc.clause_id,
            "contractual_unit_basis": svc.unit_basis_text,
            "contract_text": _redact_amounts(re.split(r"(?<=\.)\s+(?=[A-Z])", svc.source_text)[0]),
        })
    return rows


def jev_case(record: dict, contract: H2Contract) -> dict:
    """The one canonical verification case -- used for both the Playground
    export and the direct API, so the two can never show Jev different
    evidence.

    Never included: a billed price, a line, invoice or expected total, an
    invoice or patient id, or any rate-derived hint.  Candidate contract text
    has its amounts redacted.  The billed unit basis appears only in
    ``identity_evidence``, and only when it was consumed for identity.
    """
    result = record["classifier"]["result"]
    return {
        "cluster_id": record["cluster_id"],
        "description": record["raw_descriptions"][0],
        "raw_descriptions": record["raw_descriptions"][:5],
        "normalized_description": record["normalized_description"],
        "candidates": _candidates_for_record(record, contract),
        "classifier": {
            "status": result["status"],
            "selected_service": result["selected_service"],
            "confidence": result["confidence"],
            "evidence_clause_ids": result["evidence_clause_ids"],
            "reason": result["reason"],
        },
        "identity_evidence": identity_evidence(record),
    }


def build_jev_batch(
    doc: dict, contract: H2Contract, config: SemanticConfig
) -> tuple[dict, dict, dict[str, dict]]:
    """``(state, questions, bookkeeping)`` for every decision awaiting a verdict.

    ``state`` and ``questions`` are exactly what the Playground (or the API)
    receives.  ``bookkeeping`` is recorded on each mapping so an incoming
    verdict can be checked against the decision it was issued for.
    """
    template = load_jev_template(JEV_PROMPT_FILE)
    cases: dict[str, dict] = {}
    questions: dict[str, dict] = {}
    bookkeeping: dict[str, dict] = {}
    for cid, record in sorted(doc["clusters"].items()):
        clf = record.get("classifier")
        if not clf or clf["status"] != "ok":
            continue
        jev = record.get("jev") or {}
        if jev.get("result") is not None and jev.get("classifier_digest") == record_digest(record):
            continue  # already verified for this exact answer
        case = case_id_for(cid)
        qid = question_id_for(case)
        cases[case] = jev_case(record, contract)
        questions[qid] = template.render(case)
        bookkeeping[cid] = {
            "model": config.jev_model, "prompt_version": template.version,
            "case_id": case, "question_id": qid,
            "classifier_digest": record_digest(record),
            "status": "exported", "source": None, "result": None, "decision": None,
            "exported_utc": _now(),
        }
    return {"cases": cases}, questions, bookkeeping


def export_jev_batch(doc: dict, contract: H2Contract, config: SemanticConfig) -> tuple[dict, dict]:
    state, questions, bookkeeping = build_jev_batch(doc, contract, config)
    for cid, entry in bookkeeping.items():
        doc["clusters"][cid]["jev"] = entry
    doc["updated_utc"] = _now()
    return state, questions


def normalize_jev_results(raw: object, model: str) -> dict[str, dict]:
    """Validate raw verdicts into the one internal shape, or raise listing
    every problem.  Accepts ``{"model": ..., "results": {question_id: item}}``
    or a bare ``{question_id: item}``.  Each item needs ``choice`` and all
    three ``probabilities``; ``confidence`` and ``model`` are optional but
    must agree if present."""
    if not isinstance(raw, dict):
        raise SemanticError("Jev results must be a JSON object")
    if raw.get("model") not in (None, model):
        raise SemanticError(f"results are for model {raw.get('model')!r}, expected {model!r}")
    if "results" in raw:
        items = raw["results"]
    else:
        # Bare per-question results, optionally beside a top-level "model".
        items = {k: v for k, v in raw.items() if k != "model"}
    if not isinstance(items, dict) or not items:
        raise SemanticError("no Jev results found")

    out: dict[str, dict] = {}
    errors: list[str] = []
    for qid, item in items.items():
        try:
            out[qid] = _normalize_one(item, model)
        except SemanticError as exc:
            errors.append(f"{qid}: {exc}")
    if errors:
        raise SemanticError("malformed Jev results, nothing applied:\n  " + "\n  ".join(errors))
    return out


def _normalize_one(item: object, model: str) -> dict:
    if not isinstance(item, dict):
        raise SemanticError("result is not an object")
    choice = item.get("choice")
    if choice not in JEV_OUTCOMES:
        raise SemanticError(f"choice {choice!r} not in {JEV_OUTCOMES}")
    probs = item.get("probabilities")
    if not isinstance(probs, dict) or set(probs) != set(JEV_OUTCOMES):
        raise SemanticError(f"probabilities must have exactly {JEV_OUTCOMES}")
    clean: dict[str, float] = {}
    for k in JEV_OUTCOMES:
        v = probs[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v or not 0 <= v <= 1:
            raise SemanticError(f"probability {k}={v!r} is not a number in [0, 1]")
        clean[k] = float(v)
    if abs(sum(clean.values()) - 1.0) > 0.02:
        raise SemanticError(f"probabilities sum to {sum(clean.values()):.3f}, not 1")
    if clean[choice] < max(clean.values()) - 1e-9:
        raise SemanticError(f"choice {choice} is not the most probable outcome")
    if "confidence" in item:
        c = item["confidence"]
        if not isinstance(c, (int, float)) or isinstance(c, bool) or abs(c - clean[choice]) > 0.02:
            raise SemanticError(f"confidence {c!r} disagrees with P({choice})={clean[choice]}")
    if item.get("model") not in (None, model):
        raise SemanticError(f"model {item.get('model')!r}, expected {model!r}")
    return {"choice": choice, "probabilities": clean, "confidence": clean[choice], "model": model}


def jev_gate(probs: dict[str, float], threshold: float) -> str:
    """ACCEPT / REJECT only on a clear margin; everything else is UNRESOLVED."""
    if probs["ACCEPT"] >= threshold:
        return "ACCEPT"
    if probs["REJECT"] >= threshold:
        return "REJECT"
    return "UNRESOLVED"


def apply_jev_results(
    doc: dict, normalized: dict[str, dict], *, source: str, config: SemanticConfig
) -> dict[str, int]:
    """Attach verdicts to the decisions they were issued for -- all or nothing.

    A verdict is refused if its question was never exported, if its question
    id does not name the cluster it is recorded against, or if the classifier
    answer has changed since export.  Any refusal aborts the whole import.
    """
    by_question = {
        (r.get("jev") or {}).get("question_id"): r
        for r in doc["clusters"].values() if (r.get("jev") or {}).get("question_id")
    }
    errors = []
    plan = []
    for qid, result in sorted(normalized.items()):
        record = by_question.get(qid)
        if record is None:
            errors.append(f"{qid}: no exported question with this id")
            continue
        if qid != question_id_for(case_id_for(record["cluster_id"])):
            errors.append(f"{qid}: does not name cluster {record['cluster_id']}")
            continue
        clf = record.get("classifier")
        if not clf or clf.get("status") != "ok" or record_digest(record) != record["jev"]["classifier_digest"]:
            errors.append(f"{qid}: the classifier answer changed since export; re-export")
            continue
        plan.append((record, result))
    if errors:
        raise SemanticError("Jev results refused, nothing applied:\n  " + "\n  ".join(errors))
    for record, result in plan:
        record["jev"].update(status="answered", source=source, result=result,
                             decision=jev_gate(result["probabilities"], config.jev_threshold),
                             answered_utc=_now())
        record["final"] = finalize(record, config.jev_threshold)
    doc["updated_utc"] = _now()
    return {"applied": len(plan)}


def import_jev_results(doc: dict, raw: object, config: SemanticConfig) -> dict[str, int]:
    """Playground path."""
    return apply_jev_results(doc, normalize_jev_results(raw, config.jev_model),
                             source="playground_import", config=config)


#: A Jev transport takes (state, questions) and returns raw results.
JevTransport = Callable[[dict, dict], object]


def jev_request(config: SemanticConfig, state: dict, questions: dict) -> urllib.request.Request:
    """The TypeSafe SystemOne request: ``POST /v1/systemone`` with a bearer
    key and a body of ``{"state", "model", "questions"}`` -- the same state
    and questions the Playground export writes."""
    return urllib.request.Request(
        config.jev_api_url,
        data=json.dumps({"state": state, "model": config.jev_model, "questions": questions}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {config.jev_api_key}"},
        method="POST",
    )


def jev_http_transport(config: SemanticConfig) -> JevTransport:
    """Direct API adapter -- the only transport-specific Jev code.

    Sends ``jev_request`` and returns the decoded body unchanged.  The body's
    per-question Choice results (``choice``, ``probabilities``,
    ``confidence``) are validated by ``normalize_jev_results`` -- the same
    function a Playground import goes through -- so the API and the
    Playground share one interpretation and one gate.
    """
    if not config.jev_api_key:
        raise SemanticError("TYPESAFE_API_KEY (or JEV_API_KEY) is not set; use jev-export / jev-import")

    def call(state: dict, questions: dict) -> object:
        with urllib.request.urlopen(jev_request(config, state, questions),
                                    timeout=config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    return call


def run_jev(doc: dict, contract: H2Contract, config: SemanticConfig, transport: JevTransport) -> dict[str, int]:
    """Direct API path: export, call, then the same normalise-and-apply."""
    state, questions = export_jev_batch(doc, contract, config)
    if not questions:
        return {"applied": 0, "asked": 0}
    raw = transport(state, questions)
    stats = apply_jev_results(doc, normalize_jev_results(raw, config.jev_model),
                              source="api", config=config)
    return {**stats, "asked": len(questions)}


# ==========================================================================
# Final decision for a cluster
# ==========================================================================

def finalize(record: dict, threshold: float) -> dict:
    """Combine deterministic, classifier and Jev evidence into one decision.

    The candidate list always survives, because an unresolved line still
    matters to cumulative utilisation, daily aggregates, bundles and
    exclusion windows -- the audit carries it as a set of possible services.
    """
    det = record["deterministic"]
    if det["status"] in (MATCHED, UNKNOWN):
        return {"status": det["status"], "service": det["service"], "source": "deterministic",
                "verified": True, "unresolved_reason": None}

    classifier = record.get("classifier")
    if not classifier:
        return _unresolved("semantic classification not yet run")
    if classifier["status"] != "ok":
        return _unresolved("classifier failed: " + "; ".join(classifier["errors"][-2:]))

    jev = record.get("jev") or {}
    result = jev.get("result")
    if result is None or jev.get("classifier_digest") != record_digest(record):
        return _unresolved("classifier decision awaiting Jev verification")
    decision = jev_gate(result["probabilities"], threshold)
    if decision == "REJECT":
        return _unresolved("Jev rejected the classifier decision")
    if decision == "UNRESOLVED":
        return _unresolved(
            f"Jev verdict below the {threshold:.2f} threshold: "
            + ", ".join(f"{k}={v:.2f}" for k, v in result["probabilities"].items())
        )
    proposal = classifier["result"]
    # ACCEPT: the classifier's status stands *as it is*.  An accepted
    # AMBIGUOUS or UNKNOWN is a verified inability to identify, not a match.
    return {"status": proposal["status"], "service": proposal["selected_service"],
            "source": "semantic_verified", "verified": True,
            "unresolved_reason": None if proposal["status"] == MATCHED
            else f"verified {proposal['status']}: {proposal['reason']}"}


def _unresolved(reason: str) -> dict:
    return {"status": AMBIGUOUS, "service": None, "source": "unresolved",
            "verified": False, "unresolved_reason": reason}


# ==========================================================================
# Mapping store
# ==========================================================================

def cluster_id(normalized: str) -> str:
    return "c-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]


def deterministic_record(det: DeterministicMatch) -> dict:
    return {
        "status": det.status,
        "service": det.service,
        "method": det.method,
        "needs_semantic": det.needs_semantic,
        "tied": list(det.tied),
        "unit_basis_tiebreak_eligible": det.status == AMBIGUOUS and len(det.tied) >= 2,
        "candidates": [
            {"service": c.service, "clause_id": c.clause_id, "unit_basis": c.unit_basis,
             "score": c.score, "qualifier_evidenced": c.qualifier_evidenced,
             "specialty_evidenced": c.specialty_evidenced,
             "contradictions": list(c.contradictions),
             "unmatched_service_words": list(c.unmatched_service_words)}
            for c in det.candidates
        ],
        "evidence": det.evidence,
    }


def load_mappings(path: Path = MAPPINGS_FILE) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def mappings_are_current(doc: Optional[dict], contract: H2Contract) -> bool:
    return (
        doc is not None
        and doc.get("contract_fingerprint") == contract.fingerprint
        and doc.get("matcher_version") == MATCHER_VERSION
    )


def prepare_mappings(
    clusters: dict[str, dict],
    contract: H2Contract,
    config: SemanticConfig,
    previous: Optional[dict],
) -> dict:
    """Rebuild the store from the current deterministic pass.

    Semantic results carry over only when the contract fingerprint, matcher
    version, classifier prompt version and the cluster's deterministic
    candidate set are all unchanged.  A Jev verdict carries over only with the
    exact classifier answer it judged and the current Jev prompt version.
    Anything else is stale and is dropped, with the reason recorded.
    """
    classifier_prompt = load_prompt(CLASSIFIER_PROMPT_FILE)
    jev_prompt = load_jev_template(JEV_PROMPT_FILE)
    prior = {}
    invalidated = []
    if previous is not None:
        if mappings_are_current(previous, contract):
            prior = previous.get("clusters", {})
        else:
            invalidated.append("contract fingerprint or matcher version changed; all semantic results discarded")

    records = {}
    for key, cluster in sorted(clusters.items()):
        det: DeterministicMatch = cluster["deterministic"]
        cid = cluster_id(key)
        record = {
            "cluster_id": cid,
            "normalized_description": key,
            "raw_descriptions": cluster["raw_descriptions"],
            "line_count": cluster["line_count"],
            "deterministic": deterministic_record(det),
            # Semantic decisions never use the billed basis.  Per-line use of
            # it (the deterministic tie-break) is counted by the audit.
            "used_unit_basis_for_identity": False,
            "classifier": None,
            "jev": None,
        }
        old = prior.get(cid)
        if old and det.needs_semantic:
            same_candidates = old["deterministic"]["candidates"] == record["deterministic"]["candidates"]
            clf = old.get("classifier")
            if clf and same_candidates and clf.get("prompt_version") == classifier_prompt.version:
                record["classifier"] = clf
                # The identity evidence belongs to the decision and travels with it.
                if old.get("used_unit_basis_for_identity"):
                    record["used_unit_basis_for_identity"] = True
                    record["billed_unit_basis"] = old.get("billed_unit_basis")
                jev = old.get("jev")
                if (jev and clf.get("result") and jev.get("classifier_digest") == record_digest(old)
                        and jev.get("prompt_version") == jev_prompt.version):
                    record["jev"] = jev
                elif jev:
                    invalidated.append(f"{cid}: Jev verdict dropped (answer or Jev prompt changed)")
            elif clf:
                invalidated.append(f"{cid}: classifier result dropped (candidates or prompt changed)")
        record["final"] = finalize(record, config.jev_threshold)
        records[cid] = record

    return {
        "contract_fingerprint": contract.fingerprint,
        "matcher_version": MATCHER_VERSION,
        "classifier_model": config.classifier_model,
        "classifier_prompt_version": classifier_prompt.version,
        "jev_model": config.jev_model,
        "jev_prompt_version": jev_prompt.version,
        "jev_threshold": config.jev_threshold,
        "updated_utc": _now(),
        "invalidated": invalidated,
        "clusters": records,
    }


def refinalize(doc: dict, threshold: float) -> dict:
    for record in doc["clusters"].values():
        record["final"] = finalize(record, threshold)
    doc["updated_utc"] = _now()
    return doc


def run_classifier(
    doc: dict,
    clusters: dict[str, dict],
    contract: H2Contract,
    config: SemanticConfig,
    transport: Transport,
    *,
    limit: Optional[int] = None,
    retry_failed: bool = False,
) -> dict[str, int]:
    """Classify each pending cluster once.  Repeated descriptions share a
    cluster, so each distinct normalised description costs one call."""
    template = load_prompt(CLASSIFIER_PROMPT_FILE)
    by_id = {cluster_id(k): (k, c) for k, c in clusters.items()}
    stats = {"called": 0, "ok": 0, "failed": 0, "skipped": 0}
    for cid, record in sorted(doc["clusters"].items()):
        if not record["deterministic"]["needs_semantic"]:
            continue
        existing = record.get("classifier")
        if existing and (existing["status"] == "ok" or not retry_failed):
            stats["skipped"] += 1
            continue
        if limit is not None and stats["called"] >= limit:
            break
        key, cluster = by_id[cid]
        record["classifier"] = classify(
            cluster, cluster["deterministic"], contract, template, transport,
            model=config.classifier_model, max_attempts=config.max_attempts,
        )
        record["jev"] = None
        stats["called"] += 1
        stats["ok" if record["classifier"]["status"] == "ok" else "failed"] += 1
        record["final"] = finalize(record, config.jev_threshold)
    doc["updated_utc"] = _now()
    return stats


# ==========================================================================
# Counts -- clusters and line occurrences are different units
# ==========================================================================

def semantic_counts(doc: dict) -> dict[str, int]:
    """Cluster-level counts, and the line occurrences those clusters cover.

    A cluster is one normalised description; its ``line_count`` is how many
    invoice lines carry it.  "Semantic pending" means the deterministic stage
    sent the cluster to the semantic stage and no verified decision exists
    yet.  A deterministic UNKNOWN is *not* semantic-pending: the text itself
    contradicted every candidate.
    """
    rs = list(doc["clusters"].values())
    needs = [r for r in rs if r["deterministic"]["needs_semantic"]]
    pending = [r for r in needs if r["final"]["source"] == "unresolved"]
    verified = [r for r in needs if r["final"]["source"] == "semantic_verified"]
    return {
        "total_normalized_clusters": len(rs),
        "deterministic_matched_clusters": sum(r["deterministic"]["status"] == MATCHED for r in rs),
        "deterministic_unknown_clusters": sum(r["deterministic"]["status"] == UNKNOWN for r in rs),
        "semantic_required_clusters": len(needs),
        "semantic_required_line_occurrences": sum(r["line_count"] for r in needs),
        "semantic_pending_clusters": len(pending),
        "semantic_pending_line_occurrences": sum(r["line_count"] for r in pending),
        "semantic_verified_matched_clusters": sum(r["final"]["status"] == MATCHED for r in verified),
        "semantic_verified_ambiguous_clusters": sum(r["final"]["status"] == AMBIGUOUS for r in verified),
        "semantic_verified_unknown_clusters": sum(r["final"]["status"] == UNKNOWN for r in verified),
        "classifier_ok_clusters": sum((r.get("classifier") or {}).get("status") == "ok" for r in needs),
        "classifier_failed_clusters": sum((r.get("classifier") or {}).get("status") == "failed" for r in needs),
        "jev_answered_clusters": sum((r.get("jev") or {}).get("status") == "answered" for r in needs),
        "total_unresolved_or_unknown_clusters": sum(r["final"]["status"] != MATCHED for r in rs),
    }


def unresolved_report(doc: dict) -> dict:
    """Every cluster without a single verified service, in separate groups."""
    def row(cid, r):
        return {
            "cluster_id": cid,
            "normalized_description": r["normalized_description"],
            "raw_descriptions": r["raw_descriptions"],
            "line_occurrences": r["line_count"],
            "deterministic_method": r["deterministic"]["method"],
            "candidates": [c["service"] for c in r["deterministic"]["candidates"]],
            "unit_basis_tiebreak_eligible": r["deterministic"]["unit_basis_tiebreak_eligible"],
            "classifier_status": (r.get("classifier") or {}).get("status", "not_run"),
            "jev_status": (r.get("jev") or {}).get("status", "not_exported"),
            "reason": r["final"]["unresolved_reason"],
        }

    ordered = sorted(doc["clusters"].items(), key=lambda kv: (-kv[1]["line_count"], kv[0]))
    groups = {"semantic_pending": [], "deterministic_unknown": [],
              "semantic_verified_ambiguous": [], "semantic_verified_unknown": []}
    for cid, r in ordered:
        final = r["final"]
        if final["status"] == MATCHED:
            continue
        if final["source"] == "unresolved":
            groups["semantic_pending"].append(row(cid, r))
        elif final["source"] == "deterministic":
            groups["deterministic_unknown"].append(row(cid, r))
        elif final["status"] == AMBIGUOUS:
            groups["semantic_verified_ambiguous"].append(row(cid, r))
        else:
            groups["semantic_verified_unknown"].append(row(cid, r))
    return {
        "generated_utc": _now(),
        "counts": semantic_counts(doc),
        "note": (
            "Clusters without a single verified service. 'semantic_pending' clusters "
            "await the classifier and Jev and still take part in the audit as sets of "
            "possible services. 'deterministic_unknown' clusters were decided from the "
            "text alone (a recognised word contradicts every candidate) and are not "
            "semantic work. line_occurrences counts every invoice line carrying the "
            "description, before any per-line unit-basis tie-break."
        ),
        **groups,
    }


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def clusters_document(clusters: dict[str, dict], contract: H2Contract) -> dict:
    return {
        "contract_fingerprint": contract.fingerprint,
        "matcher_version": MATCHER_VERSION,
        "reference_suffix_rule": "trailing /XX-#### billing references are stripped before clustering and never read as service codes",
        "raw_description_count": sum(len(c["raw_descriptions"]) for c in clusters.values()),
        "total_normalized_clusters": len(clusters),
        "clusters": [
            {"cluster_id": cluster_id(k), "normalized_description": k,
             "line_occurrences": c["line_count"], "raw_descriptions": c["raw_descriptions"],
             "deterministic_status": c["deterministic"].status,
             "deterministic_method": c["deterministic"].method}
            for k, c in sorted(clusters.items(), key=lambda kv: -kv[1]["line_count"])
        ],
    }


def descriptions_of(occurrences: Iterable) -> Iterable[str]:
    for occ in occurrences:
        for line in occ.line_items:
            yield line.description
