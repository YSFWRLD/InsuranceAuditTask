"""Hospital 5 Jev layer: request shape, validation, gates, transports,
export/import, staleness -- and the pinned behaviour of the committed reviews.

No test calls TypeSafe.  API behaviour is exercised through a fake transport
and a patched ``urlopen``; the pinned cases read the committed review files,
which are the stored, real Jev answers."""

import io
import json
import socket
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

from src.hospital_5 import semantic as S
from src.hospital_5 import workflow as W
from src.hospital_5.matcher import H5Matcher
from src.hospital_5.normalization import (
    CONTEXT_REQUIRED, SAFE_GLOBAL, UNSAFE, ContractVocabulary, NormalizationCandidate,
)
from tests.hospital_5.conftest import SUPV_PALL, SUPV_VASC, TEST_LEXICON

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture(scope="module")
def ntemplate():
    return S.normalization_template()


@pytest.fixture(scope="module")
def mtemplate():
    return S.missing_word_template()


def norm_request(contract, ntemplate, token="endo", proposed="endocrine", model="jev-1.13.0"):
    cands = [NormalizationCandidate(token, "endocrine", ("prefix",), ("specialty",)),
             NormalizationCandidate(token, "endoscopic", ("prefix",), ("concept",))]
    cand = next(c for c in cands if c.proposed == proposed)
    fp = S.normalization_fingerprint(contract, ntemplate, model)
    return S.normalization_request(cand, ["endocrine", "endoscopic"], ContractVocabulary.from_contract(contract),
                                   ntemplate, model, fp)


def mw_request(contract, mtemplate, description="SUPV CONSULT", basis="per_procedure"):
    m = H5Matcher(contract, TEST_LEXICON).match(description)
    cluster = S.MissingWordCluster(m.identity_key, basis, m, (description.lower(),), 3)
    return S.missing_word_request(cluster, contract, mtemplate, "jev-1.13.0", {"test": True})


def answer(options, choice, **probs):
    p = {o: 0.0 for o in options}
    p.update(probs)
    return {"type": "choice", "choice": choice, "probabilities": p, "confidence": 0.9}


def numbers_in(obj, path=""):
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [path]
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in numbers_in(v, f"{path}.{k}")]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in numbers_in(v, f"{path}[{i}]")]
    return []


# -- templates and request shape ----------------------------------------------------------------

def test_templates_are_versioned_by_content(ntemplate, mtemplate):
    assert ntemplate.version.startswith("001_jev_normalization_review@") and len(ntemplate.version.split("@")[1]) == 12
    assert mtemplate.version.startswith("002_jev_missing_word_resolution@")
    assert ntemplate.question_id == "normalization_safety"
    assert mtemplate.question_id == "missing_word_resolution"


def test_normalization_request_schema(contract, ntemplate):
    r = norm_request(contract, ntemplate)
    body = r.body
    assert set(body) == {"state", "model", "questions"} and body["model"] == "jev-1.13.0"
    # The root of the questions object is keyed by the question identifier.
    assert list(body["questions"]) == ["normalization_safety"]
    q = body["questions"]["normalization_safety"]
    assert q["type"] == "choice" and set(q["instructions"]) == {"question", "focus"}
    assert list(q["criteria"]) == [SAFE_GLOBAL, CONTEXT_REQUIRED, UNSAFE]
    assert all(set(v) == {"what", "not_for"} for v in q["criteria"].values())
    s = body["state"]
    assert s["candidate_under_review"]["invoice_token"] == "endo"
    assert s["candidate_under_review"]["proposed_normalization"] == "endocrine"
    assert s["colliding_contract_words"] == ["endocrine", "endoscopic"]
    assert {"task", "hard_normalization_rules", "allowed_behavior", "forbidden_behavior",
            "contract_vocabulary", "financial_values"} <= set(s)
    assert numbers_in(s) == []                     # zero financial values -- no numbers at all


