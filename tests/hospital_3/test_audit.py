"""Hospital 3 audit: the amendment by Service Date, pricing stage by stage,
hospital-wide rules, uncertainty, financial equivalence and reconstruction.

Expected rates are computed here independently of the engine, step by step
with half-up rounding (clause 3.1), from the real contract's numbers."""

import datetime as _dt
from decimal import ROUND_HALF_UP, Decimal

import pytest

from src.hospital_3.audit import (
    AuditPolicy, Interval, PricingEngine, discount_options, possible_dates, split_line_total, tier_fraction,
)
from src.hospital_3.matcher import MissingWordDecision
from tests.hospital_3.conftest import (
    ADDED_DAY, ADDED_PROC, AMENDED, BUNDLE_A, BUNDLE_B, CAPPED, CAPPED_AMENDED, EXCLUDED, EXCLUDED_AMENDED,
    ONE_TIER, ONE_TIER_AMENDED, PLAIN, PREMIUM, SATURDAY, SUNDAY, TIE_METAB, TIE_MSK, TRIGGER, TRIGGER_OF_AMENDED,
    TWO_TIER_AMENDED, WEEKEND, WEEKEND_AMENDED, billed, categories, expected, invoice, line, line_trace,
    patched_package, rate,
)


def half_up(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def by_hand(base: int, *, premium="0", discount="0") -> int:
    """Clause 3.2 by hand after substitution: facility x1, tier x1, premium, discount; round each step."""
    r = half_up(Decimal(base) * 1)
    r = half_up(Decimal(r) * 1)
    r = half_up(Decimal(r) * (1 + Decimal(premium)))
    return half_up(Decimal(r) * (1 - Decimal(discount)))


def d(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def plus(date: str, days: int) -> str:
    return (d(date) + _dt.timedelta(days=days)).isoformat()


# -- money and the order of stages ---------------------------------------------------------------

def test_half_up_is_applied_where_bankers_rounding_would_differ(contract, run):
    # 37575 x 1.10 = 41332.5 -> 41333 half up (41332 half-even); a Saturday uplift.
    assert by_hand(37575, premium="0.10") == 41333
    r = run(invoice("INV-1", [billed(contract, "L1", WEEKEND, 1, price=41333, date=SATURDAY)]))["INV-1"]
    assert expected(r) == 41333 and not r.result.flagged


def test_stage_order_bundle_facility_tier_premium_discount(contract):
    engine = PricingEngine(contract)
    entry = contract.rate_entry_on(BUNDLE_A, d(DAY := "2024-06-05"))
    st = engine.stages(BUNDLE_A, entry, bundle=True, premium=Decimal("0.25"), discount=Decimal("0.10"))
    assert (st["base"], st["bundle"], st["facility"], st["tier"]) == (14150, 12025, 12025, 12025)
    assert st["premium"] == half_up(Decimal(12025) * Decimal("1.25")) == 15031
    assert st["discount"] == half_up(Decimal(15031) * Decimal("0.9")) == 13528
    # Discount before premium would give a different cent.
    assert half_up(Decimal(half_up(Decimal(12025) * Decimal("0.9"))) * Decimal("1.25")) == 13529


def test_money_is_integer_cents(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 3)]))["INV-1"]
    assert isinstance(expected(r), int) and expected(r) == 3 * 6025


# -- Amendment No. 1: by Service Date -----------------------------------------------------------------

@pytest.mark.parametrize("service", sorted(AMENDED))
@pytest.mark.parametrize("date,which", [("2024-12-31", 0), ("2025-01-01", 1), ("2025-01-02", 1)])
def test_amended_rates_follow_the_service_date(contract, run, service, date, which):
    correct = AMENDED[service][which]
    ok = run(invoice("INV-1", [billed(contract, "L1", service, 1, price=correct, date=date)],
                     invoice_date="2025-03-01"))["INV-1"]
    assert expected(ok) == correct and not ok.result.flagged
    assert line_trace(ok, "L1")["pricing"]["rate_period"] == (["appendix_b B.1"] if which == 0
                                                               else ["amendment_no_1 A1.2"])
    wrong = AMENDED[service][1 - which]
    bad = run(invoice("INV-2", [billed(contract, "L1", service, 1, price=wrong, date=date)],
                      invoice_date="2025-03-01"))["INV-2"]
    assert categories(bad) == {"amended_rate_applied_before_effective_date" if which == 0
                               else "amended_rate_not_applied"}
    assert expected(bad) == correct


