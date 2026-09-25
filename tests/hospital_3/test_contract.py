"""Hospital 3 contract package: three documents, explicit precedence, and the
amendment keyed by Service Date."""

import datetime as _dt
import json

import pytest

from src.hospital_3.contract import (
    AMENDMENT_1, APPENDIX_B, BASE, PRECEDENCE, ContractParseError, RateEntry, cross_check_text_rendering, rule_counts,
    write_contract_rules,
)
from tests.hospital_3.conftest import ADDED, AMENDED, patched_package

BOUNDARY = [_dt.date(2024, 12, 31), _dt.date(2025, 1, 1), _dt.date(2025, 1, 2)]


def test_rule_counts(contract):
    assert rule_counts(contract) == {
        "services": 120, "appendix_b_services": 118, "amended_rates": 7, "added_services": 2,
        "daily_caps": 12, "threshold_premiums": 14, "non_business_day_uplifts": 12, "bundle_pairs": 5,
        "cumulative_discount_services": 12, "cumulative_discount_tiers": 19, "exclusion_windows": 10,
        "compound_unit_bases": 1,
    }


def test_front_matter_and_structure(contract):
    assert contract.contract_number == "INS-H3-2024-0562"
    assert contract.provider == "Rivermead General Hospital"
    assert (contract.effective_from, contract.effective_to) == ("2024-01-01", "2025-12-31")
    assert contract.currency == "GBP" and contract.rounding_convention == "half_up_cent"
    assert contract.pricing_order == ("bundle", "facility", "plan_tier", "premium", "cumulative_discount")
    assert contract.facility_code == "F-MAIN"
    assert contract.facility_multiplier == 1 and contract.plan_tier_multiplier == 1
    assert contract.non_business_weekdays == (5, 6)          # Saturday, Sunday


def test_precedence_is_explicit(contract):
    assert contract.precedence == (AMENDMENT_1, APPENDIX_B, BASE)
    assert PRECEDENCE[AMENDMENT_1] > PRECEDENCE[APPENDIX_B] > PRECEDENCE[BASE]


def test_amendment_effective_by_service_date(contract):
    assert contract.amendment_effective == "2025-01-01"
    assert contract.amendment_basis == "service_date"


@pytest.mark.parametrize("service", sorted(AMENDED))
def test_every_amended_rate_either_side_of_the_boundary(contract, service):
    before, after = AMENDED[service]
    assert contract.is_amended(service) and not contract.is_added(service)
    assert [contract.rate_on(service, d) for d in BOUNDARY] == [before, after, after]
    assert contract.rate_entry_on(service, BOUNDARY[0]).document == APPENDIX_B
    assert contract.rate_entry_on(service, BOUNDARY[1]).document == AMENDMENT_1
    # Both entries are in force from 2025; the amendment wins by precedence.
    assert {e.document for e in contract.services[service].rates if e.covers(BOUNDARY[1])} == {APPENDIX_B, AMENDMENT_1}


@pytest.mark.parametrize("service", sorted(ADDED))
def test_added_services_are_not_contracted_before_the_effective_date(contract, service):
    assert contract.is_added(service)
    assert contract.rate_on(service, _dt.date(2024, 12, 31)) is None
    assert not contract.contracted_on(service, _dt.date(2024, 12, 31))
    assert contract.rate_on(service, _dt.date(2025, 1, 1)) == ADDED[service]
    assert contract.services[service].contracted_from == _dt.date(2025, 1, 1)


def test_every_other_service_keeps_its_appendix_rate_throughout(contract):
    others = [n for n in contract.services if not contract.date_sensitive(n)]
    assert len(others) == 111
    for n in others:
        rates = {contract.rate_on(n, d) for d in [_dt.date(2024, 1, 1), *BOUNDARY, _dt.date(2025, 12, 31)]}
        assert rates == {contract.services[n].rates[0].rate_cents}
        assert contract.services[n].rates[0].document == APPENDIX_B


def test_rate_schedule_matches_the_plain_text_rendering(contract):
    assert cross_check_text_rendering(contract) == []


def test_spot_rates_and_bases(contract):
    s = contract.services
    assert (s["Advanced Gastrointestinal Telemetry Monitoring"].rates[0].rate_cents,
            s["Advanced Gastrointestinal Telemetry Monitoring"].unit_basis) == (128100, "per_day")
    assert s["Focused Urologic Telemetry Monitoring"].unit_basis == "per_hour_per_item"
    assert s["Bedside Cardiac Recovery Room Occupancy"].unit_basis == "per_night"
    assert s["Supervised Otolaryngologic Wound Care"].rates[0].rate_cents == 24025