def test_missing_word_request_schema(contract, mtemplate):
    r = mw_request(contract, mtemplate)
    q = r.body["questions"]["missing_word_resolution"]
    assert list(r.body["questions"]) == ["missing_word_resolution"]
    assert set(q["instructions"]) == {"question", "focus"}
    assert list(q["criteria"]) == ["supervised_palliative_consultation", "supervised_vascular_consultation",
                                   S.AMBIGUOUS_OPTION, S.NONE_OPTION]
    assert "Supervised Palliative Consultation" in q["criteria"]["supervised_palliative_consultation"]["what"]
    s = r.body["state"]
    assert [c["service_name"] for c in s["candidates"]] == [SUPV_PALL, SUPV_VASC]
    pall = s["candidates"][0]
    assert (pall["qualifier"], pall["specialty"], pall["concept"]) == ("supervised", "palliative", "consultation")
    assert pall["matching_fields"] == ["qualifier", "concept"] and pall["missing_fields"] == ["specialty"]
    assert pall["contracted_unit_basis"] == "per procedure" and s["billed_unit_basis"] == "per_procedure"
    # Neighbours are shown with what contradicts them (here: the qualifier).
    names = {c["service_name"]: c["contradicted_by"] for c in s["contradicted_services"]}
    assert names["Comprehensive Palliative Consultation"] == ["qualifier (supervised vs comprehensive)"]
    # The only number in the state is the occurrence count: no price, rate or total.
    assert numbers_in(s) == [".description.occurrence_count"]
    dynamic = json.dumps({k: s[k] for k in ("description", "billed_unit_basis", "candidates",
                                            "contradicted_services")})
    for word in ("price", "cents", "total", "GBP", "rate", "invoice", "patient"):
        assert word not in dynamic.lower(), word


def test_request_hash_covers_state_question_and_model(contract, ntemplate):
    a = norm_request(contract, ntemplate)
    assert a.request_sha256 == norm_request(contract, ntemplate).request_sha256
    assert a.request_sha256 != norm_request(contract, ntemplate, proposed="endoscopic").request_sha256
    assert a.request_sha256 != norm_request(contract, ntemplate, model="jev-9").request_sha256


# -- answers ----------------------------------------------------------------------------------

@pytest.mark.parametrize("mutate,message", [
    (lambda a: a.update(choice="maybe"), "not one of"),
    (lambda a: a["probabilities"].pop(UNSAFE), "exactly"),
    (lambda a: a["probabilities"].update({SAFE_GLOBAL: 0.5}), "sum"),
    (lambda a: a.update(choice=UNSAFE), "most probable"),
    (lambda a: a.update(confidence=1.5), "confidence"),
    (lambda a: a.update(model="other"), "model"),
])
def test_validate_answer_refuses(mutate, message):
    a = answer(S.NORMALIZATION_OPTIONS, SAFE_GLOBAL, **{SAFE_GLOBAL: 0.9, CONTEXT_REQUIRED: 0.05, UNSAFE: 0.05})
    mutate(a)
    with pytest.raises(S.SemanticError, match=message):
        S.validate_answer(a, S.NORMALIZATION_OPTIONS, "jev-1.13.0")


def test_answer_from_response(contract, ntemplate):
    r = norm_request(contract, ntemplate)
    a = answer(r.options, CONTEXT_REQUIRED, **{CONTEXT_REQUIRED: 0.98, SAFE_GLOBAL: 0.01, UNSAFE: 0.01})
    parsed, raw = S.answer_from_response({"model": r.model, "answers": {"normalization_safety": a},
                                          "usage": {"input_tokens": 1}}, r)
    assert parsed["choice"] == CONTEXT_REQUIRED and raw is a
    with pytest.raises(S.SemanticError, match="expected exactly"):
        S.answer_from_response({"answers": {"other": a}}, r)


# -- missing-word gates -----------------------------------------------------------------------

