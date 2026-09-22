"""Hospital 2 semantic stage: strict output, safe failure, Jev gate, persistence.

No test touches a network.  Transports are fakes; a transport that raises
stands in for an outage.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import urllib.error

import pytest

from src.hospital_2 import semantic as S
from src.hospital_2.matcher import AMBIGUOUS, MATCHED, UNKNOWN, cluster_descriptions
from tests.hospital_2.conftest import PLAIN, invoice, line, mappings_for, run

TIED = "ENT REHAB PROG"
TIED_DAY = "Intensive Otolaryngologic Rehabilitation Programme"
MISSING = "ADV ENDOSCOPIC PROC"
MISSING_SVC = "Advanced Gastrointestinal Endoscopic Procedure"


def config(**kw) -> S.SemanticConfig:
    base = dict(openrouter_api_key=None, openrouter_base_url="http://invalid.local",
                classifier_model="test-model", max_attempts=3, timeout_seconds=1,
                jev_model="jev-1.13.0", jev_api_url=S.DEFAULT_JEV_API_URL, jev_api_key=None,
                jev_threshold=0.90)
    base.update(kw)
    return S.SemanticConfig(**base)


def answer(status, service=None, clauses=(), confidence=0.9, reason="the words decide it"):
    return json.dumps({"selected_service": service, "status": status, "confidence": confidence,
                       "evidence_clause_ids": list(clauses), "reason": reason})


@pytest.fixture()
def clusters(matcher):
    descs = [TIED] * 5 + [TIED + " /SA-77"] * 3 + [MISSING] * 4 + [PLAIN] * 2
    return cluster_descriptions(descs, matcher)


@pytest.fixture()
def doc(clusters, contract):
    return S.prepare_mappings(clusters, contract, config(), None)


def det_of(clusters, description, matcher):
    return clusters[matcher.match(description).normalized_description]["deterministic"]


# -- strict output validation ------------------------------------------------------

def test_valid_output_passes(clusters, matcher):
    det = det_of(clusters, MISSING, matcher)
    out = S.validate_classifier_output(json.loads(answer(MATCHED, MISSING_SVC, ["14.4"])), det)
    assert out["status"] == MATCHED and out["selected_service"] == MISSING_SVC


@pytest.mark.parametrize("payload,match", [
    ({"selected_service": None, "status": "AMBIGUOUS", "confidence": 0.5,
      "evidence_clause_ids": [], "reason": "x", "extra": 1}, "keys"),
    ({"selected_service": "Invented Service", "status": "MATCHED", "confidence": 0.9,
      "evidence_clause_ids": [], "reason": "x"}, "not a candidate"),
    ({"selected_service": MISSING_SVC, "status": "AMBIGUOUS", "confidence": 0.9,
      "evidence_clause_ids": [], "reason": "x"}, "must have selected_service null"),
    ({"selected_service": None, "status": "MAYBE", "confidence": 0.9,
      "evidence_clause_ids": [], "reason": "x"}, "status"),
    ({"selected_service": None, "status": "UNKNOWN", "confidence": 1.5,
      "evidence_clause_ids": [], "reason": "x"}, "confidence"),
    ({"selected_service": None, "status": "UNKNOWN", "confidence": True,
      "evidence_clause_ids": [], "reason": "x"}, "confidence"),
    ({"selected_service": None, "status": "UNKNOWN", "confidence": 0.5,
      "evidence_clause_ids": ["99.9"], "reason": "x"}, "not candidate clauses"),
    ({"selected_service": None, "status": "UNKNOWN", "confidence": 0.5,
      "evidence_clause_ids": [], "reason": "  "}, "reason"),
])
def test_invalid_output_is_rejected(clusters, matcher, payload, match):
    with pytest.raises(S.InvalidClassifierOutput, match=match):
        S.validate_classifier_output(payload, det_of(clusters, MISSING, matcher))


def test_malformed_provider_response_fails_safely(clusters, contract, matcher):
    """Prose, truncated JSON, JSON inside prose: never scraped, never guessed."""
    responses = iter(['Sure! Here is the answer: {"status": "MATCHED"}', '{"selected_service": null, "stat',
                      "The service is " + MISSING_SVC])
    key = matcher.match(MISSING).normalized_description
    out = S.classify(clusters[key], clusters[key]["deterministic"], contract,
                     S.load_prompt(S.CLASSIFIER_PROMPT_FILE), lambda s, u: next(responses),
                     model="m", max_attempts=3)
    assert out["status"] == "failed" and out["result"] is None
    assert len(out["errors"]) == 3 and all("invalid output" in e for e in out["errors"])


def test_retries_are_bounded_then_recover(clusters, contract, matcher):
    calls = []
    def flaky(system, user):
        calls.append(1)
        return "not json" if len(calls) < 2 else answer(MATCHED, MISSING_SVC, ["14.4"])
    key = matcher.match(MISSING).normalized_description
    out = S.classify(clusters[key], clusters[key]["deterministic"], contract,
                     S.load_prompt(S.CLASSIFIER_PROMPT_FILE), flaky, model="m", max_attempts=3)
    assert out["status"] == "ok" and out["attempts"] == 2 and len(calls) == 2


def test_api_outage_creates_no_mapping(doc, clusters, contract):
    def down(system, user):
        raise urllib.error.URLError("connection refused")
    stats = S.run_classifier(doc, clusters, contract, config(), down)
    assert stats["ok"] == 0 and stats["failed"] == 2
    for r in doc["clusters"].values():
        if r["deterministic"]["needs_semantic"]:
            assert r["classifier"]["status"] == "failed"
            assert r["final"]["status"] == AMBIGUOUS and r["final"]["service"] is None
            assert r["final"]["source"] == "unresolved"
            assert "classifier failed" in r["final"]["unresolved_reason"]


def test_no_api_key_means_no_transport():
    with pytest.raises(S.SemanticError, match="OPENROUTER_API_KEY"):
        S.openrouter_transport(config(openrouter_api_key=None))


def test_repeated_descriptions_are_classified_once(doc, clusters, contract):
    calls = []
    def count(system, user):
        calls.append(user)
        return answer(AMBIGUOUS, None, [])
    S.run_classifier(doc, clusters, contract, config(), count)
    # 14 lines, 3 clusters, 2 of which need semantics: two calls, not fourteen.
    assert len(calls) == 2
    S.run_classifier(doc, clusters, contract, config(), count)
    assert len(calls) == 2, "an answered cluster is not asked again"


def test_the_prompt_carries_identity_evidence_only(doc, clusters, contract, matcher):
    seen = []
    S.run_classifier(doc, clusters, contract, config(), lambda s, u: seen.append(s + u) or answer(AMBIGUOUS))
    for text in seen:
        assert not re.search(r"GBP \d", text), "contract amounts are redacted"
        assert "INV-" not in text and "PT-" not in text
        assert "per_visit" not in text and "per_day" not in text, "no billed unit-basis token"


def test_prompt_version_comes_from_the_prompt_file():
    t = S.load_prompt(S.CLASSIFIER_PROMPT_FILE)
    assert re.fullmatch(r"001_service_classifier@[0-9a-f]{12}", t.version)
    assert "{{normalized_description}}" in t.user and "Reply with the JSON object only." in t.system


# -- Jev: shared helpers -----------------------------------------------------------------

def _classify(doc, clusters, contract, *, missing_status=MATCHED):
    """Classifier fake: the missing-specialty cluster gets ``missing_status``,
    the tied cluster gets AMBIGUOUS."""
    def fake(system, user):
        if "adv endoscopic proc" in user:
            if missing_status == MATCHED:
                return answer(MATCHED, MISSING_SVC, ["14.4"])
            return answer(missing_status)
        return answer(AMBIGUOUS)
    S.run_classifier(doc, clusters, contract, config(), fake)


def _missing_record(doc, matcher):
    return doc["clusters"][S.cluster_id(matcher.match(MISSING).normalized_description)]


def _verdict(choice, a, r, u, **extra):
    return {"choice": choice, "probabilities": {"ACCEPT": a, "REJECT": r, "UNCERTAIN": u}, **extra}


def _import(doc, results, model="jev-1.13.0"):
    return S.import_jev_results(doc, {"model": model, "results": results}, config())


# -- Jev: Playground export --------------------------------------------------------------

def test_playground_state_export_is_valid(doc, clusters, contract):
    _classify(doc, clusters, contract)
    state, questions = S.export_jev_batch(doc, contract, config())
    assert set(state) == {"cases"} and len(state["cases"]) == 2
    for case_id, case in state["cases"].items():
        assert case_id == "h2_" + case["cluster_id"]
        assert {"description", "normalized_description", "candidates", "classifier"} <= set(case)
        assert set(case["classifier"]) >= {"status", "selected_service", "confidence", "reason"}
        for cand in case["candidates"]:
            assert set(cand) == {"service", "clause_id", "contractual_unit_basis", "contract_text"}
        text = json.dumps(case)
        assert not re.search(r"GBP \d", text), "contract amounts are redacted"
        assert "INV-" not in text and "PT-" not in text and "per_visit" not in text


def test_playground_questions_export_uses_the_choice_format(doc, clusters, contract):
    _classify(doc, clusters, contract)
    state, questions = S.export_jev_batch(doc, contract, config())
    assert len(questions) == len(state["cases"]) == 2
    for qid, q in questions.items():
        assert set(q) == {"type", "instructions", "criteria"}, "no invented question/options shape"
        assert q["type"] == "choice"
        assert set(q["criteria"]) == {"ACCEPT", "REJECT", "UNCERTAIN"}
        case_id = qid.removeprefix("verify_")
        assert case_id in state["cases"] and case_id in q["instructions"]
        assert "{{" not in json.dumps(q)


def test_question_ids_map_deterministically_to_clusters(doc, clusters, contract):
    _classify(doc, clusters, contract)
    _, first = S.export_jev_batch(doc, contract, config())
    for cid, record in doc["clusters"].items():
        if record.get("jev"):
            assert record["jev"]["question_id"] == f"verify_h2_{cid}"
            assert record["jev"]["question_id"] in first
    _, again = S.export_jev_batch(doc, contract, config())
    assert set(again) == set(first)


def test_template_is_loaded_from_the_versioned_prompt_file():
    t = S.load_jev_template(S.JEV_PROMPT_FILE)
    assert re.fullmatch(r"003_jev_verifier@[0-9a-f]{12}", t.version)
    assert S.JEV_PROMPT_FILE.name == "003_jev_verifier.md"


def test_nothing_to_export_without_classifier_decisions(doc, contract):
    state, questions = S.export_jev_batch(doc, contract, config())
    assert state == {"cases": {}} and questions == {}


# -- Jev: the gate -------------------------------------------------------------------

@pytest.mark.parametrize("verdict,status,service,verified", [
    (("ACCEPT", 0.95, 0.03, 0.02), MATCHED, MISSING_SVC, True),      # ACCEPT >= .90
    (("ACCEPT", 0.90, 0.05, 0.05), MATCHED, MISSING_SVC, True),      # exactly at the gate
    (("REJECT", 0.02, 0.95, 0.03), AMBIGUOUS, None, False),          # REJECT >= .90
    (("ACCEPT", 0.85, 0.10, 0.05), AMBIGUOUS, None, False),          # weak ACCEPT
    (("REJECT", 0.10, 0.85, 0.05), AMBIGUOUS, None, False),          # weak REJECT
    (("UNCERTAIN", 0.30, 0.20, 0.50), AMBIGUOUS, None, False),
])
def test_jev_gate(doc, clusters, contract, matcher, verdict, status, service, verified):
    _classify(doc, clusters, contract)
    S.export_jev_batch(doc, contract, config())
    rec = _missing_record(doc, matcher)
    _import(doc, {rec["jev"]["question_id"]: _verdict(*verdict)})
    f = rec["final"]
    assert (f["status"], f["service"], f["verified"]) == (status, service, verified)


@pytest.mark.parametrize("classifier_status", [AMBIGUOUS, UNKNOWN])
def test_jev_acceptance_preserves_ambiguous_and_unknown(doc, clusters, contract, matcher, classifier_status):
    _classify(doc, clusters, contract, missing_status=classifier_status)
    S.export_jev_batch(doc, contract, config())
    rec = _missing_record(doc, matcher)
    _import(doc, {rec["jev"]["question_id"]: _verdict("ACCEPT", 0.97, 0.01, 0.02)})
    assert (rec["final"]["status"], rec["final"]["service"], rec["final"]["verified"]) == (classifier_status, None, True)


def test_no_verdict_means_unresolved(doc, clusters, contract, matcher):
    _classify(doc, clusters, contract)
    S.export_jev_batch(doc, contract, config())
    f = _missing_record(doc, matcher)["final"]
    assert f["status"] == AMBIGUOUS and "awaiting Jev" in f["unresolved_reason"]


# -- Jev: malformed results are refused, visibly and atomically -------------------------

@pytest.mark.parametrize("bad,match", [
    (_verdict("MAYBE", 0.95, 0.03, 0.02), "choice"),
    ({"choice": "ACCEPT", "probabilities": {"ACCEPT": 0.95, "REJECT": 0.05}}, "exactly"),
    ({"choice": "ACCEPT"}, "exactly"),
    (_verdict("ACCEPT", 1.2, -0.1, -0.1), r"\[0, 1\]"),
    (_verdict("ACCEPT", float("nan"), 0.5, 0.5), r"\[0, 1\]"),
    (_verdict("ACCEPT", "0.95", 0.03, 0.02), r"\[0, 1\]"),
    (_verdict("ACCEPT", True, 0, 0), r"\[0, 1\]"),
    (_verdict("ACCEPT", 0.5, 0.1, 0.1), "sum"),
    (_verdict("REJECT", 0.95, 0.03, 0.02), "most probable"),
    (_verdict("ACCEPT", 0.95, 0.03, 0.02, confidence=0.5), "confidence"),
    (_verdict("ACCEPT", 0.95, 0.03, 0.02, model="jev-0.1"), "model"),
])
def test_malformed_results_fail_safely(doc, clusters, contract, matcher, bad, match):
    _classify(doc, clusters, contract)
    S.export_jev_batch(doc, contract, config())
    rec = _missing_record(doc, matcher)
    before = json.dumps(doc, sort_keys=True)
    with pytest.raises(S.SemanticError, match=match):
        _import(doc, {rec["jev"]["question_id"]: bad})
    assert json.dumps(doc, sort_keys=True) == before, "nothing may be applied"


def test_one_bad_result_blocks_the_whole_import(doc, clusters, contract, matcher):
    _classify(doc, clusters, contract)
    _, questions = S.export_jev_batch(doc, contract, config())
    good, other = sorted(questions)
    before = json.dumps(doc, sort_keys=True)
    with pytest.raises(S.SemanticError):
        _import(doc, {good: _verdict("ACCEPT", 0.95, 0.03, 0.02),
                      other: _verdict("ACCEPT", 2, 0, 0)})
    assert json.dumps(doc, sort_keys=True) == before


def test_unknown_question_ids_and_wrong_model_are_refused(doc, clusters, contract):
    _classify(doc, clusters, contract)
    S.export_jev_batch(doc, contract, config())
    with pytest.raises(S.SemanticError, match="no exported question"):
        _import(doc, {"verify_h2_c-0000000000": _verdict("ACCEPT", 0.95, 0.03, 0.02)})
    with pytest.raises(S.SemanticError, match="expected 'jev-1.13.0'"):
        _import(doc, {}, model="jev-0.1")


def test_a_verdict_cannot_attach_to_a_changed_classifier_answer(doc, clusters, contract, matcher):
    _classify(doc, clusters, contract)
    S.export_jev_batch(doc, contract, config())
    rec = _missing_record(doc, matcher)
    rec["classifier"]["result"]["status"] = AMBIGUOUS          # answer changed after export
    rec["classifier"]["result"]["selected_service"] = None
    with pytest.raises(S.SemanticError, match="changed since export"):
        _import(doc, {rec["jev"]["question_id"]: _verdict("ACCEPT", 0.95, 0.03, 0.02)})


# -- Jev: direct API ------------------------------------------------------------------

def test_api_and_playground_use_the_same_gate(doc, clusters, contract, matcher):
    """Same verdicts through both paths give identical final decisions."""
    _classify(doc, clusters, contract)
    api_doc = copy.deepcopy(doc)
    verdicts = {}

    def fake_api(state, questions):
        assert set(questions) == {f"verify_{c}" for c in state["cases"]}
        for qid in questions:
            verdicts[qid] = _verdict("ACCEPT", 0.96, 0.02, 0.02)
        return {"model": "jev-1.13.0", "results": verdicts}

    stats = S.run_jev(api_doc, contract, config(), fake_api)
    assert stats == {"applied": 2, "asked": 2}

    S.export_jev_batch(doc, contract, config())
    _import(doc, verdicts)
    finals = lambda d: {cid: r["final"] for cid, r in d["clusters"].items()}
    assert finals(api_doc) == finals(doc)
    sources = {r["jev"]["source"] for r in api_doc["clusters"].values() if r.get("jev")}
    assert sources == {"api"}


def test_api_results_are_validated_like_imports(doc, clusters, contract):
    _classify(doc, clusters, contract)
    with pytest.raises(S.SemanticError, match="sum"):
        S.run_jev(doc, contract, config(), lambda s, q: {qid: _verdict("ACCEPT", 0.5, 0.1, 0.1) for qid in q})


def test_jev_api_needs_only_a_key():
    with pytest.raises(S.SemanticError, match="TYPESAFE_API_KEY"):
        S.jev_http_transport(config(jev_api_key=None))
    S.jev_http_transport(config(jev_api_key="secret-key"))   # no URL needed


# -- TypeSafe / Jev configuration ---------------------------------------------------------

JEV_ENV = ("TYPESAFE_API_KEY", "JEV_API_KEY", "JEV_API_URL", "JEV_MODEL", "JEV_VERSION")


@pytest.fixture()
def clean_env(monkeypatch):
    for name in JEV_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_typesafe_api_key_is_accepted(clean_env):
    clean_env.setenv("TYPESAFE_API_KEY", "ts-key")
    assert S.SemanticConfig.from_env().jev_api_key == "ts-key"


def test_jev_api_key_remains_a_fallback(clean_env):
    clean_env.setenv("JEV_API_KEY", "jev-key")
    assert S.SemanticConfig.from_env().jev_api_key == "jev-key"


def test_typesafe_api_key_wins_over_jev_api_key(clean_env):
    clean_env.setenv("TYPESAFE_API_KEY", "ts-key")
    clean_env.setenv("JEV_API_KEY", "jev-key")
    assert S.SemanticConfig.from_env().jev_api_key == "ts-key"


def test_default_endpoint_and_model(clean_env):
    c = S.SemanticConfig.from_env()
    assert c.jev_api_url == "https://api.typesafe.ai/v1/systemone"
    assert c.jev_model == "jev-1.13.0"
    assert c.jev_api_key is None
    assert c.readiness()["jev_api"] == "not configured: set TYPESAFE_API_KEY"


def test_jev_api_url_can_override_the_default(clean_env):
    clean_env.setenv("JEV_API_URL", "https://example.invalid/v1/systemone")
    assert S.SemanticConfig.from_env().jev_api_url == "https://example.invalid/v1/systemone"


def test_the_request_is_a_bearer_post_of_state_model_and_questions():
    c = config(jev_api_key="ts-key")
    state, questions = {"cases": {"h2_c-1": {"x": 1}}}, {"verify_h2_c-1": {"type": "choice"}}
    req = S.jev_request(c, state, questions)
    assert req.full_url == "https://api.typesafe.ai/v1/systemone"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer ts-key"
    assert req.get_header("Content-type") == "application/json"
    body = json.loads(req.data.decode("utf-8"))
    assert body == {"state": state, "model": "jev-1.13.0", "questions": questions}


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_api_response_is_normalised_like_a_playground_import(doc, clusters, contract, monkeypatch):
    """The real transport, with only the network replaced: the TypeSafe reply
    goes through the same normaliser and gate as a Playground import."""
    _classify(doc, clusters, contract)
    playground_doc = copy.deepcopy(doc)
    sent = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        sent.append((request, body))
        return _FakeResponse({qid: {"choice": "ACCEPT",
                                    "probabilities": {"ACCEPT": 0.94, "REJECT": 0.04, "UNCERTAIN": 0.02},
                                    "confidence": 0.94}
                              for qid in body["questions"]})

    monkeypatch.setattr(S.urllib.request, "urlopen", fake_urlopen)
    c = config(jev_api_key="ts-key")
    assert S.run_jev(doc, contract, c, S.jev_http_transport(c)) == {"applied": 2, "asked": 2}
    request, body = sent[0]
    assert request.get_header("Authorization") == "Bearer ts-key"
    assert set(body) == {"state", "model", "questions"}

    S.export_jev_batch(playground_doc, contract, config())
    S.import_jev_results(playground_doc, {"model": "jev-1.13.0", "results": {
        qid: {"choice": "ACCEPT", "probabilities": {"ACCEPT": 0.94, "REJECT": 0.04, "UNCERTAIN": 0.02}}
        for qid in body["questions"]}}, config())
    for cid, record in doc["clusters"].items():
        other = playground_doc["clusters"][cid]
        if record.get("jev"):
            assert record["jev"]["result"] == other["jev"]["result"]
            assert set(record["jev"]["result"]) == {"choice", "probabilities", "confidence", "model"}
            assert (record["jev"]["source"], other["jev"]["source"]) == ("api", "playground_import")
        assert record["final"] == other["final"]


# -- unit-basis evidence ------------------------------------------------------------------

def _consume_basis(doc, matcher, basis="per_procedure"):
    """Simulate a semantic decision that used the billed basis as a tie-break."""
    rec = _missing_record(doc, matcher)
    rec["used_unit_basis_for_identity"] = True
    rec["billed_unit_basis"] = basis
    return rec


def _case(state, rec):
    return state["cases"][f"h2_{rec['cluster_id']}"]


def test_normal_case_does_not_expose_the_billed_unit_basis(doc, clusters, contract, matcher):
    _classify(doc, clusters, contract)
    state, _ = S.export_jev_batch(doc, contract, config())
    for case in state["cases"].values():
        assert case["identity_evidence"] == {"used_unit_basis_for_identity": False}
    text = json.dumps(state)
    assert "billed_unit_basis" not in text
    for token in ("per_procedure", "per_visit", "per_day", "per_hour", "per_item", "per_night"):
        assert token not in text


def test_tie_break_case_exposes_the_consumed_basis(doc, clusters, contract, matcher):
    _classify(doc, clusters, contract)
    rec = _consume_basis(doc, matcher)
    state, _ = S.export_jev_batch(doc, contract, config())
    assert _case(state, rec)["identity_evidence"] == {
        "used_unit_basis_for_identity": True, "billed_unit_basis": "per_procedure"}
    others = [c for k, c in state["cases"].items() if k != f"h2_{rec['cluster_id']}"]
    assert all("billed_unit_basis" not in c["identity_evidence"] for c in others)


def test_consumed_basis_without_a_recorded_basis_is_refused(doc, clusters, contract, matcher):
    _classify(doc, clusters, contract)
    _consume_basis(doc, matcher, basis=None)
    with pytest.raises(S.SemanticError, match="no billed basis"):
        S.export_jev_batch(doc, contract, config())


def test_a_verdict_is_bound_to_the_identity_evidence_too(doc, clusters, contract, matcher):
    _classify(doc, clusters, contract)
    S.export_jev_batch(doc, contract, config())
    rec = _consume_basis(doc, matcher)      # evidence changed after export
    with pytest.raises(S.SemanticError, match="changed since export"):
        _import(doc, {rec["jev"]["question_id"]: _verdict("ACCEPT", 0.95, 0.03, 0.02)})


@pytest.mark.parametrize("consumed", [False, True])
def test_no_price_field_ever_reaches_jev(doc, clusters, contract, matcher, consumed):
    _classify(doc, clusters, contract)
    if consumed:
        _consume_basis(doc, matcher)
    state, questions = S.export_jev_batch(doc, contract, config())
    text = json.dumps({"state": state, "questions": questions})
    for field in ("unit_price_cents", "line_total_cents", "invoice_total_cents", "expected_total",
                  "billed_total", "rate_cents", "provisional"):
        assert field not in text
    assert not re.search(r"GBP \d", text)
    rates = {svc.base_rate_cents for svc in contract.services.values()} | {
        b.rate_a_cents for b in contract.bundles} | {b.rate_b_cents for b in contract.bundles}
    for rate in rates:
        assert not re.search(rf"(?<![\w.-]){rate}(?![\w.-])", text), rate


@pytest.mark.parametrize("consumed", [False, True])
def test_playground_and_api_build_identical_evidence(doc, clusters, contract, matcher, consumed):
    _classify(doc, clusters, contract)
    if consumed:
        _consume_basis(doc, matcher)
    playground_doc = copy.deepcopy(doc)
    exported_state, exported_questions = S.export_jev_batch(playground_doc, contract, config())
    seen = {}

    def capture(state, questions):
        seen.update(state=state, questions=questions)
        return {qid: _verdict("UNCERTAIN", 0.2, 0.2, 0.6) for qid in questions}

    S.run_jev(doc, contract, config(), capture)
    assert seen["state"] == exported_state
    assert seen["questions"] == exported_questions


def test_semantic_basis_consumption_cannot_become_wrong_unit_basis(contract, matcher):
    """Tied text, the billed basis breaks the tie, Jev accepts, and the same
    basis is then barred from supporting wrong_unit_basis -- even when the
    chosen service's contractual basis differs from the consumed one."""
    from src.hospital_2.audit import H2Auditor, audit_resolved, resolve_lines

    occ = [invoice("INV-UB", [line("L-UB1", TIED, 1, 97200, basis="per_hour"),
                              line("L-UB2", TIED, 1, 97200, basis="per_night")])]
    doc = mappings_for(contract, matcher, occ)
    rec = doc["clusters"][S.cluster_id(matcher.match(TIED).normalized_description)]
    rec["used_unit_basis_for_identity"] = True
    rec["billed_unit_basis"] = "per_hour"
    rec["final"] = {"status": MATCHED, "service": TIED_DAY, "source": "semantic_verified",
                    "verified": True, "unresolved_reason": None}
    assert contract.services[TIED_DAY].unit_basis == "per_day"   # differs from the consumed basis

    resolved = resolve_lines(occ, matcher, doc)
    consumed, other = resolved
    assert (consumed.status, consumed.service, consumed.used_unit_basis_for_identity) == (MATCHED, TIED_DAY, True)
    # A line carrying a different basis gets no identity from that decision.
    assert other.status == AMBIGUOUS and not other.used_unit_basis_for_identity and not other.verified_ambiguity

    r = audit_resolved(occ, resolved, H2Auditor(contract))[0]
    line_findings = {(f.line_id, f.category) for f in r.occurrences[0].findings}
    assert ("L-UB1", "wrong_unit_basis") not in line_findings


