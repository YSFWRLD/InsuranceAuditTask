"""Hospital 5 normalisation: candidate generation, gates and the lexicon.

Generation must be broad and deterministic and must never read money;
acceptance is decided by gates in code, with two invariants that hold
whatever a review says."""

import inspect

from src.hospital_5 import normalization as N
from src.hospital_5.normalization import (
    CONTEXT_REQUIRED, SAFE_GLOBAL, UNSAFE, ContractVocabulary, Lexicon, NormalizationCandidate, build_lexicon,
    corpus_tokens, normalize_description, propose_candidates, spelling_variants, strip_reference,
)
from src.hospital_5.semantic import GateConfig, gate_normalization


def ans(choice, safe, ctx, unsafe, confidence):
    return {"choice": choice, "probabilities": {SAFE_GLOBAL: safe, CONTEXT_REQUIRED: ctx, UNSAFE: unsafe},
            "confidence": confidence}


def proposals(contract, *tokens):
    return propose_candidates(tokens, ContractVocabulary.from_contract(contract))


# -- description normalisation ------------------------------------------------------------

def test_reference_suffix_is_stripped_for_matching_only():
    raw = "OBS  DERM NURSING /PH-3348"
    assert strip_reference(raw).strip() == "OBS  DERM NURSING"
    assert normalize_description(raw) == "obs derm nursing"
    assert normalize_description("RTN - PSYCH PHYSIO SESSION") == "rtn psych physio session"
    assert raw == "OBS  DERM NURSING /PH-3348"       # the raw value is never altered


def test_corpus_tokens_count_distinct_descriptions():
    toks = corpus_tokens(["EXT ENDO THTR TM", "EXT ENDO THTR TM /PH-1", "FOC ENDO THTR TIME"])
    assert toks["endo"] == 2 and toks["ext"] == 1 and "ph" not in toks


# -- candidate generation -----------------------------------------------------------------

def test_spelling_variants():
    assert "pediatric" in spelling_variants("paediatric")
    assert "hematology" in spelling_variants("haematology")
    assert "sterilization" in spelling_variants("sterilisation")
    assert "theater" in spelling_variants("theatre")
    assert "program" in spelling_variants("programme")


def test_generation_is_broad(contract):
    by = {}
    for c in proposals(contract, "neuro", "endo", "img", "consult", "thtr", "ent", "pediatric", "ped", "ward"):
        by.setdefault(c.token, {})[c.proposed] = c.heuristics
    assert by["neuro"] == {"neurological": ("prefix",)}
    assert set(by["endo"]) >= {"endocrine", "endoscopic"}                  # the collision is exposed
    assert "diagnostic imaging" in by["img"] and "imaging" in by["img"]    # the phrase is proposed ...
    assert by["img"]["diagnostic imaging"] == ("concept_phrase",)
    assert by["consult"]["palliative consultation"] == ("specialty_phrase",)  # ... to be rejected
    assert by["thtr"]["theatre"] == ("subsequence",)
    assert by["ent"] == {"otolaryngologic": ("clinical_acronym",)}
    assert "spelling_variant" in by["pediatric"]["paediatric"]
    assert by["ped"]["paediatric"] == ("prefix",)
    assert "ward" not in by                                                # nothing to propose


def test_contract_words_and_noise_are_not_proposed(contract):
    assert proposals(contract, "metabolic", "the", "per", "12") == []


def test_generation_is_deterministic(contract):
    assert proposals(contract, "cr", "cs", "gi") == proposals(contract, "gi", "cs", "cr")


def test_generation_never_sees_money():
    params = set(inspect.signature(propose_candidates).parameters)
    assert params == {"tokens", "vocab"}
    source = inspect.getsource(N)
    for word in ("price", "cents", "total", "quantity"):
        assert f".{word}" not in source and f"{word}_" not in source


# -- gates ------------------------------------------------------------------------------------

G = GateConfig()
WORD = NormalizationCandidate("neuro", "neurological", ("prefix",), ("specialty",))
CONCEPT_PHRASE = NormalizationCandidate("img", "diagnostic imaging", ("concept_phrase",), ("concept", "concept"))
SPECIALTY_PHRASE = NormalizationCandidate("consult", "palliative consultation", ("specialty_phrase",),
                                          ("specialty", "concept"))


def test_global_needs_choice_probability_and_confidence():
    assert gate_normalization(WORD, ans(SAFE_GLOBAL, .97, .01, .02, .96), G)[0] == SAFE_GLOBAL
    # Chosen, but below the probability bar: demoted to context, not promoted.
    assert gate_normalization(WORD, ans(SAFE_GLOBAL, .84, .14, .02, .76), G)[0] == CONTEXT_REQUIRED
    # Probability clears but reported confidence does not.
    assert gate_normalization(WORD, ans(SAFE_GLOBAL, .90, .05, .05, .80), G)[0] == CONTEXT_REQUIRED


def test_context_and_rejection():
    assert gate_normalization(WORD, ans(CONTEXT_REQUIRED, .01, .98, .01, .98), G)[0] == CONTEXT_REQUIRED
    assert gate_normalization(WORD, ans(SAFE_GLOBAL, .61, .10, .29, .41), G)[0] == UNSAFE   # too uncertain
    assert gate_normalization(WORD, ans(UNSAFE, .30, .30, .40, .10), G)[0] == UNSAFE


def test_invariant_a_phrase_is_never_global():
    decision, reason = gate_normalization(CONCEPT_PHRASE, ans(SAFE_GLOBAL, .99, .0, .01, .99), G)
    assert decision == CONTEXT_REQUIRED and "invariant" in reason