def test_missing_word_gates(contract, mtemplate):
    r = mw_request(contract, mtemplate)
    o = r.options
    g = S.GateConfig()
    service = S.gate_missing_word(r, answer(o, o[0], **{o[0]: 0.95, S.AMBIGUOUS_OPTION: 0.05}), g)
    assert (service.outcome, service.service) == ("service", SUPV_PALL)
    closed = S.gate_missing_word(r, answer(o, S.AMBIGUOUS_OPTION, **{S.AMBIGUOUS_OPTION: 1.0}), g)
    assert closed.outcome == "ambiguous_contracted"
    weak = S.gate_missing_word(r, answer(o, o[0], **{o[0]: 0.5, S.NONE_OPTION: 0.5}), g)
    assert weak.outcome == "unresolved"
    none = S.gate_missing_word(r, answer(o, S.NONE_OPTION, **{S.NONE_OPTION: 0.95, o[0]: 0.05}), g)
    assert none.outcome == "unknown"


# -- adversarial: one contracted candidate --------------------------------------------------------
#
# A single-candidate closure rule (P(candidate) + P(ambiguous) >= 0.90) was
# tried and removed in the pre-commit review.  These tests pin its absence.

def single(contract, mtemplate, description, basis):
    r = mw_request(contract, mtemplate, description, basis)
    assert len(r.state["candidates"]) == 1
    return r, r.options


def test_hedge_on_the_ambiguous_option_is_not_confirmation(contract, mtemplate):
    r, o = single(contract, mtemplate, "COMPR CONSULT", "per_visit")
    assert o == ("comprehensive_palliative_consultation", S.AMBIGUOUS_OPTION, S.NONE_OPTION)
    # The four reviewed clusters had exactly this shape: the only candidate at
    # 0.80-0.89, the rest on "ambiguous", nothing on "none".
    for p in (0.89, 0.80):
        hedged = answer(o, o[0], **{o[0]: p, S.AMBIGUOUS_OPTION: round(1 - p, 2)})
        d = S.gate_missing_word(r, hedged, S.GateConfig())
        assert d.outcome == "unresolved", p
    assert not hasattr(S.GateConfig(), "single_candidate_closure")


def test_case_1_qualifier_and_concept_without_specialty(contract, mtemplate):
    """The contract's only comprehensive consultation: accepted only on a
    clear choice, exactly as any other missing-word review."""
    r, o = single(contract, mtemplate, "COMPR CONSULT", "per_visit")
    assert r.state["candidates"][0]["missing_fields"] == ["specialty"]
    ok = S.gate_missing_word(r, answer(o, o[0], **{o[0]: 0.97, S.AMBIGUOUS_OPTION: 0.03}), S.GateConfig())
    assert (ok.outcome, ok.service) == ("service", "Comprehensive Palliative Consultation")


def test_case_2_concept_only_is_never_enough(contract, mtemplate):
    """"SPECIMEN ANALYSIS" fits one contracted service only because the
    contract has one: two of three name slots are missing.  No review
    confidence can supply both."""
    r, o = single(contract, mtemplate, "SPECIMEN ANALYSIS", "per_item")
    assert r.state["candidates"][0]["missing_fields"] == ["qualifier", "specialty"]
    d = S.gate_missing_word(r, answer(o, o[0], **{o[0]: 0.99, S.AMBIGUOUS_OPTION: 0.01}), S.GateConfig())
    assert d.outcome == "unresolved" and "at most 1" in d.reason


def test_case_2_multi_candidate_closure_needs_the_same_evidence(contract, mtemplate):
    r = mw_request(contract, mtemplate, "CONSULT", "per_visit")          # five consultations, two slots missing
    d = S.gate_missing_word(r, answer(r.options, S.AMBIGUOUS_OPTION, **{S.AMBIGUOUS_OPTION: 1.0}), S.GateConfig())
    assert d.outcome == "unresolved"


def test_case_3_an_unread_token_in_the_missing_slot(contract, mtemplate):
    """The unread token is shown to the reviewer.  A clear choice is accepted
    (the reviewer read the token in context); a hedge is not."""
    r, o = single(contract, mtemplate, "SUPERVISED XYZ SPECIMEN ANALYSIS", "per_item")
    assert r.state["description"]["unread_tokens"] == ["xyz"]
    hedge = S.gate_missing_word(r, answer(o, o[0], **{o[0]: 0.8, S.AMBIGUOUS_OPTION: 0.2}), S.GateConfig())
    assert hedge.outcome == "unresolved"
    doubt = S.gate_missing_word(r, answer(o, o[0], **{o[0]: 0.6, S.NONE_OPTION: 0.4}), S.GateConfig())
    assert doubt.outcome == "unresolved"