def test_rule_tables(contract):
    p = contract.threshold_premiums["Ambulatory Renal Case Conference"]
    assert (p.exceeds_units, str(p.uplift_fraction)) == (8, "0.25")
    assert str(contract.non_business_day_uplifts["Specialist Psychiatric Discharge Planning"].uplift_fraction) == "0.1"
    tiers = contract.volume_discounts["Assisted Urologic Endoscopic Procedure"]
    assert [(t.exceeds_units, str(t.discount_fraction)) for t in tiers] == [(100, "0.1"), (300, "0.2")]
    assert contract.daily_caps["Intensive Infectious Anaesthesia Administration"].max_units == 4
    assert contract.bundle_partner("Bedside Palliative Wound Care")[:2] == (
        "Intermittent Gastrointestinal Endoscopic Procedure", 12025)
    assert contract.bundle_partner("Intermittent Gastrointestinal Endoscopic Procedure")[1] == 235000
    w = contract.exclusions_for("Intensive Ophthalmic Laboratory Panel")
    assert [(e.days, e.other_service) for e in w] == [(30, "Assisted Ophthalmic Recovery Room Occupancy")]


def test_vocabularies_are_disjoint(contract):
    assert not contract.qualifiers & contract.specialties
    assert not contract.qualifiers & contract.concept_words
    assert not contract.specialties & contract.concept_words


def test_rules_name_only_appendix_services(contract):
    added = {n for n in contract.services if contract.is_added(n)}
    named = (set(contract.threshold_premiums) | set(contract.non_business_day_uplifts) | set(contract.daily_caps)
             | set(contract.volume_discounts) | {s for b in contract.bundles for s in (b.service_a, b.service_b)}
             | {s for e in contract.exclusion_windows for s in (e.service, e.other_service)})
    assert not named & added


def test_equal_precedence_tie_is_refused(contract):
    svc = "Standard Orthopaedic Theatre Time"
    s = contract.services[svc]
    dup = RateEntry(svc, 1, APPENDIX_B, "B.1", None, None, "x")
    contract.services[svc] = type(s)(**{**s.__dict__, "rates": (s.rates[0], dup)})
    try:
        with pytest.raises(ContractParseError, match="equal precedence"):
            contract.rate_on(svc, _dt.date(2024, 6, 1))
    finally:
        contract.services[svc] = s


# -- the parser refuses to guess ------------------------------------------------------------

def test_disagreeing_old_rate_in_the_amendment_is_refused(tmp_path):
    with pytest.raises(ContractParseError, match="differs from Appendix B"):
        patched_package(tmp_path, "amendment", "| GBP 25.75 | GBP 30.50 |", "| GBP 25.70 | GBP 30.50 |")


def test_caps_that_disagree_between_documents_are_refused(tmp_path):
    with pytest.raises(ContractParseError, match="disagree"):
        patched_package(tmp_path, "base", "| Routine Psychiatric Consultation | 8 procedures |",
                        "| Routine Psychiatric Consultation | 9 procedures |")


def test_front_matter_must_match_across_documents(tmp_path):
    with pytest.raises(ContractParseError, match="front matter"):
        patched_package(tmp_path, "appendix", "**Contract number:** INS-H3-2024-0562",
                        "**Contract number:** INS-H3-2024-0563")


def test_a_directional_exclusion_clause_forces_a_rereading(tmp_path):
    with pytest.raises(ContractParseError, match="direction"):
        patched_package(tmp_path, "base", "## 9. Exclusion Windows\n",
                        "## 9. Exclusion Windows\n\n9.1 Measured after the other Service.\n")


def test_an_amendment_that_stops_being_by_service_date_is_refused(tmp_path):
    with pytest.raises(ContractParseError, match="A1.1.2"):
        patched_package(tmp_path, "amendment", "applies **by Service Date**", "applies **by invoice date**")


def test_a_rule_naming_an_added_service_is_refused(tmp_path):
    with pytest.raises(ContractParseError, match="unknown service"):
        patched_package(tmp_path, "base", "| Routine Psychiatric Consultation | 8 procedures |",
                        "| Elective Pulmonary Imaging Interpretation | 8 procedures |")


def test_a_changed_precedence_clause_is_refused(tmp_path):
    with pytest.raises(ContractParseError, match="1.3"):
        patched_package(tmp_path, "base", "an amendment prevails over Appendix B", "Appendix B prevails over an amendment")


def test_contract_rules_artifact(contract, tmp_path):
    path = tmp_path / "rules.json"
    write_contract_rules(contract, path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["fingerprint"] == contract.fingerprint
    assert doc["text_rendering_disagreements"] == []
    assert set(doc["source_sha256"]) == {BASE, APPENDIX_B, AMENDMENT_1}
    rates = doc["services"]["Intensive Ophthalmic Laboratory Panel"]["rates"]
    assert [(r["document"], r["rate_cents"], r["start"]) for r in rates] == [
        (APPENDIX_B, 41825, None), (AMENDMENT_1, 39725, "2025-01-01")]