def test_invoice_date_never_moves_the_rate_period(contract, run):
    svc = ONE_TIER_AMENDED
    for invoice_date in ("2024-12-31", "2025-01-01", "2025-06-30", "2025-12-31"):
        r = run(invoice("INV-1", [billed(contract, "L1", svc, 1, price=68025, date="2024-12-31")],
                        invoice_date=invoice_date))["INV-1"]
        assert expected(r) == 68025 and not r.result.flagged


def test_a_2025_service_on_a_2024_invoice_is_priced_by_its_service_date_and_flagged_for_the_date(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", ONE_TIER_AMENDED, 1, price=80275, date="2025-01-02")],
                    invoice_date="2024-12-31"))["INV-1"]
    assert categories(r) == {"service_date_after_invoice_date"}
    assert expected(r) == 80275


@pytest.mark.parametrize("service", [ADDED_PROC, ADDED_DAY])
def test_added_service_before_the_effective_date_is_identified_but_not_contracted(contract, run, service):
    price = rate(contract, service, "2025-01-01")
    pre = run(invoice("INV-1", [billed(contract, "L1", service, 2, price=price, date="2024-12-31")]))["INV-1"]
    assert categories(pre) == {"service_not_contracted_on_date"}
    assert "unknown_service" not in categories(pre)
    t = line_trace(pre, "L1")
    assert t["identity"]["status"] == "MATCHED" and t["identity"]["service"] == service
    assert expected(pre) == 0                               # A1.3: "not billable"
    on = run(invoice("INV-2", [billed(contract, "L1", service, 2, price=price, date="2025-01-01")]))["INV-2"]
    assert not on.result.flagged and expected(on) == 2 * price
    blank = run(invoice("INV-3", [billed(contract, "L1", service, 2, price=price, date="2024-12-31")]),
                policy=AuditPolicy(uncontracted_on_date_payable_zero=False))["INV-3"]
    assert expected(blank) is None


def test_unknown_service_differs_from_uncontracted_on_date(contract, run):
    r = run(invoice("INV-1", [line("L1", "elective physiotherapy session vascular", 1, 1000, unit_basis="per_night")]))
    assert categories(r["INV-1"]) == {"unknown_service"} and expected(r["INV-1"]) is None


def test_malformed_date_either_side_of_the_amendment_is_left_blank(contract, run):
    svc = ONE_TIER_AMENDED
    r = run(invoice("INV-1", [billed(contract, "L1", svc, 1, price=80275, date="not-a-date")]))["INV-1"]
    assert "malformed_service_date" in categories(r)
    assert expected(r) is None
    assert "rate_period_uncertain" in line_trace(r, "L1")["uncertainty_tags"]


def test_malformed_date_wholly_after_the_amendment_is_priced(contract, run):
    # "2025-02-30": every day of February 2025, or 2 March 2025 -- all after 1 January 2025.
    svc = "Bedside Neurological Radiotherapy Fraction"
    r = run(invoice("INV-1", [billed(contract, "L1", svc, 3, price=3050, date="2025-02-30")]))["INV-1"]
    assert categories(r) == {"malformed_service_date"}
    assert expected(r) == 3 * 3050
    assert possible_dates("2025-02-30") == frozenset(
        {d(f"2025-02-{k:02d}") for k in range(1, 29)} | {d("2025-03-02")})


# -- threshold premiums (4.1): the patient's Service Day aggregate -----------------------------------

@pytest.mark.parametrize("qty,uplift", [(7, False), (8, False), (9, True)])
def test_threshold_premium_boundary(contract, run, qty, uplift):
    correct = by_hand(8775, premium="0.25") if uplift else 8775
    assert by_hand(8775, premium="0.25") == 10969                  # 10968.75 half up
    r = run(invoice("INV-1", [billed(contract, "L1", PREMIUM, qty, price=correct)]))["INV-1"]
    assert not r.result.flagged and expected(r) == qty * correct
    wrong = 8775 if uplift else 10969
    bad = run(invoice("INV-2", [billed(contract, "L1", PREMIUM, qty, price=wrong)]))["INV-2"]
    assert categories(bad) == {"premium_omitted" if uplift else "premium_incorrectly_applied"}


