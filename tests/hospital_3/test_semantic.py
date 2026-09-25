"""Hospital 3 Jev missing-word stage: what is sent, how answers are gated,
and that the committed reviews reproduce the audit offline.  Nothing here
touches the network."""

import copy
import json
import re

import pytest

from src.hospital_3 import semantic as S
from src.hospital_3.matcher import MissingWordDecision


@pytest.fixture(scope="module")
def requests(contract, occurrences, matcher):
    return S.missing_word_requests(contract, occurrences, matcher, S.JevConfig())


def answer(req, probs: dict, confidence=0.9):
    full = {o: 0.0 for o in req.options}
    full.update(probs)
    choice = max(full, key=full.get)
    return {"choice": choice, "probabilities": full, "confidence": confidence}


def request_for(requests, prefix):
    return next(r for r in requests if r.review_id.startswith(prefix))


# -- what is asked ---------------------------------------------------------------------------------

def test_one_bounded_choice_question_per_cluster(requests):
    assert len(requests) == 38
    assert sum(r.state["description"]["occurrence_count"] for r in requests) == 890
    for r in requests:
        assert list(r.body["questions"]) == ["missing_word_resolution"]
        q = r.body["questions"]["missing_word_resolution"]
        assert q["type"] == "choice" and set(q["instructions"]) == {"question", "focus"}
        assert r.body["model"] == "jev-1.13.0"
        assert 1 <= len(r.state["candidates"]) <= 8
        assert set(r.options) == {c["option"] for c in r.state["candidates"]} | {S.AMBIGUOUS_OPTION, S.NONE_OPTION}


def test_no_money_date_or_identifier_is_sent(requests):
    """The evidence the code adds to the template contains no price, rate,
    quantity, date or identifier: no digit at all, apart from the cluster's
    line count."""
    static = set(S.load_template().body["state_static"])
    for r in requests:
        dynamic = {k: v for k, v in copy.deepcopy(r.state).items() if k not in static}
        assert isinstance(dynamic["description"].pop("occurrence_count"), int)
        text = json.dumps(dynamic)
        assert not re.search(r"\d", text), r.review_id
        forbidden = r"\b(price|prices|cents|rate|rates|total|totals|invoice|patient|date|dates)\b"
        assert not re.search(forbidden, text, flags=re.I), r.review_id


def test_template_is_hospital_3s_own_file_with_hospital_5s_question():
    t = S.load_template()
    assert t.name == "001_jev_missing_word_resolution" and t.version.startswith(t.name + "@")
    h5 = (S.REPO_ROOT / "prompts" / "hospital_5" / "002_jev_missing_word_resolution.md").read_text(encoding="utf-8")
    h5_block = json.loads(re.search(r"```json\n(.*?)\n```", h5, flags=re.S).group(1))
    assert t.body == h5_block                                  # the question was not re-tuned for H3


# -- the gates --------------------------------------------------------------------------------------------

def test_default_gates_are_the_predeclared_ones():
    g = S.GateConfig()
    assert (g.service_min_probability, g.service_min_confidence, g.closure_min_probability,
            g.unknown_min_probability, g.max_candidates) == (0.90, 0.80, 0.90, 0.90, 8)
    assert S.MAX_SLOTS_SUPPLIED_BY_REVIEW == 1


def test_service_gate_boundary(requests):
    r = request_for(requests, "endoscopic extended procedure")
    opt = r.state["candidates"][0]["option"]
    g = S.GateConfig()
    assert S.gate_missing_word(r, answer(r, {opt: 0.90, S.AMBIGUOUS_OPTION: 0.10}, 0.80), g).outcome == "service"
    assert S.gate_missing_word(r, answer(r, {opt: 0.89, S.AMBIGUOUS_OPTION: 0.11}, 0.95), g).outcome == "unresolved"
    assert S.gate_missing_word(r, answer(r, {opt: 0.95, S.AMBIGUOUS_OPTION: 0.05}, 0.79), g).outcome == "unresolved"


def test_no_single_candidate_closure(requests):
    """With one candidate, mass on 'ambiguous' is hedging, not support."""
    r = request_for(requests, "endoscopic extended procedure")
    opt = r.state["candidates"][0]["option"]
    d = S.gate_missing_word(r, answer(r, {opt: 0.60, S.AMBIGUOUS_OPTION: 0.40}), S.GateConfig())
    assert d.outcome == "unresolved"


def test_closure_needs_two_candidates(requests):
    r = request_for(requests, "inpatient theatre time")
    assert len(r.state["candidates"]) == 2
    d = S.gate_missing_word(r, answer(r, {S.AMBIGUOUS_OPTION: 0.95, S.NONE_OPTION: 0.05}), S.GateConfig())
    assert d.outcome == "ambiguous_contracted"


def test_review_may_supply_at_most_one_missing_slot(requests):
    r = copy.deepcopy(request_for(requests, "endoscopic extended procedure"))
    r.state["candidates"][0]["missing_fields"] = ["qualifier", "specialty"]
    opt = r.state["candidates"][0]["option"]
    assert S.gate_missing_word(r, answer(r, {opt: 0.99}, 0.99), S.GateConfig()).outcome == "unresolved"


