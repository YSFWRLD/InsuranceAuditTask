"""Hospital 5 structural matching and line identity."""

import inspect

import pytest

from src.hospital_5 import workflow as W
from src.hospital_5.matcher import (
    AMBIGUOUS, MATCHED, UNKNOWN, H5Matcher, MissingWordDecision, resolve_line_identity, unit_basis_tiebreak,
)
from tests.hospital_5.conftest import SUPV_PALL, SUPV_VASC, TIE_HOUR, TIE_VISIT


def test_exact_contract_name(matcher):
    m = matcher.match("Assisted Renal Imaging Interpretation")
    assert (m.status, m.service, m.method) == (MATCHED, "Assisted Renal Imaging Interpretation", "text")


def test_word_order_and_reference_suffix_are_irrelevant(matcher):
    a = matcher.match("ASST REN IMG INTERP /PH-1234")
    b = matcher.match("interp img ren asst")
    assert a is b and a.status == MATCHED and a.service == "Assisted Renal Imaging Interpretation"


def test_abbreviations(matcher):
    m = matcher.match("EXT NEURO CONSULT")
    assert (m.status, m.service) == (MATCHED, "Extended Neurological Consultation")


def test_context_required_token_is_resolved_by_its_neighbours(matcher):
    m = matcher.match("EXT ENDO THTR TM")
    assert (m.status, m.service, m.method) == (MATCHED, "Extended Endocrine Theatre Time", "text_contextual")
    assert m.contextual["endo"] == {"alternatives": ["endocrine", "endoscopic"], "surviving": ["endocrine"]}


def test_context_required_token_other_reading(matcher):
    m = matcher.match("METAB ENDO PROC")
    # "metabolic endocrine" names two specialties: that reading is eliminated.
    assert m.contextual["endo"]["surviving"] == ["endoscopic"]
    assert m.status == AMBIGUOUS and m.method == "tied_candidates"
    assert m.candidate_services == ("Assisted Metabolic Endoscopic Procedure",
                                    "Continuous Metabolic Endoscopic Procedure")


def test_contextual_token_that_no_reading_fits_is_left_unread(matcher):
    m = matcher.match("NEURO ENDO CONSULT")
    assert m.status == AMBIGUOUS and m.method == "unrecognised_token" and m.unread_tokens == ("endo",)
    assert m.candidate_services == ("Extended Neurological Consultation",)


def test_missing_qualifier_ties_and_keeps_every_candidate(matcher):
    m = matcher.match("HEP CS CONF")
    assert m.status == AMBIGUOUS and m.method == "tied_candidates"
    assert m.candidate_services == (TIE_HOUR, TIE_VISIT)
    assert [c.missing_fields for c in m.candidates] == [("qualifier",), ("qualifier",)]


def test_missing_specialty(matcher):
    m = matcher.match("COMPR CONSULT")
    assert (m.status, m.method, m.candidate_services) == (
        AMBIGUOUS, "missing_discriminator", ("Comprehensive Palliative Consultation",))
    ident = resolve_line_identity(m, "per_visit")
    assert ident.status == AMBIGUOUS and ident.open_world      # an uncontracted service is not ruled out


def test_unknown_words(matcher):
    m = matcher.match("EXT NEURO CONSULT XYZ")
    assert m.status == AMBIGUOUS and m.method == "unrecognised_token" and m.unread_tokens == ("xyz",)
    assert m.candidate_services == ("Extended Neurological Consultation",)
    assert matcher.match("XYZ QRS").method == "no_recognised_words"


def test_contradiction_is_unknown(matcher):
    m = matcher.match("Emergency Palliative Sterilisation Service")
    assert (m.status, m.method, m.candidates) == (UNKNOWN, "contradiction", ())


def test_candidate_sets_are_never_truncated(matcher, contract):
    m = matcher.match("CONSULT")
    expected = tuple(sorted(n for n in contract.services if n.endswith("Consultation")))
    assert m.candidate_services == expected and len(expected) == 5


def test_identity_key_groups_equivalent_descriptions(matcher):
    assert matcher.match("EXT NEURO CONSULT").identity_key == matcher.match("Consultation Extended Neurological").identity_key