def test_day_aggregate_across_lines_and_invoices(contract, run):
    """Two billings of the premium service for one patient and day, on two
    invoices.  Default: the second is a repeat (10.3) and delivers nothing more,
    so the first is assessed on its own 5.  Literal reading: 5 + 5 = 10 > 8."""
    a = invoice("INV-A", [billed(contract, "A1", PREMIUM, 5)], invoice_date="2024-06-10")
    b = invoice("INV-B", [billed(contract, "B1", PREMIUM, 5)], invoice_date="2024-06-20")
    r = run(a, b)
    assert not r["INV-A"].result.flagged
    assert categories(r["INV-B"]) == {"cross_invoice_duplicate"} and expected(r["INV-B"]) == 0
    assert line_trace(r["INV-A"], "A1")["pricing"]["premium"]["day_quantity"] == [5, 5]
    lit = run(a, b, policy=AuditPolicy(repeats_count_toward_day_aggregate=True))
    assert line_trace(lit["INV-A"], "A1")["pricing"]["premium"]["day_quantity"] == [10, 10]
    assert categories(lit["INV-A"]) == {"premium_omitted"}
    assert expected(lit["INV-A"]) == 5 * 10969


def test_day_aggregate_is_per_patient_and_per_day(contract, run):
    a = invoice("INV-A", [billed(contract, "A1", PREMIUM, 5)], patient="PT-1")
    b = invoice("INV-B", [billed(contract, "B1", PREMIUM, 5)], patient="PT-2")
    c = invoice("INV-C", [billed(contract, "C1", PREMIUM, 5, date="2024-06-06")], patient="PT-1")
    r = run(a, b, c, policy=AuditPolicy(repeats_count_toward_day_aggregate=True))
    assert not any(x.result.flagged for x in r.values())


# -- non-business-day uplifts (Section 5, clause 2.2) ------------------------------------------------

@pytest.mark.parametrize("date,weekend", [("2024-06-05", False), (SATURDAY, True), (SUNDAY, True)])
def test_weekend_uplift(contract, run, date, weekend):
    correct = by_hand(37575, premium="0.10") if weekend else 37575
    r = run(invoice("INV-1", [billed(contract, "L1", WEEKEND, 2, price=correct, date=date)]))["INV-1"]
    assert not r.result.flagged and expected(r) == 2 * correct


def test_weekend_uplift_applies_to_the_amended_rate(contract, run):
    sat_2025 = "2025-01-04"
    assert d(sat_2025).weekday() == 5
    correct = by_hand(44050, premium="0.10")
    assert correct == 48455
    r = run(invoice("INV-1", [billed(contract, "L1", WEEKEND_AMENDED, 1, price=correct, date=sat_2025)]))["INV-1"]
    assert not r.result.flagged and expected(r) == correct


