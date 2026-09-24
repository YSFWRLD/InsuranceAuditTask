"""Hospital 5 contract parser: every table, every clause the engine relies on,
and loud refusal of anything it does not understand."""

from decimal import Decimal

import pytest

from src.hospital_5.contract import (
    ContractParseError, cross_check_text_rendering, parse_contract, rule_counts, write_contract_rules,
)
from tests.hospital_5.conftest import (
    BUNDLE_A, BUNDLE_B, CAPPED, EXCLUDED, ONE_TIER, PLAIN, PREMIUM, TRIGGER, TWO_TIER, WEEKEND, patched_contract,
)


def test_metadata(contract):
    assert contract.contract_number == "INS-H5-2024-0731"
    assert contract.provider == "Pelham Health Network"
    assert contract.payer == "Meridian Health Assurance Group"
    assert (contract.effective_from, contract.effective_to) == ("2024-01-01", "2025-12-31")
    assert contract.currency == "GBP" and contract.rounding_convention == "half_up_cent"
    assert contract.pricing_order == ("bundle", "facility", "plan_tier", "premium", "cumulative_discount")


def test_facilities_tiers_and_business_days(contract):
    assert list(contract.facilities) == ["F-MAIN", "F-NORTH", "F-COAST"]
    assert contract.facilities["F-COAST"] == "Coastal Satellite Unit"
    assert contract.plan_tiers == ("BRONZE", "SILVER", "GOLD")
    assert contract.non_business_weekdays == (5, 6)       # clause 2.2: Saturday and Sunday


def test_rule_counts(contract):
    assert rule_counts(contract) == {
        "services": 84, "facilities": 3, "plan_tiers": 3, "facility_multiplier_cells": 252,
        "plan_tier_multiplier_cells": 252, "daily_caps": 9, "threshold_premiums": 10,
        "non_business_day_uplifts": 9, "bundle_pairs": 3, "cumulative_discount_services": 9,
        "cumulative_discount_tiers": 15, "exclusion_windows": 7, "compound_unit_bases": 1,
    }


def test_table_1_rows(contract):
    s = contract.services[PLAIN]
    assert (s.qualifier, s.specialty, s.concept) == ("assisted", "renal", "imaging interpretation")
    assert (s.base_rate_cents, s.unit_basis) == (161650, "per_procedure")
    assert contract.services["Ambulatory Paediatric Isolation Room Occupancy"].base_rate_cents == 126525
    compound = contract.services["Routine Psychiatric Telemetry Monitoring"]
    assert compound.unit_basis == "per_hour_per_item" and compound.compound_unit_basis


def test_every_service_has_a_full_multiplier_row(contract):
    for name in contract.services:
        assert set(contract.facility_multipliers[name]) == {"F-MAIN", "F-NORTH", "F-COAST"}
        assert set(contract.tier_multipliers[name]) == {"BRONZE", "SILVER", "GOLD"}


def test_matrices_by_column(contract):
    assert contract.facility_multipliers[PLAIN] == {"F-MAIN": Decimal("1"), "F-NORTH": Decimal("1.2"),
                                                    "F-COAST": Decimal("0.92")}
    assert contract.tier_multipliers[PLAIN] == {"BRONZE": Decimal("1.05"), "SILVER": Decimal("0.98"),
                                                "GOLD": Decimal("0.95")}
    assert contract.tier_multipliers[TWO_TIER]["GOLD"] == Decimal("0.92")


def test_daily_caps(contract):
    assert {n: c.max_units for n, c in contract.daily_caps.items()} == {
        "Advanced Cardiac Ventilation Support": 24, "Advanced Dermatologic Pharmaceutical Dispensing": 4,
        "Assisted Hepatic Physiotherapy Session": 12, "Comprehensive Oncology Transfusion Service": 8,
        CAPPED: 4, "Extended Dermatologic Transport Service": 4, "Intermittent Vascular Telemetry Monitoring": 8,
        "Preoperative Ophthalmic Transport Service": 6, "Routine Infectious Infusion Therapy": 24,
    }


def test_premiums_and_non_business_day_uplifts(contract):
    p = contract.threshold_premiums[PREMIUM]
    assert (p.exceeds_units, p.uplift_fraction) == (8, Decimal("0.3"))
    assert contract.threshold_premiums["Routine Geriatric Telemetry Monitoring"].exceeds_units == 16
    assert contract.non_business_day_uplifts[WEEKEND].uplift_fraction == Decimal("0.25")
    assert not set(contract.threshold_premiums) & set(contract.non_business_day_uplifts)


def test_bundles_substitute_both_rates(contract):
    assert contract.bundle_partner(BUNDLE_A) == (BUNDLE_B, 17500, "7.1")
    assert contract.bundle_partner(BUNDLE_B) == (BUNDLE_A, 3500, "7.1")
    assert contract.bundle_partner(PLAIN) is None
    assert len(contract.bundles) == 3


