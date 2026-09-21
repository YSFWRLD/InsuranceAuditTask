"""The service matcher: normalisation, abbreviations, and the three outcomes."""

from __future__ import annotations

import pytest

from tests.hospital_1.conftest import audit, categories, invoice, line
from src.hospital_1.matcher import ServiceMatcher, normalize_description, tokenize
from src.shared.models import MatchStatus, UnitBasis


# -- normalisation ---------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Routine Hepatic Infusion Therapy", "routine hepatic infusion therapy"),
        ("ROUTINE  HEPATIC   INFUSION THERAPY", "routine hepatic infusion therapy"),
        ("Routine - Hepatic Infusion Therapy", "routine hepatic infusion therapy"),
        ("Routine Hepatic Infusion Therapy /NG-3022", "routine hepatic infusion therapy"),
        ("Routine Hepatic Infusion Therapy /AB-7", "routine hepatic infusion therapy"),
        ("Routine/Hepatic,Infusion.Therapy", "routine hepatic infusion therapy"),
    ],
)
def test_normalisation_strips_noise(raw, expected):
    assert normalize_description(raw) == expected


def test_reference_suffix_only_stripped_at_the_end():
    """A reference number mid-string is still punctuation, not a service word."""
    assert "ng" not in tokenize("Routine Hepatic Infusion Therapy /NG-3022")


# -- the three outcomes ----------------------------------------------------

def test_exact_name_matches(matcher):
    m = matcher.match("Routine Hepatic Infusion Therapy")
    assert m.status is MatchStatus.MATCHED
    assert m.service == "Routine Hepatic Infusion Therapy"
    assert m.match_method == "text_margin"


def test_reordered_and_abbreviated_description_matches(matcher):
    """Hospital descriptions move the head noun to the front and drop vowels."""
    m = matcher.match("Therapy Rtn Hepatic Infus /NG-1234")
    assert m.status is MatchStatus.MATCHED
    assert m.service == "Routine Hepatic Infusion Therapy"


def test_a_description_naming_no_contracted_service_is_unknown(matcher):
    m = matcher.match("Interpretive Dance Session")
    assert m.status is MatchStatus.UNKNOWN
    assert m.service is None


def test_a_contract_word_the_candidate_cannot_explain_blocks_the_match(matcher):
    """"Advanced Renal Infusion Therapy" is not "Advanced Hepatic ..." with a typo.

    Four of five words agree, but "renal" is contract vocabulary pointing
    elsewhere.  A near-miss on a specialty word must not be resolved silently:
    an invented service billed at a real service's rate is exactly the error
    this system exists to catch.
    """
    m = matcher.match("Advanced Renal Infusion Therapy")
    assert m.status is MatchStatus.UNKNOWN


def test_a_description_short_of_a_discriminator_is_ambiguous(matcher):
    """Two contracted services, nothing in the text to choose between them."""
    m = matcher.match("Review Paediatric Imaging", None)
    assert m.status is MatchStatus.AMBIGUOUS
    assert m.service is None
    assert len(m.candidate_services) >= 2


# -- the unit-basis tie-break ----------------------------------------------

def test_unit_basis_breaks_a_genuine_tie_and_is_recorded(matcher):
    m = matcher.match("Review Paediatric Imaging", UnitBasis.VISIT)
    assert m.status is MatchStatus.MATCHED
    assert m.service == "Beta Paediatric Imaging Review"
    assert m.match_method == "unit_basis_tiebreak"
    assert m.used_unit_basis_for_matching is True


def test_unit_basis_tiebreak_does_not_then_report_a_wrong_unit_basis(auditor, matcher):
    """Evidence must not be counted twice with opposite signs.

    If the billed basis is what identified the service, the audit cannot then
    turn round and call that basis wrong.
    """
    occ = invoice(
        "INV-M-1",
        [
            line(
                "L-M1", "INV-M-1", 1, "Review Paediatric Imaging", 1, 70000,
                unit_basis=UnitBasis.VISIT,
            )
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-M-1"]
    assert "wrong_unit_basis" not in categories(result)


def test_a_wrong_unit_basis_on_an_unambiguous_service_is_reported(auditor, matcher):
    occ = invoice(
        "INV-M-2",
        [
            line(
                "L-M2", "INV-M-2", 1, "Routine Hepatic Infusion Therapy", 2, 10000,
                unit_basis=UnitBasis.VISIT,     # the contract says per hour
            )
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-M-2"]
    assert categories(result) == {"wrong_unit_basis"}
    # The basis is wrong; the money is not.
    assert result.expected_total_cents == 20000


def test_an_unrecognised_unit_basis_token_is_reported(auditor, matcher):
    occ = invoice(
        "INV-M-3",
        [
            line(
                "L-M3", "INV-M-3", 1, "Routine Hepatic Infusion Therapy", 1, 10000,
                unit_basis="per_fortnight",
            )
        ],
    )
    assert "wrong_unit_basis" in categories(audit(auditor, matcher, [occ])["INV-M-3"])


# -- the price must never identify the service ------------------------------

def test_the_billed_price_does_not_influence_the_match(matcher):
    """Matching is a function of text and (as a tie-break) unit basis only.

    The same description resolves the same way whatever price is attached,
    including a price that exactly equals some other service's rate.
    """
    base = matcher.match("Review Paediatric Imaging", UnitBasis.TEST)
    assert base.service == "Alpha Paediatric Imaging Review"
    # There is no price argument to pass -- which is the point.  Assert the
    # signature stays that way so a future change has to be deliberate.
    import inspect

    params = set(inspect.signature(ServiceMatcher.match).parameters)
    assert params == {"self", "description", "billed_unit_basis"}


def test_an_unknown_service_line_is_flagged_and_not_priced(auditor, matcher):
    occ = invoice(
        "INV-M-4",
        [
            line("L-M4-1", "INV-M-4", 1, "Routine Hepatic Infusion Therapy", 1, 10000),
            line("L-M4-2", "INV-M-4", 2, "Interpretive Dance Session", 1, 55555),
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-M-4"]
    assert "unknown_service" in categories(result)
    assert result.correction_reconstructable is False
    assert result.confidence_band == "low"


def test_an_ambiguous_line_priced_consistently_is_not_flagged(auditor, matcher):
    """Right under *some* reading is not evidence of an error.

    We still do not claim to know which service it is.
    """
    occ = invoice(
        "INV-M-5",
        [
            line(
                "L-M5", "INV-M-5", 1, "Review Paediatric Imaging", 1, 50000,
                unit_basis="per_fortnight",   # no basis support, so no tie-break
            )
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-M-5"]
    assert "unit_price_mismatch" not in categories(result)


def test_an_ambiguous_line_wrong_under_every_reading_is_flagged(auditor, matcher):
    occ = invoice(
        "INV-M-6",
        [
            line(
                "L-M6", "INV-M-6", 1, "Review Paediatric Imaging", 1, 12345,
                unit_basis="per_fortnight",
            )
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-M-6"]
    assert "unit_price_mismatch" in categories(result)
    assert result.expected_total_cents is None