def test_no_public_holiday_logic(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", WEEKEND, 1, date="2024-12-25")]))["INV-1"]  # a Wednesday
    assert not r.result.flagged and expected(r) == 37575


# -- cumulative volume discounts (Section 6, 6.1) -----------------------------------------------------

def _history(contract, service, qty, *, date="2024-03-01", patient="PT-H", invoice_id="INV-H", line_id="H-01"):
    return invoice(invoice_id, [billed(contract, line_id, service, qty, date=date)], patient=patient,
                   invoice_date="2024-03-31")


@pytest.mark.parametrize("prior,discounted", [(99, False), (100, False), (101, True)])
def test_discount_starts_after_utilisation_exceeds_the_threshold(contract, run, prior, discounted):
    correct = by_hand(414850, discount="0.10") if discounted else 414850
    r = run(_history(contract, ONE_TIER, prior),
            invoice("INV-1", [billed(contract, "L1", ONE_TIER, 1, price=correct, date="2024-06-05")]))
    assert line_trace(r["INV-1"], "L1")["pricing"]["discount"]["prior_cumulative"] == [prior, prior]
    assert not r["INV-1"].result.flagged and expected(r["INV-1"]) == correct


def test_two_thresholds_take_the_deeper_discount(contract, run):
    correct = by_hand(111225, discount="0.20")               # amended rate, > 300 prior
    r = run(_history(contract, TWO_TIER_AMENDED, 301, date="2025-02-01"),
            invoice("INV-1", [billed(contract, "L1", TWO_TIER_AMENDED, 1, price=correct, date="2025-03-01")]))
    assert not r["INV-1"].result.flagged and expected(r["INV-1"]) == correct == 88980
    shallow = run(_history(contract, TWO_TIER_AMENDED, 301, date="2025-02-01"),
                  invoice("INV-2", [billed(contract, "L1", TWO_TIER_AMENDED, 1, price=by_hand(111225, discount="0.10"),
                                           date="2025-03-01")]))
    assert categories(shallow["INV-2"]) == {"volume_discount_incorrectly_applied"}


def test_utilisation_counts_across_patients_and_invoices(contract, run):
    r = run(_history(contract, ONE_TIER, 60, patient="PT-A", invoice_id="INV-HA", line_id="HA-1"),
            _history(contract, ONE_TIER, 41, patient="PT-B", invoice_id="INV-HB", line_id="HB-1"),
            invoice("INV-1", [billed(contract, "L1", ONE_TIER, 1, price=by_hand(414850, discount="0.10"))],
                    patient="PT-C"))
    assert line_trace(r["INV-1"], "L1")["pricing"]["discount"]["prior_cumulative"] == [101, 101]
    assert not r["INV-1"].result.flagged


def test_same_service_date_is_ordered_by_line_identifier(contract, run):
    """Two lines on one Service Date: the lower line id is counted first,
    whatever the invoice dates."""
    hist = _history(contract, ONE_TIER, 100, date="2024-06-01")
    first = invoice("INV-Z", [billed(contract, "L-A", ONE_TIER, 3, date="2024-06-05")],
                    patient="PT-2", invoice_date="2024-12-01")
    second = invoice("INV-A", [billed(contract, "L-B", ONE_TIER, 1, price=by_hand(414850, discount="0.10"),
                                      date="2024-06-05")], patient="PT-3", invoice_date="2024-06-06")
    r = run(hist, first, second)
    assert line_trace(r["INV-Z"], "L-A")["pricing"]["discount"]["prior_cumulative"] == [100, 100]
    assert line_trace(r["INV-A"], "L-B")["pricing"]["discount"]["prior_cumulative"] == [103, 103]
    assert not r["INV-Z"].result.flagged and not r["INV-A"].result.flagged


def test_a_line_crossing_the_threshold_is_not_split(contract, run):
    """Prior 98, quantity 5: utilisation before the line (98) has not exceeded
    100, so the whole line is undiscounted (line_prior).  The next line is."""
    hist = _history(contract, ONE_TIER, 98, date="2024-06-01")
    cross = invoice("INV-1", [billed(contract, "L1", ONE_TIER, 5, date="2024-06-05")], patient="PT-2")
    nxt = invoice("INV-2", [billed(contract, "L2", ONE_TIER, 1, price=by_hand(414850, discount="0.10"),
                                   date="2024-06-06")], patient="PT-3")
    r = run(hist, cross, nxt)
    assert expected(r["INV-1"]) == 5 * 414850 and not r["INV-1"].result.flagged
    assert line_trace(r["INV-2"], "L2")["pricing"]["discount"]["prior_cumulative"] == [103, 103]
    assert not r["INV-2"].result.flagged
    # The alternative reading: units 99 and 100 undiscounted, 101-103 discounted.
    alt = run(hist, cross, nxt, policy=AuditPolicy(discount_mode="unit_split"))
    assert expected(alt["INV-1"]) == 2 * 414850 + 3 * by_hand(414850, discount="0.10")
    assert categories(alt["INV-1"]) == {"volume_discount_omitted"}


def test_per_unit_reading_does_not_turn_an_arithmetic_error_into_a_discount_finding(contract, run):
    hist = _history(contract, ONE_TIER, 500, date="2024-06-01")
    price = by_hand(414850, discount="0.10")
    r = run(hist, invoice("INV-1", [billed(contract, "L1", ONE_TIER, 2, price=price, total=price * 2 + 1)],
                          patient="PT-2"), policy=AuditPolicy(discount_mode="unit_split"))
    assert categories(r["INV-1"]) == {"line_total_arithmetic"}


def test_split_and_tier_helpers(contract):
    tiers = contract.volume_discounts[TWO_TIER_AMENDED]
    assert tier_fraction(tiers, 100) == 0 and tier_fraction(tiers, 101) == Decimal("0.1")
    assert tier_fraction(tiers, 301) == Decimal("0.2")
    assert discount_options(tiers, Interval(90, 310)) == (Decimal(0), Decimal("0.1"), Decimal("0.2"))
    # prior 99: units 100 (not "more than" 100), 101 and 102 (discounted)
    assert split_line_total(lambda f: 100 if f == 0 else 90, tiers, 99, 3) == 100 + 90 + 90


def test_certain_repeats_are_not_utilisation(contract, run):
    a = invoice("INV-A", [billed(contract, "A1", ONE_TIER, 100, date="2024-06-01")], invoice_date="2024-06-02")
    rep = invoice("INV-B", [billed(contract, "B1", ONE_TIER, 100, date="2024-06-01")], invoice_date="2024-06-03")
    nxt = invoice("INV-C", [billed(contract, "C1", ONE_TIER, 1, date="2024-06-05")], patient="PT-9")
    r = run(a, rep, nxt)
    assert categories(r["INV-B"]) == {"cross_invoice_duplicate"}
    assert line_trace(r["INV-C"], "C1")["pricing"]["discount"]["prior_cumulative"] == [100, 100]
    assert not r["INV-C"].result.flagged


# -- bundles (Section 8, 8.1) ---------------------------------------------------------------------

@pytest.mark.parametrize("date", ["2024-06-05", "2025-06-04"])
def test_bundle_same_patient_same_day(contract, run, date):
    r = run(invoice("INV-1", [billed(contract, "L1", BUNDLE_A, 1, price=12025, date=date),
                              billed(contract, "L2", BUNDLE_B, 1, price=235000, date=date)]))["INV-1"]
    assert not r.result.flagged and expected(r) == 12025 + 235000
    miss = run(invoice("INV-2", [billed(contract, "L1", BUNDLE_A, 1, date=date),
                                 billed(contract, "L2", BUNDLE_B, 1, date=date)]))["INV-2"]
    assert categories(miss) == {"bundle_not_applied"} and expected(miss) == 12025 + 235000


def test_bundle_needs_same_patient_and_same_day(contract, run):
    a = invoice("INV-A", [billed(contract, "A1", BUNDLE_A, 1)], patient="PT-1")
    b = invoice("INV-B", [billed(contract, "B1", BUNDLE_B, 1)], patient="PT-2")
    c = invoice("INV-C", [billed(contract, "C1", BUNDLE_B, 1, date="2024-06-06")], patient="PT-1")
    r = run(a, b, c)
    assert not any(x.result.flagged for x in r.values())
    wrong = run(invoice("INV-D", [billed(contract, "D1", BUNDLE_A, 1, price=12025)], patient="PT-1"), b)
    assert categories(wrong["INV-D"]) == {"bundle_incorrectly_applied"}


def test_bundle_across_invoices_and_with_a_repeated_partner(contract, run):
    a = invoice("INV-A", [billed(contract, "A1", BUNDLE_A, 1, price=12025)], invoice_date="2024-06-10")
    b = invoice("INV-B", [billed(contract, "B1", BUNDLE_B, 1, price=235000)], invoice_date="2024-06-11")
    b2 = invoice("INV-C", [billed(contract, "C1", BUNDLE_B, 1, price=235000)], invoice_date="2024-06-12")
    r = run(a, b, b2)
    assert not r["INV-A"].result.flagged and not r["INV-B"].result.flagged
    assert categories(r["INV-C"]) == {"cross_invoice_duplicate"} and expected(r["INV-C"]) == 0


# -- daily caps (Section 7) -------------------------------------------------------------------------------

def test_cap_boundary_and_breach(contract, run):
    ok = run(invoice("INV-1", [billed(contract, "L1", CAPPED, 8)]))["INV-1"]
    assert not ok.result.flagged and expected(ok) == 8 * 282975
    over = run(invoice("INV-2", [billed(contract, "L1", CAPPED, 9)]))["INV-2"]
    assert categories(over) == {"daily_cap_exceeded"}
    assert expected(over) is None                            # the right quantity is not stated
    assert over.result.maximum_contractually_payable_total_cents == 8 * 282975
    priced = run(invoice("INV-3", [billed(contract, "L1", CAPPED, 9)]),
                 policy=AuditPolicy(cap_breach_blocks_reconstruction=False))["INV-3"]
    assert expected(priced) == 8 * 282975


def test_cap_on_an_amended_service_uses_the_amended_rate_for_the_ceiling(contract, run):
    over = run(invoice("INV-1", [billed(contract, "L1", CAPPED_AMENDED, 5, date="2025-03-05")]))["INV-1"]
    assert categories(over) == {"daily_cap_exceeded"}
    assert over.result.maximum_contractually_payable_total_cents == 4 * 312350


def test_cap_aggregate_across_invoices(contract, run):
    a = invoice("INV-A", [billed(contract, "A1", CAPPED, 5)], invoice_date="2024-06-10")
    b = invoice("INV-B", [billed(contract, "B1", CAPPED, 5)], invoice_date="2024-06-11")
    r = run(a, b)
    assert categories(r["INV-A"]) == set()                   # B is a repeat and delivers nothing more
    assert categories(r["INV-B"]) == {"cross_invoice_duplicate"}
    lit = run(a, b, policy=AuditPolicy(repeats_count_toward_day_aggregate=True))
    assert "daily_cap_exceeded" in categories(lit["INV-A"])


# -- exclusion windows (Section 9) ---------------------------------------------------------------------------

@pytest.mark.parametrize("offset,violates", [(0, True), (10, True), (11, False), (-10, True), (-11, False),
                                             (5, True), (-5, True)])
def test_exclusion_window_boundaries(contract, run, offset, violates):
    """``offset`` = excluded service's day minus the trigger's day (N = 10)."""
    trig = invoice("INV-T", [billed(contract, "T1", TRIGGER, 1, date="2024-06-15")], invoice_date="2024-07-31")
    exc = invoice("INV-E", [billed(contract, "E1", EXCLUDED, 1, date=plus("2024-06-15", offset))],
                  invoice_date="2024-07-31")
    r = run(trig, exc)
    assert not r["INV-T"].result.flagged                     # the trigger stays billable
    assert (categories(r["INV-E"]) == {"exclusion_window_violation"}) is violates
    assert expected(r["INV-E"]) == (0 if violates else 439850)


@pytest.mark.parametrize("offset,after_only,exclusive", [(0, True, True), (10, True, False), (5, True, True),
                                                         (-5, False, True), (-10, False, False)])
def test_exclusion_alternative_readings(contract, run, offset, after_only, exclusive):
    trig = invoice("INV-T", [billed(contract, "T1", TRIGGER, 1, date="2024-06-15")], invoice_date="2024-07-31")
    exc = invoice("INV-E", [billed(contract, "E1", EXCLUDED, 1, date=plus("2024-06-15", offset))],
                  invoice_date="2024-07-31")
    a = run(trig, exc, policy=AuditPolicy(exclusion_both_directions=False))
    assert ("exclusion_window_violation" in categories(a["INV-E"])) is after_only
    b = run(trig, exc, policy=AuditPolicy(exclusion_window_inclusive=False))
    assert ("exclusion_window_violation" in categories(b["INV-E"])) is exclusive


def test_exclusion_is_per_patient(contract, run):
    trig = invoice("INV-T", [billed(contract, "T1", TRIGGER, 1)], patient="PT-1")
    exc = invoice("INV-E", [billed(contract, "E1", EXCLUDED, 1)], patient="PT-2")
    assert not run(trig, exc)["INV-E"].result.flagged


def test_reading_dependent_exclusion_lowers_confidence_only_when_it_is_the_only_finding(contract, run):
    trig = invoice("INV-T", [billed(contract, "T1", TRIGGER, 1, date="2024-06-15")], invoice_date="2024-07-31")
    before = invoice("INV-B", [billed(contract, "B1", EXCLUDED, 1, date="2024-06-10")], invoice_date="2024-07-31")
    after = invoice("INV-A", [billed(contract, "A1", EXCLUDED, 1, date="2024-06-20")], invoice_date="2024-07-31",
                    patient="PT-1")
    rb = run(trig, before)["INV-B"]
    assert rb.result.confidence_band == "low"                # holds only under the two-sided reading
    ra = run(trig, after)["INV-A"]
    assert ra.result.confidence_band == "medium"             # holds under every reading


def test_amended_exclusion_service(contract, run):
    trig = invoice("INV-T", [billed(contract, "T1", TRIGGER_OF_AMENDED, 1, date="2025-01-20")],
                   invoice_date="2025-02-28")
    exc = invoice("INV-E", [billed(contract, "E1", EXCLUDED_AMENDED, 1, date="2025-01-25")],
                  invoice_date="2025-02-28")
    r = run(trig, exc)
    assert categories(r["INV-E"]) == {"exclusion_window_violation"} and expected(r["INV-E"]) == 0


# -- repeats and invoice numbers (10.1, 10.3) ------------------------------------------------------------------

def test_within_invoice_repeat(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 2), billed(contract, "L2", PLAIN, 2)]))["INV-1"]
    assert categories(r, "L2") == {"duplicate_service"} and expected(r) == 2 * 6025