def test_case_5_a_plausible_uncontracted_service(contract, mtemplate):
    r, o = single(contract, mtemplate, "COMPR CONSULT", "per_visit")
    for probs in ({o[0]: 0.7, S.NONE_OPTION: 0.3}, {o[0]: 0.5, S.AMBIGUOUS_OPTION: 0.25, S.NONE_OPTION: 0.25}):
        assert S.gate_missing_word(r, answer(o, o[0], **probs), S.GateConfig()).outcome == "unresolved"
    none = S.gate_missing_word(r, answer(o, S.NONE_OPTION, **{S.NONE_OPTION: 0.95, o[0]: 0.05}), S.GateConfig())
    assert none.outcome == "unknown"


# -- transports ---------------------------------------------------------------------------------

class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, body: dict, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_transport_retries_then_succeeds(monkeypatch, contract, ntemplate):
    r = norm_request(contract, ntemplate)
    good = {"model": r.model, "answers": {"normalization_safety": answer(r.options, UNSAFE, **{UNSAFE: 1.0})}}
    calls, sleeps = [], []

    def fake_urlopen(req, timeout):
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer secret-key"
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"busy"))
        return FakeResponse(good, {"X-Request-Id": "req-123"})

    monkeypatch.setattr(S.urllib.request, "urlopen", fake_urlopen)
    transport = S.http_transport(S.JevConfig(api_key="secret-key"), sleep=sleeps.append)
    body, meta = transport(r.body)
    assert body == good and meta["request_id"] == "req-123" and meta["attempts"] == 2 and sleeps == [1]
    assert json.loads(calls[0].data) == r.body


def test_http_transport_does_not_retry_a_client_error_or_leak_the_key(monkeypatch, contract, ntemplate):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"unauthorised"))

    monkeypatch.setattr(S.urllib.request, "urlopen", fake_urlopen)
    transport = S.http_transport(S.JevConfig(api_key="secret-key"), sleep=lambda s: None)
    with pytest.raises(S.SemanticError) as exc:
        transport(norm_request(contract, ntemplate).body)
    assert "401" in str(exc.value) and "secret-key" not in str(exc.value)


def test_no_key_means_no_call():
    with pytest.raises(S.SemanticError, match="TYPESAFE_API_KEY"):
        S.http_transport(S.JevConfig(api_key=None))


def test_run_requests_stores_answers_and_skips_current(tmp_path, contract, ntemplate, no_network):
    reqs = [norm_request(contract, ntemplate, proposed=p) for p in ("endocrine", "endoscopic")]
    asked = []

    def fake(body):
        asked.append(body)
        return {"model": "jev-1.13.0", "answers": {"normalization_safety": answer(
            S.NORMALIZATION_OPTIONS, CONTEXT_REQUIRED, **{CONTEXT_REQUIRED: 1.0})}}, {"request_id": None}

    store = tmp_path / "reviews.jsonl"
    stats = S.run_requests(reqs, fake, store)
    assert stats["answered"] == 2 and len(asked) == 2
    recs = S.load_reviews(store)
    rec = recs["endo->endocrine"]
    assert rec["request_sha256"] == reqs[0].request_sha256 and rec["state_sha256"] == reqs[0].state_sha256
    assert rec["answer"]["choice"] == CONTEXT_REQUIRED and rec["model"] == "jev-1.13.0" and rec["source"] == "api"
    assert S.run_requests(reqs, fake, store)["asked"] == 0 and len(asked) == 2
    # A different model makes every stored review stale.
    other = [norm_request(contract, ntemplate, proposed=p, model="jev-2") for p in ("endocrine", "endoscopic")]
    current, pending, stale = S.partition(other, S.load_reviews(store))
    assert not current and len(pending) == 2 and stale == ["endo->endocrine", "endo->endoscopic"]
    with pytest.raises(S.StaleSemanticArtifacts, match="stale"):
        S.require_current(other, S.load_reviews(store), "normalisation reviews", "rerun")


