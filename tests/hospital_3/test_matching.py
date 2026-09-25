"""Hospital 3 service identity: the deterministic lexicon, structural matching,
the unit-basis tie-break, and the absence of any price or date input."""

import inspect
import re
from pathlib import Path

import pytest

from src.hospital_3 import matcher as M
from src.hospital_3 import normalization as N
from src.hospital_3.matcher import AMBIGUOUS, MATCHED, UNKNOWN, MissingWordDecision, resolve_line_identity
from src.hospital_3.normalization import (
    CLINICAL_ACRONYMS, CONTEXTUAL, GLOBAL, Lexicon, acronym_evidence, build_lexicon, normalize_description,
    word_rules,
)
from tests.hospital_3.conftest import ADDED_DAY, ADDED_PROC

SRC = Path(__file__).resolve().parents[2] / "src" / "hospital_3"


def ident(matcher, description, basis="per_procedure", decision=None):
    return resolve_line_identity(matcher.match(description), basis, decision)


# -- normalisation --------------------------------------------------------------------------------

def test_normalisation_strips_reference_case_and_punctuation():
    assert normalize_description("procedure - spclst pulmonary /RM-4002") == "procedure spclst pulmonary"
    assert normalize_description("  INTENS  -  ent anaes admin ") == "intens ent anaes admin"


def test_letter_rules():
    assert word_rules("pulm", "pulmonary") == ("prefix",)
    assert word_rules("thtr", "theatre") == ("subsequence",)
    assert word_rules("paed", "paediatric") == ("prefix",)
    assert "spelling_variant" in word_rules("pediatric", "paediatric")
    assert word_rules("ent", "otolaryngologic") == ("clinical_acronym",)
    assert word_rules("ent", "oncology") == ()


def test_every_reading_is_a_hospital_3_contract_word_reached_by_a_rule(contract, lexicon):
    for token, words in list(lexicon.global_map.items()) + [(t, w) for t, ws in lexicon.contextual.items() for w in ws]:
        assert words in contract.vocabulary, (token, words)
        assert word_rules(token, words), (token, words)


def test_global_tokens_are_unique_long_and_slot_consistent(lexicon):
    for token, word in lexicon.global_map.items():
        e = lexicon.evidence[token]
        assert e.classification == GLOBAL and list(e.readings) == [word]
        assert len(token) >= N.MIN_GLOBAL_LENGTH and token not in CLINICAL_ACRONYMS
        assert not e.slot_conflicts


def test_short_ambiguous_and_acronym_tokens_are_contextual(lexicon):
    for token in ("cr", "bd", "wd", "gi", "rm", "tm", "hm", "cs", "endo", "inf", "img", "onc", "rtn", "ent"):
        assert token in lexicon.contextual and token not in lexicon.global_map, token
    assert lexicon.evidence["ent"].classification == CONTEXTUAL
    assert lexicon.contextual["ent"] == ("otolaryngologic",)


def test_the_lexicon_is_deterministic(contract, occurrences, lexicon):
    again = build_lexicon(contract, reversed([li.description for o in occurrences for li in o.line_items]))
    assert again.global_map == lexicon.global_map and again.contextual == lexicon.contextual


def test_no_other_hospitals_table_is_imported():
    for path in SRC.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"hospital_[1245]", text.split('"""', 2)[-1]), path.name


def test_ent_is_a_human_reviewed_contextual_reading_never_a_global_alias(lexicon):
    from src.hospital_3.normalization import ACRONYM_REVIEWS

    review = ACRONYM_REVIEWS["ent"]
    assert (review["status"], review["global"], review["scope"]) == ("human_reviewed", False, "hospital_3_contextual")
    assert set(ACRONYM_REVIEWS) == set(CLINICAL_ACRONYMS)
    assert "ent" not in lexicon.global_map and lexicon.contextual["ent"] == ("otolaryngologic",)


def test_ent_corpus_evidence(contract, occurrences, lexicon):
    ev = acronym_evidence(contract, [li.description for o in occurrences for li in o.line_items], lexicon)["ent"]
    assert ev["with_another_specialty_word"] == []
    assert ev["descriptions_fitting_a_contracted_service_of_the_specialty"] == ev["distinct_descriptions"] == 15
    assert ev["descriptions_also_seen_with_the_specialty_spelled_out"] >= 7


# -- structural matching ---------------------------------------------------------------------------

def test_exact_contract_name(matcher):
    i = ident(matcher, "Specialist Pulmonary Biopsy Procedure")
    assert (i.status, i.service, i.method) == (MATCHED, "Specialist Pulmonary Biopsy Procedure", "text")


def test_abbreviated_and_reordered(matcher):
    assert ident(matcher, "procedure - spclst pulmonary /RM-4002").service == "Specialist Pulmonary Biopsy Procedure"
    assert ident(matcher, "sess - foc neurological physio").service == "Focused Neurological Physiotherapy Session"