def test_cross_invoice_repeat_first_billing_by_invoice_date_stands(contract, run):
    late = invoice("INV-A", [billed(contract, "A1", PLAIN, 2)], invoice_date="2024-07-20")
    early = invoice("INV-Z", [billed(contract, "Z1", PLAIN, 2)], invoice_date="2024-07-10")
    r = run(late, early)
    assert categories(r["INV-A"]) == {"cross_invoice_duplicate"} and expected(r["INV-A"]) == 0
    assert not r["INV-Z"].result.flagged


def test_unresolved_line_never_makes_a_certain_repeat(contract, run):
    a = invoice("INV-A", [line("A1", "ext endoscopic proc", 1, 442975, unit_basis="per_procedure")],
                invoice_date="2024-07-01")
    b = invoice("INV-B", [billed(contract, "B1", "Extended Musculoskeletal Endoscopic Procedure", 1)],
                invoice_date="2024-07-02")
    r = run(a, b)
    assert "cross_invoice_duplicate" not in categories(r["INV-B"])
    assert expected(r["INV-B"]) is None                     # it *may* be a repeat
    assert "possible_repeat" in line_trace(r["INV-B"], "B1")["uncertainty_tags"]


def test_duplicate_invoice_number_is_not_a_duplicate_service(contract, run):
    first = invoice("INV-1", [billed(contract, "L1", PLAIN, 1)], index=0, patient="PT-1")
    second = invoice("INV-1", [billed(contract, "L2", PLAIN, 1, date="2024-06-06")], index=1, patient="PT-2")
    r = run(first, second)["INV-1"]
    assert r.result.occurrence_ids == ["INV-1#0", "INV-1#1"]
    assert categories(r) == {"duplicate_invoice_id"} and expected(r) == 6025