# -- the unit-basis tie-break ------------------------------------------------------------------

def test_unit_basis_breaks_a_genuine_tie_only(matcher):
    m = matcher.match("HEP CS CONF")
    hour = resolve_line_identity(m, "per_hour")
    assert (hour.status, hour.service, hour.method, hour.used_unit_basis_for_identity) == (
        MATCHED, TIE_HOUR, "unit_basis_tiebreak", True)
    assert hour.candidates == (TIE_HOUR, TIE_VISIT)             # the pre-tie-break set is kept
    assert resolve_line_identity(m, "per_visit").service == TIE_VISIT
    assert resolve_line_identity(m, "per_day").status == AMBIGUOUS
    # Not a tie: the basis may not choose a single missing-word candidate.
    assert unit_basis_tiebreak(matcher.match("COMPR CONSULT"), "per_visit") is None


def test_unit_basis_cannot_break_a_tie_it_does_not_separate(matcher):
    m = matcher.match("SUPV CONSULT")
    assert m.candidate_services == (SUPV_PALL, SUPV_VASC)
    assert resolve_line_identity(m, "per_procedure").status == AMBIGUOUS    # both per procedure


# -- reviewed decisions -------------------------------------------------------------------------

def test_reviewed_service_decision(matcher):
    m = matcher.match("COMPR CONSULT")
    ident = resolve_line_identity(m, "per_visit", MissingWordDecision("service", "Comprehensive Palliative Consultation"))
    assert (ident.status, ident.method, ident.open_world, ident.used_unit_basis_for_identity) == (
        MATCHED, "jev_missing_word", False, False)
    with pytest.raises(ValueError):
        resolve_line_identity(m, "per_visit", MissingWordDecision("service", SUPV_PALL))


def test_reviewed_closure_allows_the_tie_break(matcher):
    m = matcher.match("SUPV CONSULT")
    closed = MissingWordDecision("ambiguous_contracted")
    ident = resolve_line_identity(m, "per_procedure", closed)
    assert (ident.status, ident.method, ident.open_world) == (AMBIGUOUS, "jev_ambiguous_contracted", False)
    m2 = matcher.match("PALL CONSULT")      # Comprehensive (per visit) or Supervised (per procedure)
    assert m2.method == "tied_candidates"
    # A textual tie resolves by basis before any review is needed ...
    assert resolve_line_identity(m2, "per_visit").method == "unit_basis_tiebreak"
    # ... and after a review closed an unread-token cluster, the same narrow rule applies.
    m3 = matcher.match("PALL CONSULT XYZ")
    after = resolve_line_identity(m3, "per_visit", closed)
    assert (after.status, after.service, after.method, after.used_unit_basis_for_identity) == (
        MATCHED, "Comprehensive Palliative Consultation", "unit_basis_tiebreak_after_review", True)


def test_reviewed_unknown(matcher):
    ident = resolve_line_identity(matcher.match("COMPR CONSULT"), "per_visit", MissingWordDecision("unknown"))
    assert (ident.status, ident.method) == (UNKNOWN, "jev_none_of_the_above")


def test_basis_seen_by_the_reviewer_counts_as_used_when_it_separates(matcher):
    m = matcher.match("PALL CONSULT XYZ")
    ident = resolve_line_identity(m, "per_visit", MissingWordDecision("service", "Comprehensive Palliative Consultation"))
    assert ident.used_unit_basis_for_identity        # the billed basis agrees with it and not with the alternative


def test_identity_never_takes_money_or_ids():
    assert list(inspect.signature(H5Matcher.match).parameters) == ["self", "description"]
    assert list(inspect.signature(resolve_line_identity).parameters) == ["match", "billed_unit_basis", "decision"]
    assert list(inspect.signature(unit_basis_tiebreak).parameters) == ["match", "billed_unit_basis"]


# -- the committed, reviewed lexicon ------------------------------------------------------------

@pytest.fixture(scope="module")
def reviewed(contract):
    sem = W.load_semantics(strict=True)
    return H5Matcher(contract, sem.lexicon)