def test_readiness_never_reveals_credentials():
    text = json.dumps(config(openrouter_api_key="sk-or-SECRET", jev_api_key="jev-SECRET").readiness())
    assert "SECRET" not in text
    assert "ready (test-model)" in text


def test_models_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("H2_CLASSIFIER_MODEL", "some/other-model")
    monkeypatch.setenv("JEV_MODEL", "jev-9.9.9")
    c = S.SemanticConfig.from_env()
    assert (c.classifier_model, c.jev_model) == ("some/other-model", "jev-9.9.9")
    monkeypatch.delenv("H2_CLASSIFIER_MODEL")
    monkeypatch.delenv("JEV_MODEL")
    c = S.SemanticConfig.from_env()
    assert (c.classifier_model, c.jev_model) == ("z-ai/glm-5.3-flash", "jev-1.13.0")


# -- counts ------------------------------------------------------------------------------

def test_counts_separate_clusters_from_line_occurrences(doc):
    c = S.semantic_counts(doc)
    # Fixture: 8 lines of the tied description (2 spellings, 1 cluster), 4 of
    # the missing-specialty one, 2 of a clear match.
    assert c["total_normalized_clusters"] == 3
    assert c["semantic_pending_clusters"] == 2
    assert c["semantic_pending_line_occurrences"] == 12
    assert c["deterministic_matched_clusters"] == 1


