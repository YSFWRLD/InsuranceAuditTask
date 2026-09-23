"""Hospital 4 contract parser: rules come from the text, and a bad text fails loudly."""

import json
from decimal import Decimal

import pytest

from src.hospital_4.contract import (
    ContractParseError, parse_contract, rule_counts, words_to_int, write_contract_rules,
)
from tests.hospital_4.conftest import (
    BUNDLE_A, BUNDLE_B, CAPPED, COMPOUND, DISCOUNT, EXCLUDED, PREMIUM, TRIGGER, TWO_TIER, patched_contract,
)


def test_identity_and_term(contract):
    assert contract.contract_number == "INS-H4-2024-2049"
    assert (contract.effective_from, contract.effective_to) == ("2024-01-01", "2025-12-31")
    assert contract.currency == "GBP"
    assert contract.rounding_convention == "half_up_cent"
    assert contract.provider == "Calderwood University Teaching Hospital"
    assert contract.facility_code == "F-MAIN"
    assert contract.facility_multiplier == contract.plan_tier_multiplier == Decimal(1)


def test_rule_family_counts(contract):
    # Cross-checks only: the parser derives these from the text.
    assert rule_counts(contract) == {
        "services": 98,
        "threshold_premiums": 18,
        "daily_caps": 18,
        "bundle_pairs": 7,
        "cumulative_discount_services": 3,
        "cumulative_discount_tiers": 4,
        "exclusion_windows": 15,
        "non_business_day_uplifts": 0,
        "compound_unit_bases": 1,
    }


def test_no_weekend_uplift(contract):
    assert contract.non_business_day_uplifts == {}


def test_pricing_order_is_parsed_from_clause_4_1(contract):
    assert contract.pricing_order == ("bundle", "facility", "plan_tier", "premium", "cumulative_discount")


def test_compound_unit_basis_is_kept_whole(contract):
    svc = contract.services[COMPOUND]
    assert svc.unit_basis == "per_hour_per_item" and svc.compound_unit_basis
    assert sum(s.compound_unit_basis for s in contract.services.values()) == 1


def test_rates_and_rules_read_exactly(contract):
    assert contract.services["Advanced Vascular Endoscopic Procedure"].base_rate_cents == 321775
    assert contract.threshold_premiums[PREMIUM].exceeds_units == 6
    assert contract.threshold_premiums[PREMIUM].uplift_fraction == Decimal("0.15")
    assert contract.daily_caps[CAPPED].max_units == 8
    tiers = contract.volume_discounts[TWO_TIER]
    assert [(t.exceeds_units, t.discount_fraction) for t in tiers] == [(80, Decimal("0.15")), (240, Decimal("0.3"))]
    assert contract.volume_discounts[DISCOUNT][0].exceeds_units == 120
    assert contract.bundle_partner(BUNDLE_A) == (BUNDLE_B, 11875, "7.1")
    assert contract.bundle_partner(BUNDLE_B) == (BUNDLE_A, 4900, "7.1")
    (w,) = contract.exclusions_for(EXCLUDED)
    assert (w.days, w.other_service) == (21, TRIGGER)


def test_every_rule_references_a_known_service(contract):
    names = set(contract.services)
    assert set(contract.threshold_premiums) <= names
    assert set(contract.daily_caps) <= names
    assert set(contract.volume_discounts) <= names
    assert {s for b in contract.bundles for s in (b.service_a, b.service_b)} <= names
    assert {s for e in contract.exclusion_windows for s in (e.service, e.other_service)} <= names


def test_service_names_have_three_disjoint_slots(contract):
    q = {s.qualifier for s in contract.services.values()}
    sp = {s.specialty for s in contract.services.values()}
    cw = {w for s in contract.services.values() for w in s.concept.split()}
    assert not (q & sp) and not (q & cw) and not (sp & cw)


def test_provenance_is_recorded(contract):
    assert contract.services[PREMIUM].source_text.startswith(f"| {PREMIUM} |")
    assert contract.threshold_premiums[PREMIUM].clause_id == "5.1"
    assert len(contract.fingerprint) == 64 and len(contract.source_sha256) == 64