def test_invariant_normalisation_never_creates_a_discriminator():
    decision, reason = gate_normalization(SPECIALTY_PHRASE, ans(SAFE_GLOBAL, 1.0, 0, 0, 1.0), G)
    assert decision == UNSAFE and "invariant" in reason


def test_invariant_two_letter_tokens_are_never_global():
    rm = NormalizationCandidate("rm", "room", ("subsequence",), ("concept",))
    decision, reason = gate_normalization(rm, ans(SAFE_GLOBAL, .99, .0, .01, .99), G)
    assert decision == CONTEXT_REQUIRED and "2-letter" in reason
    three = NormalizationCandidate("svc", "service", ("subsequence",), ("concept",))
    assert gate_normalization(three, ans(SAFE_GLOBAL, .99, .0, .01, .99), G)[0] == SAFE_GLOBAL


def test_bd_bedside_is_overridden_with_provenance():
    """Jev judged BD -> bedside safe (P=0.96): the review state listed only
    contract words as collisions.  A recorded human override rejects it."""
    bd = NormalizationCandidate("bd", "bedside", ("subsequence",), ("qualifier",))
    decision, reason = gate_normalization(bd, ans(SAFE_GLOBAL, .96, .02, .02, .95), G)
    assert decision == UNSAFE and reason.startswith("human review override")


def test_ent_is_allowed_in_context_only_by_human_review():
    """Jev chose safe for ENT -> otolaryngologic at P=0.61, below the gate.
    Human review allows it in context -- never globally."""
    ent = NormalizationCandidate("ent", "otolaryngologic", ("clinical_acronym",), ("specialty",))
    decision, reason = gate_normalization(ent, ans(SAFE_GLOBAL, .61, .10, .29, .41), G)
    assert decision == CONTEXT_REQUIRED and "hospital_5_contextual" in reason
    assert gate_normalization(ent, ans(SAFE_GLOBAL, .61, .10, .29, .41), G, overrides={})[0] == UNSAFE


def test_the_human_review_file(tmp_path):
    from src.hospital_5.semantic import HUMAN_REVIEW_FILE, SemanticError, load_review_overrides
    import json
    import pytest
    doc = json.loads(HUMAN_REVIEW_FILE.read_text(encoding="utf-8"))
    assert {e["candidate_id"] for e in doc["overrides"]} == {"bd->bedside", "ent->otolaryngologic"}
    assert all(e["status"] == "human_reviewed" and e["global"] is False and e["reason"] for e in doc["overrides"])
    ent = next(e for e in doc["overrides"] if e["token"] == "ent")
    assert (ent["canonical"], ent["scope"], ent["decision"]) == ("otolaryngologic", "hospital_5_contextual",
                                                                  CONTEXT_REQUIRED)
    for bad in ({"decision": SAFE_GLOBAL}, {"global": True}, {"reason": " "}, {"candidate_id": "ent->x"}):
        path = tmp_path / "overrides.json"
        path.write_text(json.dumps({"overrides": [{**ent, **bad}]}), encoding="utf-8")
        with pytest.raises(SemanticError):
            load_review_overrides(path)


def test_thresholds_are_configurable(monkeypatch):
    monkeypatch.setenv("H5_JEV_GLOBAL_MIN_PROBABILITY", "0.8")
    monkeypatch.setenv("H5_JEV_GLOBAL_MIN_CONFIDENCE", "0.7")
    g = GateConfig.from_env()
    assert gate_normalization(WORD, ans(SAFE_GLOBAL, .84, .14, .02, .76), g)[0] == SAFE_GLOBAL


# -- the lexicon ----------------------------------------------------------------------------

def test_lexicon_global_contextual_and_rejected(contract):
    cands = [NormalizationCandidate("neuro", "neurological", ("prefix",), ("specialty",)),
             NormalizationCandidate("endo", "endocrine", ("prefix",), ("specialty",)),
             NormalizationCandidate("endo", "endoscopic", ("prefix",), ("concept",)),
             NormalizationCandidate("gi", "gastrointestinal", ("subsequence",), ("specialty",)),
             NormalizationCandidate("gi", "geriatric", ("subsequence",), ("specialty",)),
             NormalizationCandidate("bd", "bedside", ("subsequence",), ("qualifier",))]
    decisions = {"neuro->neurological": SAFE_GLOBAL, "endo->endocrine": CONTEXT_REQUIRED,
                 "endo->endoscopic": CONTEXT_REQUIRED, "gi->gastrointestinal": SAFE_GLOBAL,
                 "gi->geriatric": SAFE_GLOBAL, "bd->bedside": UNSAFE}
    lex = build_lexicon(decisions, cands)
    assert lex.global_map == {"neuro": ("neurological",)}
    # Two readings each judged safe still cannot both be global: contextual.
    assert lex.contextual == {"endo": (("endocrine",), ("endoscopic",)),
                              "gi": (("gastrointestinal",), ("geriatric",))}
    assert lex.readings("bd", contract.vocabulary) is None
    assert lex.readings("metabolic", contract.vocabulary) == (("metabolic",),)
    assert build_lexicon(decisions, cands, include_contextual=False).contextual == {}


def test_exact_only_lexicon_reads_contract_words_only(contract):
    lex = Lexicon.exact_only()
    assert lex.readings("neuro", contract.vocabulary) is None
    assert lex.readings("neurological", contract.vocabulary) == (("neurological",),)