def test_contextual_tokens_resolved_by_structure(matcher):
    i = ident(matcher, "cr ent interm wnd", "per_visit")
    assert (i.status, i.service, i.method) == (MATCHED, "Intermittent Otolaryngologic Wound Care", "text_contextual")
    assert matcher.match("cr ent interm wnd").contextual["cr"]["surviving"] == ["care"]
    i = ident(matcher, "postop card wd bd occ", "per_day")
    assert i.service == "Postoperative Cardiac Ward Bed Occupancy"


def test_ent_is_read_only_where_an_otolaryngologic_service_fits(matcher):
    m = matcher.match("ent consult")                       # no Otolaryngologic Consultation is contracted
    assert m.contextual["ent"]["surviving"] == [] and "ent" in m.unread_tokens
    assert m.status != MATCHED
    assert ident(matcher, "intens ent anaes admin").service == "Intensive Otolaryngologic Anaesthesia Administration"


def test_missing_discriminator_is_ambiguous_and_open_world(matcher):
    i = ident(matcher, "ext endoscopic proc")
    assert (i.status, i.method, i.candidates) == (AMBIGUOUS, "missing_discriminator",
                                                  ("Extended Musculoskeletal Endoscopic Procedure",))
    assert i.open_world


def test_unit_basis_breaks_only_a_genuine_tie(matcher):
    assert ident(matcher, "adv nutr supp", "per_unit_dispensed").service == "Advanced Geriatric Nutritional Support"
    tb = ident(matcher, "adv nutr supp", "per_day")
    assert (tb.service, tb.method, tb.used_unit_basis_for_identity) == (ADDED_DAY, "unit_basis_tiebreak", True)
    assert ident(matcher, "adv nutr supp", "per_hour").status == AMBIGUOUS
    # A missing-word description with the "right" basis is still not identified by it.
    i = ident(matcher, "ext endoscopic proc", "per_procedure")
    assert i.status == AMBIGUOUS and not i.used_unit_basis_for_identity


def test_unknown_is_a_contradiction_not_a_guess(matcher):
    i = ident(matcher, "elective physiotherapy session vascular", "per_night")
    assert (i.status, i.method, i.service) == (UNKNOWN, "contradiction", None)
    # ...and the unit basis cannot rescue it.
    assert ident(matcher, "elective physiotherapy session vascular", "per_hour").status == UNKNOWN


def test_added_services_are_identified_from_text_alone(matcher):
    assert ident(matcher, "elect - pulm img interpretation").service == ADDED_PROC
    assert ident(matcher, "adv derm nutr supp", "per_day").service == ADDED_DAY


def test_reviewed_decisions(matcher):
    m = matcher.match("ext endoscopic proc")
    svc = "Extended Musculoskeletal Endoscopic Procedure"
    i = resolve_line_identity(m, "per_procedure", MissingWordDecision("service", svc))
    assert (i.status, i.service, i.method, i.open_world) == (MATCHED, svc, "jev_missing_word", False)
    assert not i.used_unit_basis_for_identity           # one candidate: the basis separated nothing
    with pytest.raises(ValueError):
        resolve_line_identity(m, "per_procedure", MissingWordDecision("service", "Standard Orthopaedic Theatre Time"))
    tie = matcher.match("inpt thtr tm")
    closed = resolve_line_identity(tie, "per_hour", MissingWordDecision("ambiguous_contracted"))
    assert (closed.status, closed.method, closed.open_world) == (AMBIGUOUS, "jev_ambiguous_contracted", False)
    assert resolve_line_identity(tie, "per_hour", MissingWordDecision("unresolved")).open_world


def test_exact_only_baseline_reads_contract_words_only(contract):
    m = M.H3Matcher(contract, Lexicon.exact_only())
    assert m.match("spclst pulm biop proc").status == UNKNOWN
    assert m.match("Specialist Pulmonary Biopsy Procedure").status == MATCHED


# -- no price, total or date ever reaches identity ------------------------------------------------------

def test_identity_signatures_take_no_money_or_date():
    for fn in (M.H3Matcher.match, M.resolve_line_identity, M.unit_basis_tiebreak, N.build_lexicon,
               N.normalize_description):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"price", "unit_price_cents", "line_total_cents", "invoice_total_cents", "total",
                             "date", "service_date", "invoice_date", "patient_id"}, fn.__name__


def test_identity_modules_never_read_financial_fields():
    for name in ("matcher.py", "normalization.py"):
        code = (SRC / name).read_text(encoding="utf-8")
        for field in ("unit_price_cents", "line_total_cents", "invoice_total_cents", "rate_cents", "service_date",
                      "invoice_date"):
            assert field not in code, (name, field)


def test_resolve_lines_ignores_date_and_price(contract, matcher):
    from src.hospital_3.audit import resolve_lines
    from tests.hospital_3.conftest import invoice, line

    a = invoice("INV-A", [line("A-1", "interp - elect img", 1, 1, date="2024-03-01", unit_basis="per_procedure")])
    b = invoice("INV-B", [line("B-1", "interp - elect img", 9, 999999, date="2025-03-01", unit_basis="per_procedure")])
    ra, rb = resolve_lines([a], matcher)[0], resolve_lines([b], matcher)[0]
    assert ra.identity == rb.identity