def test_discount_tiers_are_ordered_and_deepen(contract):
    tiers = contract.volume_discounts[TWO_TIER]
    assert [(t.exceeds_units, t.discount_fraction) for t in tiers] == [(100, Decimal("0.12")), (300, Decimal("0.3"))]
    assert [(t.exceeds_units, t.discount_fraction) for t in contract.volume_discounts[ONE_TIER]] == \
        [(80, Decimal("0.12"))]


def test_exclusions_are_directed(contract):
    (w,) = contract.exclusions_for(EXCLUDED)
    assert (w.days, w.other_service) == (10, TRIGGER)
    assert contract.exclusions_for(TRIGGER) == []       # the trigger is not itself excluded
    assert len(contract.exclusion_windows) == 7


def test_invoice_clauses(contract):
    assert "unique across the term" in contract.invoice_clauses["10.1"]
    assert "may not fall after the invoice date" in contract.invoice_clauses["10.2"]
    assert "whether on one invoice or across several" in contract.invoice_clauses["10.3"]


def test_rules_touching(contract):
    assert contract.rules_touching(PLAIN) == []
    assert contract.rules_touching(BUNDLE_A) == ["bundle"]
    assert contract.rules_touching(TWO_TIER) == ["cumulative_discount"]
    assert set(contract.rules_touching(EXCLUDED)) == {"exclusion"}


def test_plain_text_rendering_agrees(contract):
    assert cross_check_text_rendering(contract) == []


def test_contract_rules_artifact(contract, tmp_path):
    path = tmp_path / "rules.json"
    write_contract_rules(contract, path)
    text = path.read_text(encoding="utf-8")
    assert contract.fingerprint in text and '"text_rendering_disagreements": []' in text


# -- the parser refuses what it does not understand ------------------------------------------

@pytest.mark.parametrize("old,new,message", [
    ("**Rounding convention:** half_up_cent", "**Rounding convention:** bankers", "rounding"),
    ("**Currency:** GBP", "**Currency:** EUR", "currency"),
    ("| Service | F-MAIN | F-NORTH | F-COAST |", "| Service | F-NORTH | F-MAIN | F-COAST |", "header"),
    ("| Service | BRONZE | SILVER | GOLD |", "| Service | GOLD | SILVER | BRONZE |", "header"),
    ("| Supervised Vascular Diagnostic Imaging | 1 | 1 | 1 |\n\n### Table 3",
     "\n### Table 3", "no row for Table 1"),
    ("| Comprehensive Palliative Consultation | more than 8 visits | +30% |",
     "| Comprehensive Palliative Consultation | more than 8 hours | +30% |", "wrong unit"),
    ("| Routine Urologic Transport Service | 10 days | Inpatient Pulmonary Critical Care Occupancy |",
     "| Routine Urologic Transport Service | 10 days | Imaginary Pulmonary Service |", "unknown service"),
    ("| Supervised Otolaryngologic Specimen Analysis | more than 180 items | 20% |",
     "| Supervised Otolaryngologic Specimen Analysis | more than 180 items | 5% |", "monotonically"),
    ("(d) any premium or uplift; and (e) any cumulative volume discount",
     "(d) any cumulative volume discount; and (e) any premium or uplift", "pricing order"),
    ("Rounding is applied after each individual step of the calculation, not once at the end.",
     "Rounding is applied once at the end.", "rounding frequency"),
    ("but the rounding step is still taken", "and no rounding is needed", "3.4"),
    ("in ascending order of line identifier", "in any order", "line identifier"),
    ("| Emergency Oncology Imaging Interpretation | per procedure | GBP 4,412.50 | 4 procedures |",
     "| Emergency Oncology Imaging Interpretation | per procedure | GBP 4,412.50 | 4 hours |", "cap"),
    ("| Assisted Cardiac Ventilation Support | +20% |", "| Comprehensive Palliative Consultation | +20% |",
     "both a Section 5 premium and a Section 6 uplift"),
    ("| Extended Urologic Infusion Therapy | GBP 59.25 |", "| Emergency Metabolic Discharge Planning | GBP 59.25 |",
     "more than one bundle"),
])
def test_parser_refuses(tmp_path, contract_text, old, new, message):
    with pytest.raises(ContractParseError, match=message):
        patched_contract(tmp_path, contract_text, old, new)


def test_multiplier_must_be_plausible(tmp_path, contract_text):
    with pytest.raises(ContractParseError, match="multiplier"):
        patched_contract(tmp_path, contract_text, "| Advanced Cardiac Ventilation Support | 1 | 1.1 | 0.92 |",
                         "| Advanced Cardiac Ventilation Support | 1 | 11 | 0.92 |")


def test_fingerprint_changes_with_the_text(tmp_path, contract_text, contract):
    other = patched_contract(tmp_path, contract_text, "GBP 1,616.50", "GBP 1,616.75")
    assert other.fingerprint != contract.fingerprint
    assert other.services[PLAIN].base_rate_cents == 161675


def test_parse_is_deterministic():
    assert parse_contract().fingerprint == parse_contract().fingerprint