def test_deterministic_unknown_is_not_semantic_pending(matcher, contract):
    clusters = cluster_descriptions(["ONC WD BD OCC"] * 3 + [TIED] * 2, matcher)
    c = S.semantic_counts(S.prepare_mappings(clusters, contract, config(), None))
    assert c["deterministic_unknown_clusters"] == 1
    assert c["semantic_pending_clusters"] == 1 and c["semantic_pending_line_occurrences"] == 2
    assert c["total_unresolved_or_unknown_clusters"] == 2
    report = S.unresolved_report(S.prepare_mappings(clusters, contract, config(), None))
    assert [r["normalized_description"] for r in report["deterministic_unknown"]] == ["onc wd bd occ"]
    assert [r["normalized_description"] for r in report["semantic_pending"]] == ["ent rehab prog"]


def test_unit_basis_provisional_lines_are_counted_separately(contract, matcher):
    from src.hospital_2.audit import line_identity_counts, resolve_lines

    occ = [invoice("INV-K", [line("L-K1", TIED, 1, 30775, basis="per_visit"),     # tie broken by basis
                             line("L-K2", TIED, 1, 30775, basis="per_hour"),      # basis supports neither
                             line("L-K3", MISSING, 1, 377750, basis="per_procedure")])]
    doc = mappings_for(contract, matcher, occ)
    counts = {**S.semantic_counts(doc), **line_identity_counts(resolve_lines(occ, matcher, doc), doc)}
    assert counts["semantic_pending_line_occurrences"] == 3
    assert counts["unit_basis_provisional_line_occurrences"] == 1
    assert counts["currently_ambiguous_line_occurrences"] == 2


