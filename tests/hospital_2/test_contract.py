"""Hospital 2 contract extraction: counts, rates, bases, provenance, failure."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.hospital_2.contract import (
    CONTRACT_PATH,
    ContractParseError,
    parse_contract,
    rule_counts,
    words_to_int,
    write_contract_rules,
)

ROOT = Path(__file__).resolve().parents[2]


def _mutated(tmp_path, old: str, new: str) -> Path:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    assert old in text, old
    path = tmp_path / "msa.md"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


# -- metadata and counts -------------------------------------------------------

def test_metadata(contract):
    assert contract.contract_number == "INS-H2-2024-1183"
    assert contract.provider == "St. Auben Metropolitan Hospital Trust"
    assert (contract.effective_from, contract.effective_to) == ("2024-01-01", "2025-12-31")
    assert contract.currency == "GBP"
    assert contract.rounding_convention == "half_up_cent"
    assert contract.facility_code == "F-MAIN"
    assert contract.facility_multiplier == contract.plan_tier_multiplier == Decimal(1)
    assert contract.invoice_rules.submission_days_after_discharge == 60


def test_rule_family_counts(contract):
    assert rule_counts(contract) == {
        "services": 76,
        "non_business_day_uplifts": 8,
        "daily_aggregate_uplifts": 9,
        "cumulative_discount_services": 8,
        "cumulative_discount_tiers": 12,
        "daily_caps": 8,
        "exclusion_windows": 6,
        "bundle_pairs": 3,
        "compound_unit_bases": 1,
    }
    assert contract.warnings == []


def test_rates_are_integer_cents(contract):
    assert all(isinstance(s.base_rate_cents, int) for s in contract.services.values())
    assert contract.services["Advanced Infectious Isolation Room Occupancy"].base_rate_cents == 164825
    assert contract.services["Specialist Psychiatric Sterilisation Service"].base_rate_cents == 213700
    assert contract.services["Intensive Paediatric Infusion Therapy"].base_rate_cents == 2900


def test_unit_bases(contract):
    s = contract.services
    assert s["Focused Palliative Recovery Room Occupancy"].unit_basis == "per_night"
    assert s["Advanced Infectious Isolation Room Occupancy"].unit_basis == "per_day"
    assert s["Postoperative Pulmonary Infusion Therapy"].unit_basis == "per_unit_dispensed"
    assert s["Supervised Haematology Biopsy Procedure"].unit_basis == "per_item"
    assert {v.unit_basis for v in s.values()} == {
        "per_hour", "per_day", "per_night", "per_visit", "per_test",
        "per_procedure", "per_item", "per_unit_dispensed", "per_hour_per_item",
    }


def test_clause_4_2_compound_unit_basis_is_not_collapsed(contract):
    svc = contract.services["Assisted Infectious Telemetry Monitoring"]
    assert svc.clause_id == "4.2"
    assert svc.unit_basis_text == "per hour, per item"
    assert svc.unit_basis == "per_hour_per_item"
    assert svc.compound_unit_basis is True
    assert svc.unit_basis != "per_hour"
    assert sum(v.compound_unit_basis for v in contract.services.values()) == 1


def test_uplift_discount_cap_values(contract):
    assert contract.non_business_day_uplifts["Emergency Renal Infusion Therapy"].uplift_fraction == Decimal("0.12")
    d = contract.daily_aggregate_uplifts["Focused Palliative Recovery Room Occupancy"]
    assert (d.exceeds_units, d.uplift_fraction) == (12, Decimal("0.30"))
    tiers = contract.volume_discounts["Emergency Haematology Transport Service"]
    assert [(t.exceeds_units, t.discount_fraction) for t in tiers] == [(60, Decimal("0.10")), (180, Decimal("0.20"))]
    assert contract.daily_caps["Focused Cardiac Dialysis Session"].max_units == 6


def test_bundles_are_reciprocal(contract):
    for b in contract.bundles:
        assert contract.bundle_partner(b.service_a) == (b.service_b, b.rate_a_cents)
        assert contract.bundle_partner(b.service_b) == (b.service_a, b.rate_b_cents)
        assert len(set(b.clause_ids)) == 2
    pair = next(b for b in contract.bundles if "Standard Orthopaedic Isolation Room Occupancy" in (b.service_a, b.service_b))
    rates = {pair.service_a: pair.rate_a_cents, pair.service_b: pair.rate_b_cents}
    assert rates == {"Standard Orthopaedic Isolation Room Occupancy": 27275,
                     "Outpatient Haematology Biopsy Procedure": 73625}


def test_exclusion_windows(contract):
    rules = {(e.service, e.other_service): e.days for e in contract.exclusion_windows}
    assert rules[("Intermittent Psychiatric Laboratory Panel", "Emergency Pulmonary Ventilation Support")] == 7
    assert rules[("Intensive Infectious Physiotherapy Session", "Routine Ophthalmic Laboratory Panel")] == 10


def test_clause_provenance(contract):
    for name, s in contract.services.items():
        assert s.source_text.startswith(f"In respect of {name}")
        assert "Contracted Services" in s.article
        assert s.clause_id.count(".") == 1
    for rule in list(contract.daily_caps.values()) + contract.exclusion_windows:
        assert rule.source_text in contract.services[rule.service].source_text
    assert set(contract.conventions) >= {"3.1_rounding", "3.2_order", "3.5_cumulative", "3.6_exclusion_direction"}


def test_service_name_slots_are_structural(contract):
    s = contract.services["Postoperative Orthopaedic Isolation Room Occupancy"]
    assert (s.qualifier, s.specialty, s.service_type) == ("Postoperative", "Orthopaedic", "Isolation Room Occupancy")


# -- fingerprint -----------------------------------------------------------------

def test_fingerprint_is_stable_and_tracks_the_text(contract, tmp_path):
    assert parse_contract().fingerprint == contract.fingerprint
    changed = parse_contract(_mutated(tmp_path, "GBP 1,648.25", "GBP 1,648.50"))
    assert changed.fingerprint != contract.fingerprint
    assert changed.services["Advanced Infectious Isolation Room Occupancy"].base_rate_cents == 164850


def test_contract_rules_artifact_round_trips(contract, tmp_path):
    path = tmp_path / "contract_rules.json"
    write_contract_rules(contract, path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["fingerprint"] == contract.fingerprint
    assert doc["counts"]["services"] == 76
    assert doc["services"]["Assisted Infectious Telemetry Monitoring"]["unit_basis"] == "per_hour_per_item"


# -- failing loudly ----------------------------------------------------------------

def test_an_unrecognised_sentence_in_a_service_clause_raises(tmp_path):
    path = _mutated(
        tmp_path,
        "The Provider shall not bill more than twenty-four (24) days of this Service",
        "The Provider shall ordinarily bill no more than twenty-four (24) days of this Service",
    )
    with pytest.raises(ContractParseError, match="unrecognised sentence"):
        parse_contract(path)


def test_number_words_and_digits_must_agree(tmp_path):
    path = _mutated(tmp_path, "exceeds twelve (12) nights", "exceeds twelve (13) nights")
    with pytest.raises(ContractParseError, match="does not equal"):
        parse_contract(path)


def test_the_two_statements_of_a_bundle_must_agree(tmp_path):
    """Clause 16.4 restates the 14.2 bundle; change one side's rate and the
    parser must refuse rather than pick one."""
    path = _mutated(tmp_path, "this Service at GBP 736.25 per procedure",
                    "this Service at GBP 736.50 per procedure")
    with pytest.raises(ContractParseError, match="disagree"):
        parse_contract(path)


def test_a_cap_stated_in_the_wrong_unit_raises(tmp_path):
    path = _mutated(tmp_path, "more than six (6) procedures of this Service",
                    "more than six (6) hours of this Service")
    with pytest.raises(ContractParseError, match="billed"):
        parse_contract(path)


def test_an_unknown_unit_basis_raises(tmp_path):
    path = _mutated(tmp_path, "GBP 1,648.25 per day of service", "GBP 1,648.25 per fortnight")
    with pytest.raises(ContractParseError):
        parse_contract(path)


def test_a_missing_article_iii_convention_raises(tmp_path):
    path = _mutated(tmp_path, "An exclusion window is measured in either direction", "An exclusion window is measured forwards")
    with pytest.raises(ContractParseError, match="3.6"):
        parse_contract(path)


@pytest.mark.parametrize("words,value", [
    ("six", 6), ("twelve", 12), ("twenty-four", 24), ("sixty", 60),
    ("one hundred and eighty", 180), ("three hundred", 300), ("one hundred and twenty", 120),
])
def test_words_to_int(words, value):
    assert words_to_int(words) == value


def test_the_plain_text_contract_agrees(contract):
    txt = (ROOT / "contracts" / "hospital_2" / "master_services_agreement.txt").read_text(encoding="utf-8")
    flat = " ".join(txt.split())
    for name, svc in contract.services.items():
        assert name in flat, name
        whole, cents = divmod(svc.base_rate_cents, 100)
        assert f"GBP {whole:,}.{cents:02d}" in flat, name