# -- validation and arithmetic ------------------------------------------------------------------------------

def test_contract_number_and_facility(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 1)], contract_number="INS-H2-2024-1183",
                    facility="F-NORTH"))["INV-1"]
    assert categories(r) == {"contract_number_mismatch", "facility_mismatch"}
    assert expected(r) == 6025                               # no facility differential (1.5)


def test_plan_tiers_are_reimbursed_identically(contract, run):
    for tier in ("BRONZE", "SILVER", "GOLD", "PLATINUM"):
        r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 1)], tier=tier))["INV-1"]
        assert not r.result.flagged and expected(r) == 6025


def test_date_validation(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 1, date="2023-12-31"),
                              billed(contract, "L2", PLAIN, 1, date="2024-08-01"),
                              billed(contract, "L3", PLAIN, 1, price=6025, date="31/02/2024")], invoice_date="2024-07-01"))["INV-1"]
    assert categories(r, "L1") == {"service_date_out_of_window"}
    assert categories(r, "L2") == {"service_date_after_invoice_date"}
    assert categories(r, "L3") == {"malformed_service_date"}
    assert expected(r) == 3 * 6025                           # none of these changes a rate


def test_line_arithmetic_is_separate_from_contract_pricing(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 2, total=99999)]))["INV-1"]
    assert categories(r) == {"line_total_arithmetic"}
    assert expected(r) == 2 * 6025