@pytest.mark.parametrize("description,service", [
    ("EXT ENDO THTR TM", "Extended Endocrine Theatre Time"),
    ("FOC  ENDO THTR TIME", "Focused Endocrine Theatre Time"),
    ("PREOP ENDO REHAB PROGRAMME", "Preoperative Endocrine Rehabilitation Programme"),
    ("ASST METAB ENDOSC PROC", "Assisted Metabolic Endoscopic Procedure"),
    ("RTN INFECT INF THER", "Routine Infectious Infusion Therapy"),
    ("CR INPATIENT PULM CRIT", "Inpatient Pulmonary Critical Care Occupancy"),
    ("OBS  DERM NURSING /PH-3348", None),
])
def test_reviewed_lexicon_examples(reviewed, description, service):
    m = reviewed.match(description)
    if service is None:
        assert m.status == AMBIGUOUS and m.method == "tied_candidates"   # inpatient or specialist?
    else:
        assert (m.status, m.service) == (MATCHED, service)


def test_ent_is_a_reviewed_contextual_reading_not_a_global_one(reviewed):
    lex = reviewed.lexicon
    assert "ent" not in lex.global_map
    assert lex.contextual["ent"] == (("otolaryngologic",),)


@pytest.mark.parametrize("description", ["SUPV ENT SPCM ANLY", "SUPV  ENT SPCM ANALYSIS /PH-4343",
                                         "SUPV ENT SPECIMEN ANLY", "ANALYSIS SUPV ENT SPCM"])
def test_ent_in_the_specialty_slot_resolves(reviewed, description):
    m = reviewed.match(description)
    assert m.contextual["ent"] == {"alternatives": ["otolaryngologic"], "surviving": ["otolaryngologic"]}
    assert (m.status, m.service, m.method) == (MATCHED, "Supervised Otolaryngologic Specimen Analysis",
                                               "text_contextual")
    assert m.unread_tokens == ()


@pytest.mark.parametrize("description", ["ENT CONSULT", "EXT ENT TRANSP SVC", "SUPV ENT CONSULT"])
def test_ent_does_not_resolve_where_no_contracted_service_fits(reviewed, description):
    """No contracted Otolaryngologic service fits these words, so ENT stays
    unread: it neither resolves nor turns the description into a contradiction."""
    m = reviewed.match(description)
    assert m.status == AMBIGUOUS and "ent" in m.unread_tokens and m.contextual["ent"]["surviving"] == []
    assert all(c.specialty != "otolaryngologic" for c in m.candidates)


def test_the_other_three_descriptions_stay_unresolved(reviewed):
    for description in ("SUPERVISED SPCM ANLY /PH-7244", "UROL HM VST /PH-1437", "RTN  PHYSIOTHERAPY SESS"):
        m = reviewed.match(description)
        assert (m.status, m.method) == (AMBIGUOUS, "missing_discriminator"), description
        assert len(m.candidates) == 1 and resolve_line_identity(m, m.candidates[0].unit_basis).open_world


def test_a_single_reading_contextual_token_is_never_a_contradiction(contract):
    """Regression (h5-matcher-2): a context-required token with one accepted
    reading is left unread where the reading fits no service."""
    from src.hospital_5.normalization import Lexicon
    lex = Lexicon("t", global_map={"consult": ("consultation",)}, contextual={"ent": (("otolaryngologic",),)})
    m = H5Matcher(contract, lex).match("ENT CONSULT")
    assert m.status == AMBIGUOUS and m.unread_tokens == ("ent",)
    as_global = H5Matcher(contract, Lexicon("g", global_map={"consult": ("consultation",),
                                                             "ent": ("otolaryngologic",)})).match("ENT CONSULT")
    assert as_global.status == UNKNOWN                      # what a global mapping would have forced


def test_reviewed_lexicon_leaves_rejected_tokens_unread(reviewed):
    bd = reviewed.match("ELECT INFECT WARD BD OCC")
    assert bd.status == UNKNOWN and "bd" in bd.unread_tokens            # bd -> bedside is overridden
    assert reviewed.match("INPT IMMUN RECOV RM OCC").service == "Inpatient Immunologic Recovery Room Occupancy"