def test_none_of_the_above(requests):
    r = request_for(requests, "endoscopic extended procedure")
    assert S.gate_missing_word(r, answer(r, {S.NONE_OPTION: 0.95}), S.GateConfig()).outcome == "unknown"


def test_answer_validation(requests):
    r = requests[0]
    with pytest.raises(S.SemanticError):
        S.validate_answer({"choice": "nope", "probabilities": {}}, r.options, r.model)
    bad = answer(r, {S.NONE_OPTION: 0.5})
    bad["probabilities"][S.AMBIGUOUS_OPTION] = 0.9                 # not the top choice, sums past 1
    with pytest.raises(S.SemanticError):
        S.validate_answer(bad, r.options, r.model)


# -- the committed reviews ---------------------------------------------------------------------------------

def test_committed_reviews_are_current_and_complete(requests):
    reviews = S.load_reviews()
    current, pending, stale = S.partition(requests, reviews)
    assert len(current) == 38 and not pending and not stale
    assert {rec["model"] for rec in reviews.values()} == {"jev-1.13.0"}
    assert {rec["template_version"] for rec in reviews.values()} == {S.load_template().version}
    for rec in reviews.values():
        assert S.validate_answer(rec["raw_answer"], tuple(rec["options"]), rec["model"])["choice"] == \
            rec["answer"]["choice"]


def test_committed_decisions(contract, occurrences, matcher):
    decisions = S.load_decisions(contract, occurrences, matcher)
    outcomes = {}
    for d in decisions.values():
        outcomes[d.outcome] = outcomes.get(d.outcome, 0) + 1
    assert outcomes == {"service": 34, "ambiguous_contracted": 3, "unresolved": 1}
    # The one below the bar stays below it: no post-hoc rule rescues it.
    below = decisions[("dispensing inpatient pharmaceutical", "per_item")]
    assert below == MissingWordDecision("unresolved", None, below.reason) and "0.890" in below.reason


def test_stale_or_missing_reviews_are_refused(contract, occurrences, matcher, tmp_path):
    path = tmp_path / "reviews.jsonl"
    reviews = S.load_reviews()
    first = sorted(reviews)[0]
    reviews[first] = {**reviews[first], "request_sha256": "0" * 64}
    S.save_reviews(path, reviews)
    with pytest.raises(S.StaleSemanticArtifacts, match="stale"):
        S.load_decisions(contract, occurrences, matcher, path=path)
    with pytest.raises(S.StaleSemanticArtifacts, match="missing"):
        S.load_decisions(contract, occurrences, matcher, path=tmp_path / "none.jsonl")
    assert len(S.load_decisions(contract, occurrences, matcher, path=path, strict=False)) == 37


def test_run_export_and_import_use_the_same_validated_path(requests, tmp_path):
    store = tmp_path / "reviews.jsonl"
    one = requests[:2]

    def fake(body):
        q = next(iter(body["questions"]))
        opts = list(body["questions"][q]["criteria"])
        probs = {o: 0.0 for o in opts}
        probs[S.AMBIGUOUS_OPTION] = 1.0
        return {"answers": {q: {"choice": S.AMBIGUOUS_OPTION, "probabilities": probs, "confidence": 1.0}}}, {}

    stats = S.run_requests(one, fake, store)
    assert stats["answered"] == 2 and not stats["failed"]
    assert S.export_requests(requests[:3], S.load_reviews(store), tmp_path / "export.json") == 1
    r = requests[2]
    good = {"results": {r.review_id: {"request_sha256": r.request_sha256, **answer(r, {S.NONE_OPTION: 1.0})}}}
    assert S.import_results(good, requests, store) == {"imported": 1}
    bad = {"results": {r.review_id: {"request_sha256": "x", **answer(r, {S.NONE_OPTION: 1.0})}}}
    with pytest.raises(S.SemanticError, match="nothing imported"):
        S.import_results(bad, requests, store)


def test_the_audit_never_calls_jev(monkeypatch):
    from src.hospital_3.audit import H3Pipeline

    def boom(*a, **k):
        raise AssertionError("the audit must not make a network call")

    monkeypatch.setattr(S, "http_transport", boom)
    monkeypatch.setattr(S.urllib.request, "urlopen", boom)
    assert len(H3Pipeline(check_csv=False).run()) == 932


def test_stored_request_bodies_match_and_carry_no_money():
    """The committed request bodies are exactly what the code sends; their only
    digits are the model name and each cluster's line count, and 'price'
    occurs only in the fixed instructions that forbid it."""
    doc = json.loads(S.MISSING_WORD_REQUESTS_FILE.read_text(encoding="utf-8"))
    static = json.dumps(S.load_template().body)
    assert len(doc["requests"]) == 38
    for e in doc["requests"]:
        body = e["body"]
        assert S.sha256_of(body) == e["request_sha256"]
        assert set(body) == {"state", "model", "questions"}
        text = json.dumps(body)
        runs = re.findall(r"\d+(?:\.\d+)*", text)
        count = body["state"]["description"]["occurrence_count"]
        assert sorted(runs) == sorted(["1.13.0", str(count)]), e["review_id"]
        assert text.count("price") == static.count("price"), e["review_id"]