def test_invoice_total_is_checked_against_its_lines(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 2)], total=1))["INV-1"]
    assert categories(r) == {"invoice_total_mismatch"} and expected(r) == 2 * 6025


def test_wrong_unit_basis(contract, run):
    r = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 2, unit_basis="per_item")]))["INV-1"]
    assert categories(r) == {"wrong_unit_basis"} and expected(r) == 2 * 6025


# -- uncertainty and financial equivalence ---------------------------------------------------------------------

def test_open_world_ambiguity_is_blank(contract, run):
    r = run(invoice("INV-1", [line("L1", "ext endoscopic proc", 1, 442975, unit_basis="per_procedure")]))["INV-1"]
    assert expected(r) is None and not r.result.pricing_complete
    assert any("omits a word" in b for b in r.blank_reasons)


def test_reviewed_identity_is_priced(contract, run, matcher):
    key = matcher.match("ext endoscopic proc").identity_key
    decision = {(key, "per_procedure"): MissingWordDecision("service", "Extended Musculoskeletal Endoscopic Procedure")}
    r = run(invoice("INV-1", [line("L1", "ext endoscopic proc", 1, 442975, unit_basis="per_procedure")]),
            decisions=decision)["INV-1"]
    assert expected(r) == 442975 and r.result.confidence_band == "medium"