def test_export_import_round_trip_is_all_or_nothing(tmp_path, contract, ntemplate):
    reqs = [norm_request(contract, ntemplate, proposed=p) for p in ("endocrine", "endoscopic")]
    export, store = tmp_path / "export.json", tmp_path / "reviews.jsonl"
    assert S.export_requests(reqs, {}, export) == 2
    exported = json.loads(export.read_text(encoding="utf-8"))["requests"]
    assert exported[0]["request"] == reqs[0].body
    results = {"model": "jev-1.13.0", "results": {
        e["review_id"]: {"request_sha256": e["request_sha256"], "choice": CONTEXT_REQUIRED,
                         "probabilities": {SAFE_GLOBAL: 0.01, CONTEXT_REQUIRED: 0.98, UNSAFE: 0.01},
                         "confidence": 0.97} for e in exported}}
    bad = json.loads(json.dumps(results))
    bad["results"]["endo->endoscopic"]["request_sha256"] = "0" * 64
    with pytest.raises(S.SemanticError, match="request_sha256"):
        S.import_results(bad, reqs, store)
    assert not store.exists()                                  # nothing was stored
    assert S.import_results(results, reqs, store) == {"imported": 2}
    current, pending, _ = S.partition(reqs, S.load_reviews(store))
    assert len(current) == 2 and not pending
    assert S.load_reviews(store)["endo->endocrine"]["source"] == "playground_import"


# -- the committed reviews: pinned behaviour --------------------------------------------------

@pytest.fixture(scope="module")
def committed():
    """The stored, real Jev reviews (read from artifacts; nothing is called)."""
    return W.load_semantics(strict=True)


def stored(review_id, path=S.NORMALIZATION_REVIEWS):
    return S.load_reviews(path)[review_id]["answer"]


def test_committed_reviews_are_current(committed):
    assert committed.normalization_complete and committed.missing_word_complete
    assert len(committed.normalization_requests) == 266 and len(committed.missing_word_requests) == 33


def test_pinned_neuro_is_safe_global(committed):
    assert stored("neuro->neurological")["choice"] == SAFE_GLOBAL
    assert committed.normalization_decisions["neuro->neurological"]["decision"] == SAFE_GLOBAL
    assert committed.lexicon.global_map["neuro"] == ("neurological",)


def test_pinned_endo_is_context_required(committed):
    assert stored("endo->endocrine")["choice"] == CONTEXT_REQUIRED
    assert committed.normalization_decisions["endo->endocrine"]["decision"] == CONTEXT_REQUIRED
    assert set(committed.lexicon.contextual["endo"]) == {("endocrine",), ("endoscopic",)}


def test_pinned_img_to_diagnostic_imaging_is_not_globally_safe(committed):
    assert committed.normalization_decisions["img->diagnostic imaging"]["decision"] != SAFE_GLOBAL
    assert committed.lexicon.global_map.get("img") != ("diagnostic", "imaging")
    assert ("diagnostic", "imaging") not in committed.lexicon.contextual.get("img", ())


def test_pinned_invented_specialty_is_unsafe(committed):
    assert stored("consult->palliative consultation")["choice"] == UNSAFE
    assert committed.normalization_decisions["consult->palliative consultation"]["decision"] == UNSAFE
    assert committed.lexicon.global_map["consult"] == ("consultation",)


