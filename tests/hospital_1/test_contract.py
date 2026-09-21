"""The contract parser, and the ways it is required to fail loudly.

The dangerous failure for an auditing engine is not a crash, it is a parser
that reads a rate table it does not understand as "there is no rule here".
Every test below exists to prove that a particular way of misreading the
contract raises instead of returning a plausible-looking empty rule family.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.hospital_1.conftest import SYNTHETIC_CONTRACT
from src.hospital_1.contract import ContractParseError, parse_contract
from src.shared.models import UnitBasis


def _write(tmp_path, text):
    path = tmp_path / "contract.md"
    path.write_text(text, encoding="utf-8")
    return path


# -- happy path -------------------------------------------------------------

def test_metadata(rules):
    assert rules.contract_number == "INS-TEST-0001"
    assert rules.effective_from.isoformat() == "2024-03-01"
    assert rules.effective_to.isoformat() == "2025-02-28"
    assert rules.rounding_convention == "half_up_cent"


def test_rates_are_whole_cents(rules):
    assert rules.services["Routine Hepatic Infusion Therapy"].base_rate_cents == 10000
    assert rules.services["Advanced Hepatic Infusion Therapy"].base_rate_cents == 3333
    assert all(isinstance(s.base_rate_cents, int) for s in rules.services.values())


def test_unit_bases_map_to_the_invoice_vocabulary(rules):
    assert rules.services["Trigger Pulmonary Telemetry Monitoring"].unit_basis is UnitBasis.DAY
    assert rules.services["Bundled Beta Specimen Analysis"].unit_basis is UnitBasis.ITEM


def test_every_rule_family_is_populated(rules):
    assert len(rules.services) == 13
    assert len(rules.threshold_premiums) == 1
    assert len(rules.non_business_day_uplifts) == 1
    assert sum(len(v) for v in rules.volume_discounts.values()) == 2
    assert len(rules.daily_caps) == 1
    assert len(rules.bundles) == 1
    assert len(rules.exclusion_windows) == 1


def test_percentages_are_exact_decimals(rules):
    assert rules.threshold_premiums["Premium Cardiac Ward Round"].uplift_fraction == Decimal("0.25")
    tiers = rules.volume_discounts["Volume Ophthalmic Specimen Analysis"]
    assert [t.discount_fraction for t in tiers] == [Decimal("0.10"), Decimal("0.30")]
    assert [t.exceeds_units for t in tiers] == [10, 20]


def test_provenance_is_preserved(rules):
    assert rules.services["Routine Hepatic Infusion Therapy"].source_section == "Section 4"
    assert rules.bundles[0].source_section == "Section 9"
    assert rules.exclusion_windows[0].source_section == "Section 10"


def test_bundle_partner_lookup_is_symmetric(rules):
    a = rules.bundle_partner("Bundled Alpha Theatre Time")
    b = rules.bundle_partner("Bundled Beta Specimen Analysis")
    assert a == ("Bundled Beta Specimen Analysis", 30000)
    assert b == ("Bundled Alpha Theatre Time", 9000)
    assert rules.bundle_partner("Routine Hepatic Infusion Therapy") is None


# -- required failures ------------------------------------------------------

def test_missing_section_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace("## 7. Cumulative Volume Discounts", "## 7. Something Else")
    with pytest.raises(ContractParseError, match="section not found"):
        parse_contract(_write(tmp_path, text))


def test_a_rate_table_row_with_the_wrong_shape_raises(tmp_path):
    """A row the parser cannot read must not be silently skipped."""
    text = SYNTHETIC_CONTRACT.replace(
        "| Solitary Geriatric Wound Care | per item supplied | GBP 17.00 | — |",
        "| Solitary Geriatric Wound Care | per item supplied | GBP 17.00 |",
    )
    with pytest.raises(ContractParseError, match="columns"):
        parse_contract(_write(tmp_path, text))


def test_an_unknown_unit_basis_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace("| per hour | GBP 100.00 |", "| per fortnight | GBP 100.00 |")
    with pytest.raises(ContractParseError, match="unknown unit basis"):
        parse_contract(_write(tmp_path, text))


def test_an_unparseable_money_amount_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace("GBP 100.00", "GBP one hundred")
    with pytest.raises(ContractParseError, match="money"):
        parse_contract(_write(tmp_path, text))


def test_a_sub_cent_rate_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace("GBP 100.00", "GBP 100.005")
    with pytest.raises(ContractParseError, match="money"):
        parse_contract(_write(tmp_path, text))


def test_a_rule_naming_an_unknown_service_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace(
        "| Premium Cardiac Ward Round | 6 visits | +25% |",
        "| Imaginary Cardiac Ward Round | 6 visits | +25% |",
    )
    with pytest.raises(ContractParseError, match="Section 5: unknown service"):
        parse_contract(_write(tmp_path, text))


def test_sections_4_and_8_disagreeing_about_a_cap_raises(tmp_path):
    """The two tables restate one rule; we must not pick a winner silently."""
    text = SYNTHETIC_CONTRACT.replace(
        "| Capped Renal Dialysis Session | 4 visits |",
        "| Capped Renal Dialysis Session | 9 visits |",
    )
    with pytest.raises(ContractParseError, match="disagree about the cap"):
        parse_contract(_write(tmp_path, text))


def test_a_cap_present_in_one_table_only_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace(
        "| Routine Hepatic Infusion Therapy | per hour | GBP 100.00 | — |",
        "| Routine Hepatic Infusion Therapy | per hour | GBP 100.00 | 5 hours |",
    )
    with pytest.raises(ContractParseError, match="disagree about which services are capped"):
        parse_contract(_write(tmp_path, text))


def test_non_monotonic_discount_tiers_raise(tmp_path):
    """Clause 7.2 assumes the deeper threshold carries the deeper discount."""
    text = SYNTHETIC_CONTRACT.replace(
        "| Volume Ophthalmic Specimen Analysis | 20 tests | 30% |",
        "| Volume Ophthalmic Specimen Analysis | 20 tests | 5% |",
    )
    with pytest.raises(ContractParseError, match="monotonically deeper"):
        parse_contract(_write(tmp_path, text))


def test_a_service_in_two_bundles_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace(
        "| Bundled Alpha Theatre Time | Bundled Beta Specimen Analysis | GBP 300.00 | GBP 90.00 |",
        "| Bundled Alpha Theatre Time | Bundled Beta Specimen Analysis | GBP 300.00 | GBP 90.00 |\n"
        "| Bundled Alpha Theatre Time | Solitary Geriatric Wound Care | GBP 310.00 | GBP 15.00 |",
    )
    with pytest.raises(ContractParseError, match="more than one bundle"):
        parse_contract(_write(tmp_path, text))


def test_an_unsupported_rounding_convention_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace("half_up_cent", "bankers_rounding")
    with pytest.raises(ContractParseError, match="rounding convention"):
        parse_contract(_write(tmp_path, text))


def test_an_unrecognised_facility_clause_raises(tmp_path):
    """Refuse to assume a multiplier of 1.0 just because we cannot read the clause."""
    text = SYNTHETIC_CONTRACT.replace(
        "No facility differential applies.", "Facility differentials are set out in Schedule B."
    )
    with pytest.raises(ContractParseError, match="facility differential"):
        parse_contract(_write(tmp_path, text))


def test_an_unrecognised_plan_tier_clause_raises(tmp_path):
    text = SYNTHETIC_CONTRACT.replace(
        "reimbursed at the same rate under this Agreement.",
        "reimbursed per Schedule C.",
    )
    with pytest.raises(ContractParseError, match="plan-tier clause"):
        parse_contract(_write(tmp_path, text))


def test_an_emptied_rule_section_raises(tmp_path):
    """A section that exists but yields nothing is a parse failure, not "no rule"."""
    text = SYNTHETIC_CONTRACT.replace(
        "| Excluded Neurological Biopsy Procedure | 7 days | Trigger Pulmonary Telemetry Monitoring |",
        "",
    )
    with pytest.raises(ContractParseError, match="no table rows parsed"):
        parse_contract(_write(tmp_path, text))


def test_a_dropped_rate_row_is_caught_by_the_row_count_check(tmp_path):
    """Guards against a regex that stops early and looks like it worked."""
    from src.hospital_1 import contract as contract_parser

    text = SYNTHETIC_CONTRACT
    original = contract_parser._table_rows

    def truncating(body, label, cols):
        rows = original(body, label, cols)
        return rows[:-1] if label == "Section 4" else rows

    contract_parser._table_rows = truncating
    try:
        with pytest.raises(ContractParseError, match="parsed 12 services"):
            parse_contract(_write(tmp_path, text))
    finally:
        contract_parser._table_rows = original


# -- the real Hospital 1 contract ------------------------------------------

def test_the_hospital_1_contract_parses_and_is_internally_consistent():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    rules = parse_contract(root / "contracts" / "hospital_1" / "provider_services_agreement.md")
    assert rules.contract_number == "INS-H1-2024-0417"
    assert len(rules.services) == 108
    assert len(rules.threshold_premiums) == 9
    assert len(rules.non_business_day_uplifts) == 7
    assert sum(len(v) for v in rules.volume_discounts.values()) == 11
    assert len(rules.daily_caps) == 7
    assert len(rules.bundles) == 3
    assert len(rules.exclusion_windows) == 6
    assert rules.warnings == []
    # Sections 5 and 6 are both "stage (d)"; the contract never says how they
    # would combine, so they had better not overlap.
    assert not (set(rules.threshold_premiums) & set(rules.non_business_day_uplifts))


def test_the_markdown_and_plain_text_contracts_agree():
    """Two renderings of one agreement; a difference means one was misread."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "contracts" / "hospital_1"
    md = parse_contract(root / "provider_services_agreement.md")
    txt_path = root / "provider_services_agreement.txt"
    text = txt_path.read_text(encoding="utf-8")
    for name, rule in md.services.items():
        assert name in text, name