def test_words_to_int():
    assert words_to_int("two hundred and forty") == 240
    assert words_to_int("one hundred and twenty") == 120
    assert words_to_int("eighty") == 80
    with pytest.raises(ContractParseError):
        words_to_int("eighty-ish")


def test_contract_rules_json_is_deterministic(contract, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_contract_rules(contract, a)
    write_contract_rules(parse_contract(), b)
    assert a.read_bytes() == b.read_bytes()
    doc = json.loads(a.read_text(encoding="utf-8"))
    assert doc["counts"]["services"] == 98 and doc["fingerprint"] == contract.fingerprint


# -- failure modes ------------------------------------------------------------------

@pytest.mark.parametrize("old,new,message", [
    ("**Rounding convention:** half_up_cent", "**Rounding convention:** bankers_even", "unsupported rounding"),
    ("| Routine Cardiac Home Visit | more than 10 visits | +25% |",
     "| Routine Kardiac Home Visit | more than 10 visits | +25% |", "unknown service"),
    ("| Routine Cardiac Home Visit | more than 10 visits | +25% |",
     "| Routine Cardiac Home Visit | more than 10 visits | +25 per cent |", "percentage"),
    ("| Routine Cardiac Home Visit | per visit | GBP 162.00 |",
     "| Routine Cardiac Home Visit |  | GBP 162.00 |", "unit basis"),
    ("| Routine Cardiac Home Visit | per visit | GBP 162.00 |",
     "| Routine Cardiac Home Visit | per fortnight | GBP 162.00 |", "unknown unit basis"),
    ("| Routine Cardiac Home Visit | per visit | GBP 162.00 |",
     "| Routine Cardiac Home Visit | per visit | GBP 162.00 |\n| Routine Cardiac Home Visit | per visit | GBP 1.00 |",
     "duplicate service"),
    ("| Standard Oncology Ward Bed Occupancy | one hundred and twenty (120) | ten percent (10%) |",
     "| Standard Oncology Ward Bed Occupancy | one hundred and twenty (121) | ten percent (10%) |",
     "in words"),
    ("| Advanced Orthopaedic Ward Bed Occupancy | 12 nights |",
     "| Advanced Orthopaedic Ward Bed Occupancy | 12 hours |", "wrong unit"),
    ("| Advanced Orthopaedic Ward Bed Occupancy | 12 nights |",
     "| Advanced Orthopaedic Ward Bed Occupancy | 12 nights | extra |", "cells"),
    ("Rounding is applied after each individual step of the calculation, not once at the end.",
     "Rounding is applied once at the end.", "rounding frequency"),
    ("(d) any premium or uplift; and (e) any cumulative volume discount",
     "(d) any cumulative volume discount; and (e) any premium or uplift", "pricing order"),
    ("no facility differential arises under this Agreement", "a facility differential of 5% arises",
     "facility"),
    ("_None._", "", "no table rows"),
    ("| Ambulatory Musculoskeletal Ventilation Support | two hundred and forty (240) | thirty percent (30%) |",
     "| Ambulatory Musculoskeletal Ventilation Support | two hundred and forty (240) | ten percent (10%) |",
     "monotonically deeper"),
    ("| Specialist Immunologic Consultation | GBP 916.00 | Supervised Rheumatologic Dialysis Session | GBP 139.00 |",
     "| Specialist Immunologic Consultation | GBP 916.00 | Focused Vascular Infusion Therapy | GBP 139.00 |",
     "more than one bundle"),
])
def test_malformed_contracts_fail_loudly(tmp_path, contract_text, old, new, message):
    with pytest.raises(ContractParseError, match=message):
        patched_contract(tmp_path, contract_text, old, new)


def test_missing_section_fails(tmp_path, contract_text):
    with pytest.raises(ContractParseError, match="section not found"):
        patched_contract(tmp_path, contract_text, "## 6. Daily Quantity Limits", "## 6. Limits")