def test_closed_tie_with_different_prices_stays_blank(contract, run, matcher):
    key = matcher.match("inpt thtr tm").identity_key
    decision = {(key, "per_hour"): MissingWordDecision("ambiguous_contracted")}
    r = run(invoice("INV-1", [line("L1", "inpt thtr tm", 2, 21000, unit_basis="per_hour")]),
            decisions=decision)["INV-1"]
    assert expected(r) is None
    assert any("candidate services lead to different" in b for b in r.blank_reasons)


def test_closed_tie_that_prices_identically_is_proven(tmp_path, matcher, run):
    """Financial equivalence: the two candidates priced the same (a patched
    rate), every reading gives one total, so the total is reconstructable."""
    c = patched_package(tmp_path, "appendix", "| Inpatient Musculoskeletal Theatre Time | per hour | GBP 92.50 |",
                        "| Inpatient Musculoskeletal Theatre Time | per hour | GBP 210.00 |")
    key = matcher.match("inpt thtr tm").identity_key
    decision = {(key, "per_hour"): MissingWordDecision("ambiguous_contracted")}
    r = run(invoice("INV-1", [line("L1", "inpt thtr tm", 2, 21000, unit_basis="per_hour")]),
            decisions=decision, contract_=c)["INV-1"]
    assert expected(r) == 42000
    assert r.represented.outcomes[0].financial_status == "semantic_ambiguous_but_financially_resolved"
    assert r.result.confidence_band == "medium"


def test_a_possible_bundle_partner_leaves_the_partner_blank(contract, run):
    """An unresolved line that might be the bundle partner makes the other
    line's rate depend on it."""
    r = run(invoice("INV-1", [billed(contract, "L1", BUNDLE_A, 1),
                              line("L2", "gi endosc proc", 1, 276475, unit_basis="per_procedure")]))["INV-1"]
    assert expected(r) is None
    assert "bundle_partner_uncertain" in line_trace(r, "L1")["uncertainty_tags"]


def test_blank_expected_total_never_uses_billed_or_zero(contract, run):
    r = run(invoice("INV-1", [line("L1", "ext endoscopic proc", 1, 442975, unit_basis="per_procedure"),
                              billed(contract, "L2", PLAIN, 1)]))["INV-1"]
    assert expected(r) is None and r.provisional_expected_total_cents is None
    assert r.result.billed_total_cents == 442975 + 6025


def test_confidence_bands(contract, run):
    clean = run(invoice("INV-1", [billed(contract, "L1", PLAIN, 1)]))["INV-1"]
    assert (clean.result.confidence_band, clean.result.confidence) == ("high", 0.85)
    structural = run(invoice("INV-2", [billed(contract, "L1", PLAIN, 1)], total=5))["INV-2"]
    assert structural.result.confidence_band == "high"
    blank = run(invoice("INV-3", [billed(contract, "L1", CAPPED, 9)]))["INV-3"]
    assert (blank.result.confidence_band, blank.result.confidence) == ("low", 0.40)
