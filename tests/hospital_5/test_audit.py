"""Hospital 5 audit: pricing stage by stage, hospital-wide rules, uncertainty,
financial equivalence and reconstruction.

Expected rates are computed here independently of the engine, step by step
with half-up rounding (clause 3.2), from the real contract's numbers."""

import datetime as _dt
from decimal import ROUND_HALF_UP, Decimal

import pytest

from src.hospital_5.audit import (
    AuditContext, AuditPolicy, PricingEngine, discount_options, possible_dates, split_line_total, tier_fraction,
    Interval,
)
from src.hospital_5.matcher import MissingWordDecision
from tests.hospital_5.conftest import (
    BUNDLE_A, BUNDLE_B, CAPPED, EXCLUDED, ONE_TIER, PLAIN, PREMIUM, SUPV_PALL, SUPV_VASC, TRIGGER, TWO_TIER,
    WEEKEND, SATURDAY, billed, categories, expected, invoice, line, line_trace, patched_contract,
)


def half_up(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def contract_rate(c, svc, facility="F-MAIN", tier="BRONZE", *, bundle=False, premium="0", discount="0") -> int:
    """Clause 3.1/3.3 by hand: substitute, then facility, tier, premium, discount; round after each."""
    r = Decimal(c.bundle_partner(svc)[1] if bundle else c.services[svc].base_rate_cents)
    r = half_up(r * c.facility_multipliers[svc][facility])
    r = half_up(r * c.tier_multipliers[svc][tier])
    r = half_up(r * (1 + Decimal(premium)))
    return half_up(r * (1 - Decimal(discount)))


def priced(contract, line_id, svc, qty, *, facility="F-MAIN", tier="BRONZE", **kw):
    """A line billed correctly for its facility and tier, with no adjustment."""
    date = kw.pop("date", "2024-06-05")
    return billed(contract, line_id, svc, qty, price=contract_rate(contract, svc, facility, tier), date=date, **kw)


# -- clause 3: the effective unit rate -----------------------------------------------------------

def test_every_step_rounds_half_up(contract):
    engine = PricingEngine(contract)
    found = None
    for svc in sorted(contract.services):
        for f in contract.facilities:
            for t in contract.plan_tiers:
                base = Decimal(contract.services[svc].base_rate_cents)
                f_m, t_m = contract.facility_multipliers[svc][f], contract.tier_multipliers[svc][t]
                if half_up(Decimal(half_up(base * f_m)) * t_m) != half_up(base * f_m * t_m):
                    found = (svc, f, t)
                    break
            if found:
                break
        if found:
            break
    assert found, "the contract should contain a case where rounding once would differ"
    svc, f, t = found
    stages = engine.stages(svc, f, t, bundle=False, premium=Decimal(0), discount=Decimal(0))
    assert stages["discount"] == contract_rate(contract, svc, f, t)
    base = Decimal(contract.services[svc].base_rate_cents)
    assert stages["discount"] != half_up(base * contract.facility_multipliers[svc][f] * contract.tier_multipliers[svc][t])


def test_premium_and_discount_steps_round_too(contract):
    engine = PricingEngine(contract)
    s = engine.stages(PREMIUM, "F-COAST", "GOLD", bundle=False, premium=Decimal("0.3"), discount=Decimal(0))
    # 35050 x 0.92 = 32246; x 0.95 = 30633.7 -> 30634; x 1.3 = 39824.2 -> 39824
    assert (s["facility"], s["tier"], s["premium"]) == (32246, 30634, 39824)
    d = engine.stages(TWO_TIER, "F-NORTH", "SILVER", bundle=False, premium=Decimal(0), discount=Decimal("0.12"))
    # 15700 x 1.2 = 18840; x 0.98 = 18463.2 -> 18463; x 0.88 = 16247.44 -> 16247
    assert (d["tier"], d["discount"]) == (18463, 16247)


def test_multiplier_of_one_leaves_the_amount_and_still_rounds(contract):
    s = PricingEngine(contract).stages(PLAIN, "F-MAIN", "BRONZE", bundle=False, premium=Decimal(0), discount=Decimal(0))
    assert s["facility_multiplier"] == "1" and s["facility"] == s["bundle"] == 161650
    assert s["tier"] == 169733                                      # 169732.5 rounds half up


def test_facility_and_tier_multipliers(contract, run):
    ok = invoice("INV-A", [priced(contract, "L1", PLAIN, 2, facility="F-NORTH", tier="SILVER")],
                 facility="F-NORTH", tier="SILVER")
    wrong = invoice("INV-B", [billed(contract, "L2", PLAIN, 2)], facility="F-NORTH", tier="SILVER", patient="PT-2")
    out = run(ok, wrong)
    assert not out["INV-A"].result.flagged
    assert expected(out["INV-A"]) == 2 * contract_rate(contract, PLAIN, "F-NORTH", "SILVER")
    assert categories(out["INV-B"]) == {"unit_price_mismatch"}
    assert expected(out["INV-B"]) == 2 * contract_rate(contract, PLAIN, "F-NORTH", "SILVER")


# -- Section 5 premiums: the Service Day aggregate, across invoices ------------------------------------

def test_threshold_premium_uses_the_day_aggregate_across_invoices(contract, run):
    up = contract_rate(contract, PREMIUM, premium="0.3")
    a = invoice("INV-A", [billed(contract, "LA", PREMIUM, 5, price=up)])
    b = invoice("INV-B", [billed(contract, "LB", PREMIUM, 4, price=up)], invoice_date="2024-07-02")
    out = run(a, b)
    # 9 visits that day > 8, but INV-B repeats the service on the same day:
    # clause 10.3 makes LB a repeat, which pays nothing and adds no units.
    assert categories(out["INV-B"], "LB") == {"cross_invoice_duplicate"} and expected(out["INV-B"]) == 0
    assert line_trace(out["INV-A"], "LA")["pricing"]["premium"]["day_quantity"] == [5, 5]
    assert categories(out["INV-A"], "LA") == {"premium_incorrectly_applied"}


def test_threshold_premium_counts_a_line_on_another_invoice(contract, run):
    """Across invoices, the earlier-billed line is the standing billing and is
    assessed on its own quantity; the later one repeats it (clause 10.3) and
    adds no units, so 9 + 2 is never an aggregate of 11."""
    up = contract_rate(contract, PREMIUM, premium="0.3")
    a = invoice("INV-A", [billed(contract, "LA", PREMIUM, 9, price=up)], invoice_date="2024-06-20")
    b = invoice("INV-B", [billed(contract, "LB", PREMIUM, 2, price=up)], invoice_date="2024-06-10", patient="PT-1")
    out = run(a, b)
    assert categories(out["INV-B"]) == {"premium_incorrectly_applied"}   # 2 visits: no premium due
    assert expected(out["INV-B"]) == 2 * contract_rate(contract, PREMIUM)
    assert categories(out["INV-A"]) == {"cross_invoice_duplicate"} and expected(out["INV-A"]) == 0


def test_threshold_premium_boundary(contract, run):
    base, up = contract_rate(contract, PREMIUM), contract_rate(contract, PREMIUM, premium="0.3")
    over = run(invoice("INV-A", [billed(contract, "L1", PREMIUM, 9, price=base)]))["INV-A"]
    assert categories(over) == {"premium_omitted"} and expected(over) == 9 * up
    at = run(invoice("INV-A", [billed(contract, "L1", PREMIUM, 8, price=up)]))["INV-A"]
    assert categories(at) == {"premium_incorrectly_applied"} and expected(at) == 8 * base


def test_weekend_uplift_applies_only_to_named_services(contract, run):
    up = contract_rate(contract, WEEKEND, premium="0.25")
    sat = invoice("INV-A", [billed(contract, "L1", WEEKEND, 2, price=contract_rate(contract, WEEKEND), date=SATURDAY),
                            priced(contract, "L2", PLAIN, 1, date=SATURDAY)])
    r = run(sat)["INV-A"]
    assert categories(r, "L1") == {"premium_omitted"} and categories(r, "L2") == set()
    assert expected(r) == 2 * up + contract_rate(contract, PLAIN)
    weekday = run(invoice("INV-B", [billed(contract, "L3", WEEKEND, 2, price=up)]))["INV-B"]
    assert categories(weekday) == {"premium_incorrectly_applied"}


# -- Section 7 bundles --------------------------------------------------------------------------------

def test_bundle_substitutes_both_rates_before_the_multipliers_across_invoices(contract, run):
    ra = contract_rate(contract, BUNDLE_A, "F-NORTH", "GOLD", bundle=True)
    rb = contract_rate(contract, BUNDLE_B, "F-NORTH", "GOLD", bundle=True)
    a = invoice("INV-A", [billed(contract, "LA", BUNDLE_A, 2, price=ra)], facility="F-NORTH", tier="GOLD")
    b = invoice("INV-B", [billed(contract, "LB", BUNDLE_B, 3, price=rb)], facility="F-NORTH", tier="GOLD",
                invoice_date="2024-07-02")
    out = run(a, b)
    assert not out["INV-A"].result.flagged and not out["INV-B"].result.flagged
    assert (expected(out["INV-A"]), expected(out["INV-B"])) == (2 * ra, 3 * rb)
    assert ra == half_up(Decimal(half_up(Decimal(17500) * Decimal("1.1"))) * Decimal("0.92"))


def test_bundle_not_applied_is_named_on_both_lines(contract, run):
    occ = invoice("INV-A", [priced(contract, "LA", BUNDLE_A, 1), priced(contract, "LB", BUNDLE_B, 2)])
    r = run(occ)["INV-A"]
    assert categories(r, "LA") == {"bundle_not_applied"} and categories(r, "LB") == {"bundle_not_applied"}
    assert expected(r) == contract_rate(contract, BUNDLE_A, bundle=True) + 2 * contract_rate(contract, BUNDLE_B, bundle=True)


def test_bundle_rate_without_a_partner(contract, run):
    r = run(invoice("INV-A", [billed(contract, "LA", BUNDLE_A, 1, price=contract_rate(contract, BUNDLE_A, bundle=True))]))
    assert categories(r["INV-A"]) == {"bundle_incorrectly_applied"}


# -- Section 8 cumulative discounts --------------------------------------------------------------------

def history(contract, svc, units, *, date="2024-03-01", patient="PT-9", invoice_id="INV-H"):
    """Earlier utilisation by another patient on another invoice."""
    return invoice(invoice_id, [priced(contract, f"{invoice_id}-L", svc, units, date=date)], patient=patient,
                   invoice_date="2024-03-31")


def test_discount_tier_arithmetic():
    class T:
        def __init__(self, n, f):
            self.exceeds_units, self.discount_fraction = n, Decimal(f)
    tiers = [T(100, "0.12"), T(300, "0.3")]
    assert tier_fraction(tiers, 100) == 0 and tier_fraction(tiers, 101) == Decimal("0.12")
    assert tier_fraction(tiers, 301) == Decimal("0.3")                 # the deeper discount replaces
    assert discount_options(tiers, Interval(99, 99)) == (Decimal(0),)
    assert discount_options(tiers, Interval(99, 101)) == (Decimal(0), Decimal("0.12"))
    assert discount_options(tiers, Interval(99, 400)) == (Decimal(0), Decimal("0.12"), Decimal("0.3"))
    # unit_split: prior 79, quantity 5, "more than 80": units 80..84 -> 81-84 discounted.
    assert split_line_total(lambda f: 100 if f == 0 else 88, [T(80, "0.12")], 79, 5) == 1 * 100 + 4 * 88


@pytest.mark.parametrize("prior,discounted", [(79, False), (80, False), (81, True)])
def test_line_prior_discount_boundary(contract, run, prior, discounted):
    """prior 79 + 5 crosses the threshold inside the line: under the adopted
    reading the line is not split and is not discounted; prior 80 is *at* the
    threshold ("more than 80"), still not discounted; prior 81 is past it."""
    r0, r12 = contract_rate(contract, ONE_TIER), contract_rate(contract, ONE_TIER, discount="0.12")
    target = invoice("INV-T", [billed(contract, "LT", ONE_TIER, 5, price=r12 if discounted else r0)])
    out = run(history(contract, ONE_TIER, prior), target)
    assert not out["INV-T"].result.flagged
    assert line_trace(out["INV-T"], "LT")["pricing"]["discount"]["prior_cumulative"] == [prior, prior]
    assert expected(out["INV-T"]) == 5 * (r12 if discounted else r0)


def test_unit_split_reading_discounts_only_the_subsequent_units(contract, run):
    r0, r12 = contract_rate(contract, ONE_TIER), contract_rate(contract, ONE_TIER, discount="0.12")
    target = invoice("INV-T", [billed(contract, "LT", ONE_TIER, 5, price=r0)])
    out = run(history(contract, ONE_TIER, 79), target, policy=AuditPolicy(discount_mode="unit_split"))
    assert expected(out["INV-T"]) == 1 * r0 + 4 * r12


def test_deeper_tier_replaces_the_first(contract, run):
    r12, r30 = (contract_rate(contract, TWO_TIER, discount=d) for d in ("0.12", "0.3"))
    out = run(history(contract, TWO_TIER, 301), invoice("INV-T", [billed(contract, "LT", TWO_TIER, 2, price=r12)]))
    assert categories(out["INV-T"]) == {"volume_discount_incorrectly_applied"}
    assert expected(out["INV-T"]) == 2 * r30


def test_cumulative_order_is_service_date_then_line_id_not_billing_order(contract, run):
    r0, r12 = contract_rate(contract, ONE_TIER), contract_rate(contract, ONE_TIER, discount="0.12")
    early = history(contract, ONE_TIER, 80)
    # Billed first, but delivered later: it counts after LT.
    later_delivery = invoice("INV-L", [priced(contract, "L-LATER", ONE_TIER, 10, date="2024-06-20")],
                             patient="PT-7", invoice_date="2024-06-01")
    same_day_low = invoice("INV-S", [priced(contract, "A-FIRST", ONE_TIER, 1, date="2024-06-05")], patient="PT-8")
    target = invoice("INV-T", [billed(contract, "LT", ONE_TIER, 1, price=r12)])
    out = run(early, later_delivery, same_day_low, target)
    # prior for LT = 80 (history) + 1 (A-FIRST, same day, lower line id) = 81 -> discounted
    assert line_trace(out["INV-T"], "LT")["pricing"]["discount"]["prior_cumulative"] == [81, 81]
    assert expected(out["INV-T"]) == r12
    # The later-delivered line sees everything before it, whoever billed first.
    assert line_trace(out["INV-L"], "L-LATER")["pricing"]["discount"]["prior_cumulative"] == [82, 82]


# -- Table 1 daily caps ---------------------------------------------------------------------------------

def test_cap_breach_is_flagged_and_not_reconstructed(contract, run):
    r = contract_rate(contract, CAPPED)
    out = run(invoice("INV-A", [billed(contract, "L1", CAPPED, 5, price=r)]))["INV-A"]
    assert categories(out) == {"daily_cap_exceeded"}
    assert expected(out) is None and not out.result.correction_reconstructable
    assert out.result.maximum_contractually_payable_total_cents == 4 * r
    assert "cap" in " ".join(out.blank_reasons)


def test_cap_boundary_and_alternative_policy(contract, run):
    r = contract_rate(contract, CAPPED)
    at = run(invoice("INV-A", [billed(contract, "L1", CAPPED, 4, price=r)]))["INV-A"]
    assert not at.result.flagged and expected(at) == 4 * r
    priced_at_cap = run(invoice("INV-A", [billed(contract, "L1", CAPPED, 6, price=r)]),
                        policy=AuditPolicy(cap_breach_blocks_reconstruction=False))["INV-A"]
    assert categories(priced_at_cap) == {"daily_cap_exceeded"} and expected(priced_at_cap) == 4 * r


# -- Section 9 exclusions -------------------------------------------------------------------------------

@pytest.mark.parametrize("excluded_date,violation", [
    ("2024-06-15", True),    # 10 days after: inside (inclusive)
    ("2024-06-16", False),   # 11 days after
    ("2024-05-26", True),    # 10 days before: either direction
    ("2024-05-25", False),
])
def test_exclusion_window_boundaries(contract, run, excluded_date, violation):
    occ = invoice("INV-A", [priced(contract, "LT", TRIGGER, 1, date="2024-06-05"),
                            priced(contract, "LX", EXCLUDED, 2, date=excluded_date)])
    r = run(occ)["INV-A"]
    assert ("exclusion_window_violation" in categories(r, "LX")) == violation
    assert categories(r, "LT") == set()                       # directed: the trigger stays billable
    trigger_total = contract_rate(contract, TRIGGER)
    assert expected(r) == trigger_total + (0 if violation else 2 * contract_rate(contract, EXCLUDED))


def test_exclusion_needs_the_same_patient_and_policy_directions(contract, run):
    t = invoice("INV-A", [priced(contract, "LT", TRIGGER, 1, date="2024-06-05")], patient="PT-1")
    x = invoice("INV-B", [priced(contract, "LX", EXCLUDED, 1, date="2024-06-06")], patient="PT-2")
    assert categories(run(t, x)["INV-B"]) == set()
    before = invoice("INV-C", [priced(contract, "LT", TRIGGER, 1, date="2024-06-05"),
                               priced(contract, "LX", EXCLUDED, 1, date="2024-06-01")])
    only_after = AuditPolicy(exclusion_both_directions=False)
    assert categories(run(before, policy=only_after)["INV-C"]) == set()


# -- Section 10 invoicing and data integrity ----------------------------------------------------------------

def test_repeat_on_one_invoice_and_across_invoices(contract, run):
    same = run(invoice("INV-A", [priced(contract, "L1", PLAIN, 1), priced(contract, "L2", PLAIN, 1)]))["INV-A"]
    assert categories(same, "L2") == {"duplicate_service"} and categories(same, "L1") == set()
    assert expected(same) == contract_rate(contract, PLAIN)
    first = invoice("INV-A", [priced(contract, "L1", PLAIN, 1)], invoice_date="2024-07-01")
    second = invoice("INV-B", [priced(contract, "L2", PLAIN, 1)], invoice_date="2024-07-03")
    out = run(second, first)                                 # file order does not decide: billing order does
    assert categories(out["INV-B"]) == {"cross_invoice_duplicate"} and expected(out["INV-B"]) == 0
    assert not out["INV-A"].result.flagged


def test_reused_invoice_number(contract, run):
    a = invoice("INV-A", [priced(contract, "L1", PLAIN, 1)], index=0)
    b = invoice("INV-A", [priced(contract, "L2", PLAIN, 3, date="2024-06-06")], index=1, patient="PT-2",
                invoice_date="2024-07-05")
    r = run(a, b)["INV-A"]
    assert r.result.occurrence_ids == ["INV-A#0", "INV-A#1"] and categories(r) == {"duplicate_invoice_id"}
    assert expected(r) == 3 * contract_rate(contract, PLAIN)          # the represented (later) occurrence


def test_non_financial_findings_do_not_blank_the_total(contract, run):
    rate = contract_rate(contract, PLAIN)
    cases = {
        "contract": invoice("INV-1", [priced(contract, "L1", PLAIN, 1)], contract_number="INS-H2-2024-1183"),
        "window": invoice("INV-2", [priced(contract, "L2", PLAIN, 1, date="2026-01-05")], invoice_date="2026-02-01"),
        "after": invoice("INV-3", [priced(contract, "L3", PLAIN, 1, date="2024-07-09")], patient="PT-3"),
        "malformed": invoice("INV-4", [priced(contract, "L4", PLAIN, 1, date="31/02/2024")], patient="PT-4"),
        "basis": invoice("INV-5", [priced(contract, "L5", PLAIN, 1, unit_basis="per_visit")], patient="PT-5"),
    }
    out = run(*cases.values())
    assert categories(out["INV-1"]) == {"contract_number_mismatch"}
    assert categories(out["INV-2"]) == {"service_date_out_of_window"}
    assert categories(out["INV-3"]) == {"service_date_after_invoice_date"}
    assert categories(out["INV-4"]) == {"malformed_service_date"}
    assert categories(out["INV-5"]) == {"wrong_unit_basis"}
    for iid in ("INV-1", "INV-2", "INV-3", "INV-4", "INV-5"):
        assert expected(out[iid]) == rate and out[iid].result.correction_reconstructable, iid


def test_arithmetic_findings_reconstruct_the_contract_total(contract, run):
    rate = contract_rate(contract, PLAIN)
    bad_line = run(invoice("INV-A", [priced(contract, "L1", PLAIN, 2, total=2 * rate + 100)]))["INV-A"]
    assert categories(bad_line) == {"line_total_arithmetic"} and expected(bad_line) == 2 * rate
    bad_total = run(invoice("INV-B", [priced(contract, "L1", PLAIN, 2)], total=2 * rate - 1))["INV-B"]
    assert categories(bad_total) == {"invoice_total_mismatch"} and expected(bad_total) == 2 * rate


def test_unknown_service_is_flagged_and_left_blank(contract, run):
    r = run(invoice("INV-A", [line("L1", "Emergency Palliative Sterilisation Service", 1, 1000),
                              priced(contract, "L2", PLAIN, 1)]))["INV-A"]
    assert categories(r, "L1") == {"unknown_service"}
    assert expected(r) is None and r.provisional_expected_total_cents == 1000 + contract_rate(contract, PLAIN)
    carried = run(invoice("INV-A", [line("L1", "Emergency Palliative Sterilisation Service", 1, 1000)]),
                  policy=AuditPolicy(unknown_service_line_carried_at_billed=True))["INV-A"]
    assert expected(carried) == 1000


def test_tie_break_basis_is_not_then_called_wrong(contract, run):
    hour = contract_rate(contract, "Ambulatory Hepatic Case Conference")
    r = run(invoice("INV-A", [line("L1", "HEP CS CONF", 3, hour, unit_basis="per_hour")]))["INV-A"]
    t = line_trace(r, "L1")
    assert t["identity"]["used_unit_basis_for_identity"] and t["identity"]["service"] == "Ambulatory Hepatic Case Conference"
    assert "wrong_unit_basis" not in categories(r) and expected(r) == 3 * hour


# -- financial equivalence and dependency-aware uncertainty ------------------------------------------------

CLOSED = {("consultation supervised", "per_procedure"): MissingWordDecision("ambiguous_contracted")}


def test_two_identities_with_the_same_corrected_amount_are_reconstructable(tmp_path, contract_text, run):
    # Give Supervised Vascular Consultation the palliative rate; at F-MAIN /
    # BRONZE both multipliers are 1, so the two readings price identically.
    same = patched_contract(tmp_path, contract_text, "| Supervised Vascular Consultation | per procedure | GBP 1,561.00 |",
                            "| Supervised Vascular Consultation | per procedure | GBP 4,414.25 |")
    occ = invoice("INV-A", [line("L1", "SUPV CONSULT", 2, 441425, unit_basis="per_procedure")])
    r = run(occ, decisions=CLOSED, contract_=same)["INV-A"]
    assert line_trace(r, "L1")["identity"]["status"] == "AMBIGUOUS"
    assert line_trace(r, "L1")["financial_status"] == "semantic_ambiguous_but_financially_resolved"
    assert expected(r) == 2 * 441425 and r.result.correction_reconstructable and not r.result.flagged


def test_two_identities_with_different_amounts_stay_unresolved(contract, run):
    occ = invoice("INV-A", [line("L1", "SUPV CONSULT", 2, 441425, unit_basis="per_procedure"),
                            priced(contract, "L2", PLAIN, 1)])
    r = run(occ, decisions=CLOSED)["INV-A"]
    assert line_trace(r, "L1")["financial_status"] == "financially_ambiguous"
    assert expected(r) is None
    assert any("different corrected amounts" in b for b in r.blank_reasons)
    # The unrelated certain line is still priced exactly.
    assert line_trace(r, "L2")["expected_line_total_cents"] == contract_rate(contract, PLAIN)


def test_an_open_world_line_is_never_priced_from_its_candidates(tmp_path, contract_text, run):
    same = patched_contract(tmp_path, contract_text, "| Supervised Vascular Consultation | per procedure | GBP 1,561.00 |",
                            "| Supervised Vascular Consultation | per procedure | GBP 4,414.25 |")
    occ = invoice("INV-A", [line("L1", "SUPV CONSULT", 2, 441425, unit_basis="per_procedure")])
    r = run(occ, contract_=same)["INV-A"]                     # no review: an uncontracted reading remains
    assert expected(r) is None and any("uncontracted" in b for b in r.blank_reasons)


def test_all_readings_not_payable_is_financially_resolved(contract, run):
    first = invoice("INV-A", [priced(contract, "L1", SUPV_PALL, 1), priced(contract, "L2", SUPV_VASC, 1)])
    repeat = invoice("INV-B", [line("L3", "SUPV CONSULT", 1, 999, unit_basis="per_procedure")],
                     invoice_date="2024-07-09")
    r = run(first, repeat, decisions=CLOSED)["INV-B"]
    # Whichever service it is, it repeats a same-day billing: it pays 0 either way.
    assert line_trace(r, "L3")["financial_status"] == "semantic_ambiguous_but_financially_resolved"
    assert expected(r) == 0 and "cross_invoice_duplicate" in categories(r)


def test_ambiguity_reaches_only_the_rules_of_its_candidates(contract, run):
    """"PALL CONSULT XYZ" is Comprehensive or Supervised Palliative
    Consultation; the second is the bundle partner of Elective Ophthalmic
    Recovery Room Occupancy.  So the unresolved line can reach exactly one
    rule of the certain lines: that bundle.  Every other line stays exact."""
    recovery = "Elective Ophthalmic Recovery Room Occupancy"
    occ = invoice("INV-A", [priced(contract, "L1", recovery, 1),
                            line("L2", "PALL CONSULT XYZ", 1, 35050, unit_basis="per_visit"),
                            priced(contract, "L3", PLAIN, 1),
                            priced(contract, "L4", WEEKEND, 1)])
    r = run(occ)["INV-A"]
    t1 = line_trace(r, "L1")
    assert t1["pricing"]["bundle"]["partner_present"] == "maybe"
    assert t1["uncertainty_tags"] == ["bundle_partner_uncertain"]
    assert set(t1["possible_line_totals_cents"]) == {contract_rate(contract, recovery),
                                                     contract_rate(contract, recovery, bundle=True)}
    assert categories(r, "L1") == set()                      # neither applied nor denied is asserted
    for lid in ("L3", "L4"):
        assert line_trace(r, lid)["uncertainty_tags"] == []
        assert line_trace(r, lid)["expected_line_total_cents"] is not None
    assert expected(r) is None


def test_a_possible_earlier_billing_reaches_only_the_repeat_rule(contract, run):
    first = invoice("INV-A", [line("L0", "PALL CONSULT XYZ", 4, 35050, unit_basis="per_visit")],
                    invoice_date="2024-06-06")
    later = invoice("INV-B", [billed(contract, "L1", PREMIUM, 5, price=contract_rate(contract, PREMIUM)),
                              priced(contract, "L2", PLAIN, 1)], invoice_date="2024-06-20")
    r = run(first, later)["INV-B"]
    t1 = line_trace(r, "L1")
    # Only two readings exist: L0 is this service (L1 repeats it: 0), or it is
    # not (L1 is the only billing: 5 visits, no premium).
    assert t1["uncertainty_tags"] == ["possible_repeat"]
    assert t1["possible_line_totals_cents"] == [0, 5 * contract_rate(contract, PREMIUM)]
    assert line_trace(r, "L2")["expected_line_total_cents"] == contract_rate(contract, PLAIN)
    assert expected(r) is None and any("repeat" in b for b in r.blank_reasons)


# -- malformed dates -----------------------------------------------------------------------------------------

def test_possible_dates_read_the_legible_parts():
    june = possible_dates("2025-06-31")
    assert len(june) == 31 and _dt.date(2025, 6, 1) in june and _dt.date(2025, 7, 1) in june
    feb = possible_dates("31/02/2024")
    assert _dt.date(2024, 2, 29) in feb and _dt.date(2024, 3, 2) in feb and len(feb) == 30
    thirteen = possible_dates("2024-13-05")
    assert len(thirteen) == 13 and _dt.date(2024, 5, 13) in thirteen and _dt.date(2024, 11, 5) in thirteen
    assert possible_dates("not a date") is None and possible_dates("") is None


def test_a_malformed_date_blanks_only_what_the_day_decides(contract, run):
    # Section 6 uplift: the 5th of each month of 2024 includes weekends and weekdays.
    r = run(invoice("INV-A", [billed(contract, "L1", WEEKEND, 1, price=contract_rate(contract, WEEKEND),
                                     date="2024-13-05")]))["INV-A"]
    assert categories(r) == {"malformed_service_date"} and expected(r) is None
    assert any("weekend uplift" in b for b in r.blank_reasons)
    plain = run(invoice("INV-B", [priced(contract, "L2", PLAIN, 1, date="2024-13-05")]))["INV-B"]
    assert expected(plain) == contract_rate(contract, PLAIN)


def test_a_malformed_date_can_only_repeat_lines_on_its_possible_days(contract, run):
    early = invoice("INV-A", [priced(contract, "L1", PLAIN, 1, date="31/02/2024")], invoice_date="2024-03-10")
    september = invoice("INV-B", [priced(contract, "L2", PLAIN, 1, date="2024-09-07")], invoice_date="2024-09-30")
    february = invoice("INV-C", [priced(contract, "L3", PLAIN, 1, date="2024-02-10")], invoice_date="2024-03-20")
    out = run(early, september, february)
    assert expected(out["INV-B"]) == contract_rate(contract, PLAIN)       # September cannot be "31/02/2024"
    assert expected(out["INV-C"]) is None                                 # 10 February might be
    assert any("repeat" in b for b in out["INV-C"].blank_reasons)


def test_context_same_day_relation(contract, matcher):
    from src.hospital_5.audit import resolve_lines
    occs = [invoice("INV-A", [priced(contract, "L1", PLAIN, 1, date="31/02/2024"),
                              priced(contract, "L2", PLAIN, 1, date="2024-02-10"),
                              priced(contract, "L3", PLAIN, 1, date="2024-09-07")])]
    ctx = AuditContext(resolve_lines(occs, matcher))
    a, b, c = (ctx.by_id[x] for x in ("L1", "L2", "L3"))
    assert (ctx.same_day(a, b), ctx.same_day(a, c), ctx.same_day(b, c)) == ("maybe", "no", "no")


# -- pre-commit review: exclusions, every window --------------------------------------------------------
#
# Section 9: "<Service> | Not billable within | N days | Of this Service".  The
# text states a distance, not an order, so both sides of the trigger count;
# "within N days" includes day N; day 0 (the same day) is within.

def _excl_case(contract, w, offset):
    trigger_day = _dt.date(2024, 6, 15)
    x_day = trigger_day + _dt.timedelta(days=offset)
    return invoice("INV-A", [priced(contract, "LT", w.other_service, 1, date=str(trigger_day)),
                             priced(contract, "LX", w.service, 1, date=str(x_day))])


@pytest.mark.parametrize("which", range(7))
def test_every_exclusion_window_boundary(contract, run, which):
    w = contract.exclusion_windows[which]
    for offset, violation in ((0, True), (w.days, True), (w.days + 1, False), (-w.days, True), (-w.days - 1, False)):
        r = run(_excl_case(contract, w, offset))["INV-A"]
        assert ("exclusion_window_violation" in categories(r, "LX")) == violation, (w.service, offset)
        assert "exclusion_window_violation" not in categories(r, "LT"), (w.service, offset)   # directed


def test_exclusion_across_invoices_ignores_billing_order(contract, run):
    # The excluded service is billed first, on an earlier invoice, ten days
    # before a trigger that appears only on a later invoice.
    x = invoice("INV-X", [priced(contract, "LX", EXCLUDED, 1, date="2024-06-05")], invoice_date="2024-06-06")
    t = invoice("INV-T", [priced(contract, "LT", TRIGGER, 1, date="2024-06-15")], invoice_date="2024-06-30")
    out = run(x, t)
    assert categories(out["INV-X"]) == {"exclusion_window_violation"} and expected(out["INV-X"]) == 0
    assert not out["INV-T"].result.flagged


# -- pre-commit review: discount boundaries ---------------------------------------------------------------
#
# Adopted reading (``line_prior``): a line is priced at the tier that
# cumulative utilisation *before* it has exceeded.  ``unit_split`` is the
# per-unit alternative.  T = 80 for ONE_TIER; T = 100 and 300 for TWO_TIER.

@pytest.mark.parametrize("prior,qty,prior_units_discounted,split_units_discounted", [
    (79, 1, 0, 0),    # unit 80: not beyond the threshold under any reading
    (79, 2, 0, 1),    # units 80-81: crosses inside the line
    (80, 1, 0, 1),    # unit 81 starts a line exactly at the threshold
    (81, 1, 1, 1),    # past the threshold
])
def test_one_tier_boundaries_under_both_readings(contract, run, prior, qty, prior_units_discounted,
                                                split_units_discounted):
    r0, r12 = contract_rate(contract, ONE_TIER), contract_rate(contract, ONE_TIER, discount="0.12")
    target = lambda: invoice("INV-T", [billed(contract, "LT", ONE_TIER, qty, price=r0)])
    adopted = run(history(contract, ONE_TIER, prior), target())["INV-T"]
    assert expected(adopted) == prior_units_discounted * r12 * qty + (1 - prior_units_discounted) * r0 * qty
    split = run(history(contract, ONE_TIER, prior), target(), policy=AuditPolicy(discount_mode="unit_split"))["INV-T"]
    assert expected(split) == split_units_discounted * r12 + (qty - split_units_discounted) * r0


def test_two_tiers_crossing_inside_one_line(contract, run):
    r0, r12, r30 = (contract_rate(contract, TWO_TIER, discount=d) for d in ("0", "0.12", "0.3"))
    # Crossing the first tier: prior 99, 5 hours.
    first = run(history(contract, TWO_TIER, 99), invoice("INV-T", [billed(contract, "LT", TWO_TIER, 5, price=r0)]))
    assert expected(first["INV-T"]) == 5 * r0
    # Crossing the second tier: prior 299 -> the whole line at 12% (utilisation before it exceeds 100 only).
    second = run(history(contract, TWO_TIER, 299), invoice("INV-T", [billed(contract, "LT", TWO_TIER, 5, price=r12)]))
    assert expected(second["INV-T"]) == 5 * r12 and not second["INV-T"].result.flagged
    split = run(history(contract, TWO_TIER, 299), invoice("INV-T", [billed(contract, "LT", TWO_TIER, 5, price=r12)]),
                policy=AuditPolicy(discount_mode="unit_split"))
    assert expected(split["INV-T"]) == 1 * r12 + 4 * r30
    # One line crossing both tiers: prior 99, 205 hours (units 100 .. 304).
    both = run(history(contract, TWO_TIER, 99), invoice("INV-T", [billed(contract, "LT", TWO_TIER, 205, price=r0)]))
    assert expected(both["INV-T"]) == 205 * r0
    both_split = run(history(contract, TWO_TIER, 99),
                     invoice("INV-T", [billed(contract, "LT", TWO_TIER, 205, price=r0)]),
                     policy=AuditPolicy(discount_mode="unit_split"))
    assert expected(both_split["INV-T"]) == 1 * r0 + 200 * r12 + 4 * r30


# -- pre-commit review: adversarial identity cases, end to end -------------------------------------------

def test_case_4_basis_disagreeing_with_a_reviewed_service_is_still_flagged(contract, run):
    """A reviewed choice that did not rest on the basis leaves the basis
    auditable: a wrong basis is still reported."""
    decision = {("comprehensive consultation", "per_hour"):
                MissingWordDecision("service", "Comprehensive Palliative Consultation")}
    rate = contract_rate(contract, PREMIUM)
    r = run(invoice("INV-A", [line("L1", "COMPR CONSULT", 2, rate, unit_basis="per_hour")]), decisions=decision)["INV-A"]
    t = line_trace(r, "L1")
    assert t["identity"]["method"] == "jev_missing_word" and not t["identity"]["used_unit_basis_for_identity"]
    assert categories(r) == {"wrong_unit_basis"} and expected(r) == 2 * rate


def test_case_4_a_tie_whose_basis_matches_no_candidate_stays_ambiguous(contract, run):
    r = run(invoice("INV-A", [line("L1", "HEP CS CONF", 2, 19775, unit_basis="per_day")]))["INV-A"]
    assert line_trace(r, "L1")["identity"]["status"] == "AMBIGUOUS"
    assert "wrong_unit_basis" in categories(r) and expected(r) is None


def test_case_5_without_an_accepted_review_a_single_candidate_is_not_priced(contract, run):
    rate = contract_rate(contract, PREMIUM)
    r = run(invoice("INV-A", [line("L1", "COMPR CONSULT", 2, rate, unit_basis="per_visit"),
                              priced(contract, "L2", PLAIN, 1)]))["INV-A"]
    assert line_trace(r, "L1")["identity"]["open_world"] and expected(r) is None
    assert not r.result.flagged                   # the billed rate is right for the only contracted reading
    assert line_trace(r, "L2")["expected_line_total_cents"] == contract_rate(contract, PLAIN)