def test_the_genuinely_missing_word_clusters_stay_unresolved(committed):
    """Pre-commit review: Jev chose the only candidate at 0.88-0.89 with the
    rest on "ambiguous".  The discriminator is absent from the text; below the
    pre-declared bar, they stay unresolved.  No closure rule applies."""
    hedged = {("analysis specimen supervised", "per_item"): 0.89,       # SUPERVISED SPCM ANLY: no specialty
              ("home urologic visit", "per_visit"): 0.89,               # UROL HM VST: no qualifier
              ("physiotherapy routine session", "per_visit"): 0.88}     # RTN PHYSIOTHERAPY SESS: no specialty
    for key, p in hedged.items():
        a = stored(" | ".join(key), S.MISSING_WORD_REVIEWS)
        assert round(max(a["probabilities"].values()), 2) == p and a["probabilities"][S.NONE_OPTION] == 0.0
        assert committed.missing_word_decisions[key].outcome == "unresolved", key
    outcomes = [d.outcome for d in committed.missing_word_decisions.values()]
    assert (outcomes.count("service"), outcomes.count("ambiguous_contracted"), outcomes.count("unresolved")) == (28, 1, 4)


def test_the_ent_cluster_is_no_longer_a_missing_word_question(committed):
    """SUPV ENT SPCM ANLY carries its specialty, abbreviated: the reviewed
    contextual reading identifies it by structure, so it is not asked."""
    keys = {k for k, _ in committed.missing_word_decisions}
    assert not any("?ent" in k for k in keys)
    retired = [r for r in S.load_reviews(S.MISSING_WORD_REVIEWS).values() if "retired" in r]
    assert sorted(r["review_id"] for r in retired) == [
        "analysis specimen supervised ?ent | per_item", "continuous service transport ?ent | per_item",
        "continuous transport ?ent | per_item"]


def test_two_slots_missing_is_never_closed(committed):
    """DISP INPT REN PHARM: Jev said "one of the contracted dispensings"
    (P=0.96), but only the concept is evidenced -- the guard refuses."""
    key = ("dispensing pharmaceutical ?inpt ?ren", "per_visit")
    assert stored(" | ".join(key), S.MISSING_WORD_REVIEWS)["choice"] == S.AMBIGUOUS_OPTION
    assert committed.missing_word_decisions[key].outcome == "unresolved"


def test_fingerprint_migrations_left_every_answer_untouched():
    for path in (S.MISSING_WORD_REVIEWS, S.PROBE_REVIEWS):
        for rec in S.load_reviews(path).values():
            for m in rec.get("fingerprint_migrations", []):
                assert m["reason"] and m["previous_fingerprint_sha256"] != rec["fingerprint_sha256"]
            assert "lexicon" not in rec["fingerprint"] or "retired" in rec
            assert rec["raw_answer"]["choice"] == rec["answer"]["choice"]


def test_pinned_case_a_comprehensive_consultation_is_resolved(committed):
    rid = "comprehensive consultation | per_visit"
    assert stored(rid, S.MISSING_WORD_REVIEWS)["choice"] == "comprehensive_palliative_consultation"
    d = committed.missing_word_decisions[("comprehensive consultation", "per_visit")]
    assert (d.outcome, d.service) == ("service", "Comprehensive Palliative Consultation")


def test_pinned_case_b_supervised_consultation_stays_ambiguous(committed):
    """Constructed stress test: the missing specialty is the only discriminator."""
    (req,) = W.probe_requests(committed)
    assert req.review_id == "consultation supervised | per_procedure"
    rec = S.load_reviews(S.PROBE_REVIEWS)[req.review_id]
    assert S.is_current(rec, req)
    assert rec["answer"]["choice"] == S.AMBIGUOUS_OPTION
    assert S.gate_missing_word(req, rec["answer"], S.GateConfig()).outcome == "ambiguous_contracted"
    assert [c["contracted_unit_basis_token"] for c in req.state["candidates"]] == ["per_procedure"] * 2


def test_stale_template_is_refused(tmp_path, monkeypatch):
    changed = tmp_path / "001_jev_normalization_review.md"
    changed.write_text(S.NORMALIZATION_TEMPLATE.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    monkeypatch.setattr(S, "NORMALIZATION_TEMPLATE", changed)
    with pytest.raises(S.StaleSemanticArtifacts, match="normalisation reviews"):
        W.load_semantics(strict=True)


def test_stale_model_is_refused(monkeypatch):
    monkeypatch.setenv("JEV_MODEL", "jev-99")
    with pytest.raises(S.StaleSemanticArtifacts):
        W.load_semantics(strict=True)