# -- persistence -------------------------------------------------------------------------

def test_mappings_are_invalidated_when_the_contract_changes(doc, clusters, contract):
    S.run_classifier(doc, clusters, contract, config(), lambda s, u: answer(AMBIGUOUS))
    changed = dataclasses.replace(contract, fingerprint="0" * 64)
    fresh = S.prepare_mappings(clusters, changed, config(), doc)
    assert all(r["classifier"] is None for r in fresh["clusters"].values())
    assert any("fingerprint" in note for note in fresh["invalidated"])
    kept = S.prepare_mappings(clusters, contract, config(), doc)
    assert sum(r["classifier"] is not None for r in kept["clusters"].values()) == 2


def test_persisted_mappings_support_an_offline_audit(contract, matcher, tmp_path, monkeypatch):
    """Write mappings, reload them, and audit with every network call disabled."""
    occ = [invoice("INV-O", [line("L-O1", TIED, 1, 30775, basis="per_visit"),
                             line("L-O2", PLAIN, 1, 141675, basis="per_night")])]
    doc = mappings_for(contract, matcher, occ)
    path = tmp_path / "service_mappings.json"
    S.write_json(path, doc)

    def no_network(*a, **k):
        raise AssertionError("the audit must not open a network connection")
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    from src.hospital_2.audit import H2Auditor, audit_resolved, resolve_lines
    resolved = resolve_lines(occ, matcher, S.load_mappings(path))
    result = audit_resolved(occ, resolved, H2Auditor(contract))[0]
    assert not result.result.flagged


def test_a_verified_semantic_match_is_used_by_the_audit(contract, matcher):
    occ = [invoice("INV-V", [line("L-V", MISSING, 1, 377750, basis="per_procedure")])]
    verified = {MISSING: {"status": MATCHED, "service": MISSING_SVC, "source": "semantic_verified",
                          "verified": True, "unresolved_reason": None}}
    r = run(contract, matcher, occ, overrides=verified)["INV-V"]
    rl = r.occurrences[0].lines[0]
    assert (rl.status, rl.service, rl.source) == (MATCHED, MISSING_SVC, "semantic_verified")
    assert not r.result.flagged and r.result.correction_reconstructable
