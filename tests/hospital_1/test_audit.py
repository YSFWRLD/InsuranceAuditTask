"""Hospital 1 audit behaviour, end to end, against the synthetic contract.

Every expected figure below is worked out by hand from the synthetic contract
in ``conftest.py``.  None is taken from a Hospital 1 label.

Sections
    Rounding through the pricing engine
    Daily caps              (Section 8)
    Threshold premiums      (Section 5) and weekend uplifts (Section 6)
    Volume discounts        (Section 7)
    Bundles                 (Section 9)
    Exclusion windows       (Section 10)
    Duplicates              (clauses 11.2 and 11.4)
    Whole invoices
    Hospital 1 source data
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import Path

import pytest

from src.shared.data import cross_check_against_csv, load_occurrences
from src.shared.money import round_cents
from tests.hospital_1.conftest import audit, categories, invoice, line

ROOT = Path(__file__).resolve().parents[2]


# ==========================================================================
# Rounding through the pricing engine
# ==========================================================================

def test_no_floats_reach_the_money_path(rules, matcher, auditor):
    """Every monetary field the engine produces is a Python ``int``."""
    occ = invoice("INV-T-1", [line("L-1", "INV-T-1", 1, "Routine Hepatic Infusion Therapy", 3, 10000)])
    result = audit(auditor, matcher, [occ])["INV-T-1"]
    assert isinstance(result.expected_total_cents, int)
    assert isinstance(result.billed_total_cents, int)


def test_rounding_is_applied_after_each_step_not_once(rules, engine, matcher):
    """Clause 3.1 makes per-step rounding observable.

    Advanced Hepatic Infusion Therapy is GBP 33.33 = 3333 cents.  Both the
    weekend uplift and a discount would round; doing them in one multiplication
    and rounding at the end can give a different cent.  We assert the
    contractual order explicitly rather than trusting it to coincide.
    """
    from tests.hospital_1.conftest import invoice as make_invoice
    from src.hospital_1.audit import AuditContext, ResolvedLine

    occ = make_invoice(
        "INV-T-2",
        [line("L-2", "INV-T-2", 1, "Advanced Hepatic Infusion Therapy", 1, 3333)],
    )
    rl = ResolvedLine(occ, occ.line_items[0], matcher.match("Advanced Hepatic Infusion Therapy"))
    ctx = AuditContext(rules, [rl])
    si = engine.stage_inputs(rl, "Advanced Hepatic Infusion Therapy", ctx)

    stepwise = engine.compute(
        rl, "Advanced Hepatic Infusion Therapy", si,
        premium_fraction=Decimal("0.125"), discount_fraction=Decimal("0.075"),
    ).expected_unit_rate_cents
    # step 1: 3333 * 1.125 = 3749.625 -> 3750; step 2: 3750 * 0.925 = 3468.75 -> 3469
    assert stepwise == 3469
    # one-shot: 3333 * 1.125 * 0.925 = 3468.403... -> 3468.  Different cent.
    one_shot = round_cents(Decimal(3333) * Decimal("1.125") * Decimal("0.925"))
    assert one_shot == 3468
    assert stepwise != one_shot


# ==========================================================================
# Daily caps
# ==========================================================================
#
# Section 8 daily caps, at cap-1, cap and cap+1.

CAP_SVC = "Capped Renal Dialysis Session"
CAP_RATE = 5000  # GBP 50.00
CAP_UNITS = 4


@pytest.mark.parametrize("qty", [1, CAP_UNITS - 1, CAP_UNITS])
def test_at_or_below_cap_is_clean(auditor, matcher, qty):
    occ = invoice("INV-C-1", [line("L-C1", "INV-C-1", 1, CAP_SVC, qty, CAP_RATE)])
    result = audit(auditor, matcher, [occ])["INV-C-1"]
    assert not result.flagged
    assert result.expected_total_cents == CAP_RATE * qty


def test_one_unit_over_cap_is_flagged(auditor, matcher):
    occ = invoice("INV-C-2", [line("L-C2", "INV-C-2", 1, CAP_SVC, CAP_UNITS + 1, CAP_RATE)])
    result = audit(auditor, matcher, [occ])["INV-C-2"]
    assert "daily_cap_exceeded" in categories(result)


def test_cap_breach_is_detected_but_not_reconstructed(auditor, matcher):
    """The cap proves the quantity is impossible; it does not reveal the truth.

    Eleven units against a cap of four means the figure is wrong.  It does not
    mean four were delivered -- it could have been one.  So the engine reports
    the breach, declines the corrected total, and carries the ceiling
    separately.
    """
    occ = invoice("INV-C-3", [line("L-C3", "INV-C-3", 1, CAP_SVC, 11, CAP_RATE)])
    result = audit(auditor, matcher, [occ])["INV-C-3"]
    assert result.flagged
    assert result.expected_total_cents is None
    assert result.pricing_complete is False
    assert result.correction_reconstructable is False
    assert result.maximum_contractually_payable_total_cents == CAP_RATE * CAP_UNITS


def test_cap_aggregates_across_lines_on_the_same_day(auditor, matcher):
    """Section 8 caps units per patient per service day, not per line.

    Three plus three exceeds four.  (Two lines of one service on one
    patient-day also breach clause 11.4, so both findings are expected.)
    """
    occ = invoice(
        "INV-C-4",
        [
            line("L-C4-1", "INV-C-4", 1, CAP_SVC, 3, CAP_RATE, service_date="2024-06-03"),
            line("L-C4-2", "INV-C-4", 2, CAP_SVC, 3, CAP_RATE, service_date="2024-06-03"),
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-C-4"]
    assert "daily_cap_exceeded" in categories(result)
    assert "duplicate_service" in categories(result)


def test_cap_aggregates_across_invoices(auditor, matcher):
    """A patient's day is capped however many invoices it is split over."""
    a = invoice(
        "INV-C-5",
        [line("L-C5-1", "INV-C-5", 1, CAP_SVC, 3, CAP_RATE, service_date="2024-06-03")],
    )
    b = invoice(
        "INV-C-6",
        [line("L-C6-1", "INV-C-6", 1, CAP_SVC, 3, CAP_RATE, service_date="2024-06-03")],
        invoice_date="2024-07-02",
    )
    results = audit(auditor, matcher, [a, b])
    assert "daily_cap_exceeded" in categories(results["INV-C-5"])
    assert "daily_cap_exceeded" in categories(results["INV-C-6"])


def test_cap_does_not_aggregate_across_patients_or_days(auditor, matcher):
    a = invoice(
        "INV-C-7",
        [line("L-C7-1", "INV-C-7", 1, CAP_SVC, 3, CAP_RATE, service_date="2024-06-03")],
        patient_id="PT-TEST-1",
    )
    b = invoice(
        "INV-C-8",
        [line("L-C8-1", "INV-C-8", 1, CAP_SVC, 3, CAP_RATE, service_date="2024-06-03")],
        patient_id="PT-TEST-2",
    )
    c = invoice(
        "INV-C-9",
        [line("L-C9-1", "INV-C-9", 1, CAP_SVC, 3, CAP_RATE, service_date="2024-06-04")],
        patient_id="PT-TEST-1",
    )
    results = audit(auditor, matcher, [a, b, c])
    assert not any(results[i].flagged for i in ("INV-C-7", "INV-C-8", "INV-C-9"))


# ==========================================================================
# Threshold premiums and weekend uplifts
# ==========================================================================
#
# Section 5 threshold premiums and Section 6 non-business-day uplifts.

PREMIUM_SVC = "Premium Cardiac Ward Round"
PREMIUM_RATE = 8000          # GBP 80.00
PREMIUM_UPLIFTED = 10000     # +25%
THRESHOLD = 6

WEEKEND_SVC = "Weekend Vascular Transport Service"
WEEKEND_RATE = 20000         # GBP 200.00
WEEKEND_UPLIFTED = 22000     # +10%

MONDAY = "2024-06-03"
SATURDAY = "2024-06-08"
SUNDAY = "2024-06-09"


# -- threshold premiums ----------------------------------------------------

@pytest.mark.parametrize("qty", [THRESHOLD - 1, THRESHOLD])
def test_at_or_below_threshold_no_premium(auditor, matcher, qty):
    """"Applies when daily quantity *exceeds* 6" -- six is not more than six."""
    occ = invoice("INV-P-1", [line("L-P1", "INV-P-1", 1, PREMIUM_SVC, qty, PREMIUM_RATE)])
    result = audit(auditor, matcher, [occ])["INV-P-1"]
    assert not result.flagged
    assert result.expected_total_cents == PREMIUM_RATE * qty


def test_above_threshold_premium_is_due(auditor, matcher):
    qty = THRESHOLD + 1
    occ = invoice(
        "INV-P-2", [line("L-P2", "INV-P-2", 1, PREMIUM_SVC, qty, PREMIUM_UPLIFTED)]
    )
    result = audit(auditor, matcher, [occ])["INV-P-2"]
    assert not result.flagged
    assert result.expected_total_cents == PREMIUM_UPLIFTED * qty


def test_premium_omitted_is_detected_and_named(auditor, matcher):
    qty = THRESHOLD + 1
    occ = invoice(
        "INV-P-3", [line("L-P3", "INV-P-3", 1, PREMIUM_SVC, qty, PREMIUM_RATE)]
    )
    result = audit(auditor, matcher, [occ])["INV-P-3"]
    assert categories(result) == {"premium_omitted"}
    assert result.expected_total_cents == PREMIUM_UPLIFTED * qty


def test_premium_incorrectly_applied_is_detected_and_named(auditor, matcher):
    qty = THRESHOLD - 1
    occ = invoice(
        "INV-P-4", [line("L-P4", "INV-P-4", 1, PREMIUM_SVC, qty, PREMIUM_UPLIFTED)]
    )
    result = audit(auditor, matcher, [occ])["INV-P-4"]
    assert categories(result) == {"premium_incorrectly_applied"}
    assert result.expected_total_cents == PREMIUM_RATE * qty


def test_threshold_is_assessed_on_the_daily_aggregate_not_the_line(
    auditor, matcher, engine
):
    """Clause 5.1: aggregate over the patient's service day, not per line.

    Four plus four exceeds six, so both lines carry the premium even though
    neither alone would.  This is asserted at the pricing layer because two
    lines of one service on one patient-day also breach clause 11.4, and that
    separate finding would otherwise mask what is being tested here.
    """
    from tests.hospital_1.conftest import build_context

    occ = invoice(
        "INV-P-5",
        [
            line("L-P5-1", "INV-P-5", 1, PREMIUM_SVC, 4, PREMIUM_UPLIFTED, service_date=MONDAY),
            line("L-P5-2", "INV-P-5", 2, PREMIUM_SVC, 4, PREMIUM_UPLIFTED, service_date=MONDAY),
        ],
    )
    ctx, by_line = build_context(auditor, matcher, [occ])
    for line_id in ("L-P5-1", "L-P5-2"):
        priced = engine.price_line(by_line[line_id], PREMIUM_SVC, ctx)
        assert priced.expected_unit_rate_cents == PREMIUM_UPLIFTED, line_id


def test_daily_aggregate_does_not_cross_days(auditor, matcher):
    occ = invoice(
        "INV-P-6",
        [
            line("L-P6-1", "INV-P-6", 1, PREMIUM_SVC, 4, PREMIUM_RATE, service_date="2024-06-03"),
            line("L-P6-2", "INV-P-6", 2, PREMIUM_SVC, 4, PREMIUM_RATE, service_date="2024-06-04"),
        ],
    )
    assert not audit(auditor, matcher, [occ])["INV-P-6"].flagged


# -- non-business-day uplifts ----------------------------------------------

def test_weekday_carries_no_uplift(auditor, matcher):
    occ = invoice(
        "INV-W-1",
        [line("L-W1", "INV-W-1", 1, WEEKEND_SVC, 2, WEEKEND_RATE, service_date=MONDAY)],
    )
    assert not audit(auditor, matcher, [occ])["INV-W-1"].flagged


@pytest.mark.parametrize("date", [SATURDAY, SUNDAY])
def test_saturday_and_sunday_carry_the_uplift(auditor, matcher, date):
    occ = invoice(
        "INV-W-2",
        [line("L-W2", "INV-W-2", 1, WEEKEND_SVC, 2, WEEKEND_UPLIFTED, service_date=date)],
    )
    result = audit(auditor, matcher, [occ])["INV-W-2"]
    assert not result.flagged
    assert result.expected_total_cents == WEEKEND_UPLIFTED * 2


def test_weekend_uplift_omitted_is_detected(auditor, matcher):
    occ = invoice(
        "INV-W-3",
        [line("L-W3", "INV-W-3", 1, WEEKEND_SVC, 1, WEEKEND_RATE, service_date=SATURDAY)],
    )
    result = audit(auditor, matcher, [occ])["INV-W-3"]
    assert categories(result) == {"premium_omitted"}
    assert result.expected_total_cents == WEEKEND_UPLIFTED


def test_weekend_uplift_applied_on_a_weekday_is_detected(auditor, matcher):
    occ = invoice(
        "INV-W-4",
        [line("L-W4", "INV-W-4", 1, WEEKEND_SVC, 1, WEEKEND_UPLIFTED, service_date=MONDAY)],
    )
    result = audit(auditor, matcher, [occ])["INV-W-4"]
    assert categories(result) == {"premium_incorrectly_applied"}
    assert result.expected_total_cents == WEEKEND_RATE


# ==========================================================================
# Volume discounts
# ==========================================================================
#
# Section 7 cumulative volume discounts.
#
# The tricky parts are all about *when* the running total is read: clause 2.4
# excludes the line being priced, clause 7.1 counts across every patient and the
# whole term, and clause 7.2 breaks same-date ties by line identifier.
#
# Note that clause 11.4 forbids billing the same service twice for one patient on
# one day, so the sequences below advance the service date line by line.  The
# tie-break test uses different patients, which is the only way two lines of one
# service can legitimately share a date.

DISCOUNT_SVC = "Volume Ophthalmic Specimen Analysis"
DISCOUNT_RATE = 25000       # GBP 250.00
D10 = 22500        # -10%
D30 = 17500        # -30%
DISCOUNT_START = _dt.date(2024, 6, 3)


def _seq(*quantities_and_prices):
    """One invoice, one line per day, ascending service dates and line ids."""
    lines = [
        line(
            f"L-D-{i:02d}", "INV-D", i, DISCOUNT_SVC, qty, price,
            service_date=(DISCOUNT_START + _dt.timedelta(days=i - 1)).isoformat(),
        )
        for i, (qty, price) in enumerate(quantities_and_prices, start=1)
    ]
    return invoice("INV-D", lines, invoice_date="2024-09-01")


def test_before_the_threshold_no_discount(auditor, matcher):
    # prior totals: 0, then 5.  Neither exceeds 10.
    assert not audit(auditor, matcher, [_seq((5, DISCOUNT_RATE), (5, DISCOUNT_RATE))])["INV-D"].flagged


def test_the_line_that_crosses_the_threshold_is_not_itself_discounted(auditor, matcher):
    """Clause 2.4 counts utilisation *up to but excluding* the line priced.

    Line 2 takes the running total from 8 to 16, but the total *before* it is
    8, which does not exceed 10.  Line 2 is full price; line 3 is discounted.
    """
    occ = _seq((8, DISCOUNT_RATE), (8, DISCOUNT_RATE), (1, D10))
    assert not audit(auditor, matcher, [occ])["INV-D"].flagged


def test_a_line_discounted_too_early_is_detected(auditor, matcher):
    result = audit(auditor, matcher, [_seq((8, DISCOUNT_RATE), (8, D10))])["INV-D"]
    assert categories(result) == {"volume_discount_incorrectly_applied"}


def test_a_line_not_discounted_when_due_is_detected(auditor, matcher):
    result = audit(auditor, matcher, [_seq((11, DISCOUNT_RATE), (1, DISCOUNT_RATE))])["INV-D"]
    assert categories(result) == {"volume_discount_omitted"}
    assert result.expected_total_cents == DISCOUNT_RATE * 11 + D10


def test_second_threshold_applies_the_deeper_discount(auditor, matcher):
    """Clause 7.2: where two thresholds are met, the deeper discount applies."""
    assert not audit(auditor, matcher, [_seq((21, DISCOUNT_RATE), (1, D30))])["INV-D"].flagged


def test_the_wrong_tier_is_named_as_such(auditor, matcher):
    """Billing the 10% rate where 30% was due is a tier error, not an omission."""
    result = audit(auditor, matcher, [_seq((21, DISCOUNT_RATE), (1, D10))])["INV-D"]
    assert categories(result) == {"volume_discount_incorrectly_applied"}


def test_same_date_lines_are_ordered_by_line_identifier(auditor, matcher):
    """Clause 7.2's tie-break, made observable.

    Three patients receive the service on the same day.  Under line-id
    ordering the running total before the third line is 11, so the third line
    -- and only the third -- is discounted.  Any other ordering of the three
    would put the discount on a different line and flag this invoice.
    """
    day = DISCOUNT_START.isoformat()
    occs = [
        invoice(
            f"INV-D-{i}",
            [line(f"L-D-{i:02d}", f"INV-D-{i}", 1, DISCOUNT_SVC, qty, price, service_date=day)],
            patient_id=f"PT-TEST-{i}",
            invoice_date="2024-09-01",
        )
        for i, (qty, price) in enumerate([(6, DISCOUNT_RATE), (5, DISCOUNT_RATE), (4, D10)], start=1)
    ]
    results = audit(auditor, matcher, occs)
    assert not any(r.flagged for r in results.values())


def test_utilisation_is_aggregated_across_patients(auditor, matcher):
    """Clause 7.1 counts across all patients, not per patient."""
    a = invoice(
        "INV-D1",
        [line("L-D1-1", "INV-D1", 1, DISCOUNT_SVC, 11, DISCOUNT_RATE, service_date="2024-06-03")],
        patient_id="PT-TEST-1",
    )
    b = invoice(
        "INV-D2",
        [line("L-D2-1", "INV-D2", 1, DISCOUNT_SVC, 1, D10, service_date="2024-06-04")],
        patient_id="PT-TEST-2",
    )
    results = audit(auditor, matcher, [a, b])
    assert not results["INV-D1"].flagged
    assert not results["INV-D2"].flagged


def test_earlier_service_date_counts_even_on_a_later_invoice(auditor, matcher):
    """Utilisation is ordered by service date, not by invoice date."""
    later_invoice_earlier_service = invoice(
        "INV-D3",
        [line("L-D3-1", "INV-D3", 1, DISCOUNT_SVC, 11, DISCOUNT_RATE, service_date="2024-06-01")],
        invoice_date="2024-09-01",
        patient_id="PT-TEST-1",
    )
    earlier_invoice_later_service = invoice(
        "INV-D4",
        [line("L-D4-1", "INV-D4", 1, DISCOUNT_SVC, 1, D10, service_date="2024-06-02")],
        invoice_date="2024-07-01",
        patient_id="PT-TEST-2",
    )
    results = audit(
        auditor, matcher, [later_invoice_earlier_service, earlier_invoice_later_service]
    )
    assert not results["INV-D3"].flagged
    assert not results["INV-D4"].flagged


# ==========================================================================
# Bundles
# ==========================================================================
#
# Section 9 bundled rates: substituted only when both services meet.

BUNDLE_A = "Bundled Alpha Theatre Time"
BUNDLE_B = "Bundled Beta Specimen Analysis"
A_STANDALONE, A_BUNDLED = 40000, 30000
B_STANDALONE, B_BUNDLED = 12000, 9000
BUNDLE_DAY = "2024-06-03"
BUNDLE_OTHER_DAY = "2024-06-04"


def test_a_alone_uses_the_standalone_rate(auditor, matcher):
    occ = invoice("INV-B-1", [line("L-B1", "INV-B-1", 1, BUNDLE_A, 2, A_STANDALONE, service_date=BUNDLE_DAY)])
    result = audit(auditor, matcher, [occ])["INV-B-1"]
    assert not result.flagged
    assert result.expected_total_cents == A_STANDALONE * 2


def test_b_alone_uses_the_standalone_rate(auditor, matcher):
    occ = invoice("INV-B-2", [line("L-B2", "INV-B-2", 1, BUNDLE_B, 3, B_STANDALONE, service_date=BUNDLE_DAY)])
    assert not audit(auditor, matcher, [occ])["INV-B-2"].flagged


def test_a_and_b_same_patient_same_day_substitutes_both_rates(auditor, matcher):
    occ = invoice(
        "INV-B-3",
        [
            line("L-B3-1", "INV-B-3", 1, BUNDLE_A, 2, A_BUNDLED, service_date=BUNDLE_DAY),
            line("L-B3-2", "INV-B-3", 2, BUNDLE_B, 3, B_BUNDLED, service_date=BUNDLE_DAY),
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-B-3"]
    assert not result.flagged
    assert result.expected_total_cents == A_BUNDLED * 2 + B_BUNDLED * 3


def test_bundle_not_applied_is_detected_and_named(auditor, matcher):
    occ = invoice(
        "INV-B-4",
        [
            line("L-B4-1", "INV-B-4", 1, BUNDLE_A, 1, A_STANDALONE, service_date=BUNDLE_DAY),
            line("L-B4-2", "INV-B-4", 2, BUNDLE_B, 1, B_STANDALONE, service_date=BUNDLE_DAY),
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-B-4"]
    assert categories(result) == {"bundle_not_applied"}
    assert result.expected_total_cents == A_BUNDLED + B_BUNDLED


def test_a_and_b_on_different_days_is_not_a_bundle(auditor, matcher):
    occ = invoice(
        "INV-B-5",
        [
            line("L-B5-1", "INV-B-5", 1, BUNDLE_A, 1, A_STANDALONE, service_date=BUNDLE_DAY),
            line("L-B5-2", "INV-B-5", 2, BUNDLE_B, 1, B_STANDALONE, service_date=BUNDLE_OTHER_DAY),
        ],
    )
    assert not audit(auditor, matcher, [occ])["INV-B-5"].flagged


def test_a_and_b_for_different_patients_is_not_a_bundle(auditor, matcher):
    a = invoice(
        "INV-B-6",
        [line("L-B6-1", "INV-B-6", 1, BUNDLE_A, 1, A_STANDALONE, service_date=BUNDLE_DAY)],
        patient_id="PT-TEST-1",
    )
    b = invoice(
        "INV-B-7",
        [line("L-B7-1", "INV-B-7", 1, BUNDLE_B, 1, B_STANDALONE, service_date=BUNDLE_DAY)],
        patient_id="PT-TEST-2",
    )
    results = audit(auditor, matcher, [a, b])
    assert not results["INV-B-6"].flagged
    assert not results["INV-B-7"].flagged


def test_the_bundle_spans_invoices(auditor, matcher):
    """Clause 9.1 is about the patient's day, not about one invoice."""
    a = invoice(
        "INV-B-8", [line("L-B8-1", "INV-B-8", 1, BUNDLE_A, 1, A_BUNDLED, service_date=BUNDLE_DAY)]
    )
    b = invoice(
        "INV-B-9",
        [line("L-B9-1", "INV-B-9", 1, BUNDLE_B, 1, B_BUNDLED, service_date=BUNDLE_DAY)],
        invoice_date="2024-08-01",
    )
    results = audit(auditor, matcher, [a, b])
    assert not results["INV-B-8"].flagged
    assert not results["INV-B-9"].flagged


def test_bundled_rate_applied_without_the_partner_is_detected(auditor, matcher):
    occ = invoice("INV-B-10", [line("L-B10", "INV-B-10", 1, BUNDLE_A, 1, A_BUNDLED, service_date=BUNDLE_DAY)])
    result = audit(auditor, matcher, [occ])["INV-B-10"]
    assert categories(result) == {"bundle_incorrectly_applied"}
    assert result.expected_total_cents == A_STANDALONE


# ==========================================================================
# Exclusion windows
# ==========================================================================
#
# Section 10 exclusion windows: measured in either direction, inclusive.

EXCLUDED = "Excluded Neurological Biopsy Procedure"
TRIGGER = "Trigger Pulmonary Telemetry Monitoring"
EXCLUDED_RATE = 90000
TRIGGER_RATE = 6000
WINDOW = 7
ANCHOR = _dt.date(2024, 6, 10)


def _pair(offset_days: int, *, patient="PT-TEST-1"):
    excluded_date = ANCHOR + _dt.timedelta(days=offset_days)
    return invoice(
        "INV-E",
        [
            line("L-E-01", "INV-E", 1, TRIGGER, 1, TRIGGER_RATE, service_date=ANCHOR.isoformat()),
            line(
                "L-E-02", "INV-E", 2, EXCLUDED, 1, EXCLUDED_RATE,
                service_date=excluded_date.isoformat(),
            ),
        ],
        patient_id=patient,
        invoice_date="2024-08-01",
    )


@pytest.mark.parametrize("offset", [0, 1, WINDOW - 1, WINDOW])
def test_inside_the_window_in_either_direction(auditor, matcher, offset):
    for signed in {offset, -offset}:
        result = audit(auditor, matcher, [_pair(signed)])["INV-E"]
        assert "exclusion_window_violation" in categories(result), signed


@pytest.mark.parametrize("offset", [WINDOW + 1, WINDOW + 30, -(WINDOW + 1)])
def test_outside_the_window_is_clean(auditor, matcher, offset):
    result = audit(auditor, matcher, [_pair(offset)])["INV-E"]
    assert not result.flagged, offset


def test_the_boundary_day_is_inside(auditor, matcher):
    """"Not billable within 7 days" is read inclusively; day 7 is within.

    This is a recorded reading, not a derivation -- the contract does not say
    whether the boundary day counts.  See ``AuditPolicy`` and the decision log.
    """
    assert "exclusion_window_violation" in categories(
        audit(auditor, matcher, [_pair(WINDOW)])["INV-E"]
    )
    assert not audit(auditor, matcher, [_pair(WINDOW + 1)])["INV-E"].flagged


def test_the_excluded_line_is_not_payable(auditor, matcher):
    result = audit(auditor, matcher, [_pair(1)])["INV-E"]
    assert result.expected_total_cents == TRIGGER_RATE


def test_the_window_is_per_patient(auditor, matcher):
    """A different patient's telemetry does not exclude this patient's biopsy."""
    trigger = invoice(
        "INV-E-1",
        [line("L-E1-1", "INV-E-1", 1, TRIGGER, 1, TRIGGER_RATE, service_date="2024-06-10")],
        patient_id="PT-TEST-2",
        invoice_date="2024-08-01",
    )
    excluded = invoice(
        "INV-E-2",
        [line("L-E2-1", "INV-E-2", 1, EXCLUDED, 1, EXCLUDED_RATE, service_date="2024-06-11")],
        patient_id="PT-TEST-1",
        invoice_date="2024-08-01",
    )
    results = audit(auditor, matcher, [trigger, excluded])
    assert not results["INV-E-2"].flagged


def test_the_window_spans_invoices(auditor, matcher):
    trigger = invoice(
        "INV-E-3",
        [line("L-E3-1", "INV-E-3", 1, TRIGGER, 1, TRIGGER_RATE, service_date="2024-06-10")],
        invoice_date="2024-07-01",
    )
    excluded = invoice(
        "INV-E-4",
        [line("L-E4-1", "INV-E-4", 1, EXCLUDED, 1, EXCLUDED_RATE, service_date="2024-06-12")],
        invoice_date="2024-08-01",
    )
    results = audit(auditor, matcher, [trigger, excluded])
    assert "exclusion_window_violation" in categories(results["INV-E-4"])
    # The *trigger* service is not the one the contract makes unbillable.
    assert not results["INV-E-3"].flagged


def test_only_the_left_hand_service_becomes_unbillable(auditor, matcher):
    """The table is directional even though the window is symmetric in time."""
    occ = invoice(
        "INV-E-5",
        [
            line("L-E5-1", "INV-E-5", 1, TRIGGER, 2, TRIGGER_RATE, service_date="2024-06-10"),
            line("L-E5-2", "INV-E-5", 2, TRIGGER, 2, TRIGGER_RATE, service_date="2024-06-12"),
        ],
        invoice_date="2024-08-01",
    )
    assert not audit(auditor, matcher, [occ])["INV-E-5"].flagged


# ==========================================================================
# Duplicates
# ==========================================================================
#
# Clause 11.2 (reused identifiers) and 11.4 (the same service billed twice).

DUP_SVC = "Solitary Geriatric Wound Care"
DUP_RATE = 1700
DUP_DAY = "2024-06-03"


def test_same_service_same_day_twice_on_one_invoice(auditor, matcher):
    occ = invoice(
        "INV-U-1",
        [
            line("L-U1-1", "INV-U-1", 1, DUP_SVC, 2, DUP_RATE, service_date=DUP_DAY),
            line("L-U1-2", "INV-U-1", 2, DUP_SVC, 2, DUP_RATE, service_date=DUP_DAY),
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-U-1"]
    assert "duplicate_service" in categories(result)
    # The first billing stands; the repeat is not payable.
    assert result.expected_total_cents == DUP_RATE * 2


def test_same_service_same_day_across_invoices(auditor, matcher):
    first = invoice(
        "INV-U-2",
        [line("L-U2-1", "INV-U-2", 1, DUP_SVC, 1, DUP_RATE, service_date=DUP_DAY)],
        invoice_date="2024-07-01",
    )
    second = invoice(
        "INV-U-3",
        [line("L-U3-1", "INV-U-3", 1, DUP_SVC, 1, DUP_RATE, service_date=DUP_DAY)],
        invoice_date="2024-08-01",
    )
    results = audit(auditor, matcher, [first, second])
    assert not results["INV-U-2"].flagged
    assert "cross_invoice_duplicate" in categories(results["INV-U-3"])
    assert results["INV-U-3"].expected_total_cents == 0


def test_different_patients_are_not_duplicates(auditor, matcher):
    a = invoice(
        "INV-U-4",
        [line("L-U4-1", "INV-U-4", 1, DUP_SVC, 1, DUP_RATE, service_date=DUP_DAY)],
        patient_id="PT-TEST-1",
    )
    b = invoice(
        "INV-U-5",
        [line("L-U5-1", "INV-U-5", 1, DUP_SVC, 1, DUP_RATE, service_date=DUP_DAY)],
        patient_id="PT-TEST-2",
    )
    results = audit(auditor, matcher, [a, b])
    assert not results["INV-U-4"].flagged
    assert not results["INV-U-5"].flagged


def test_different_service_days_are_not_duplicates(auditor, matcher):
    occ = invoice(
        "INV-U-6",
        [
            line("L-U6-1", "INV-U-6", 1, DUP_SVC, 1, DUP_RATE, service_date="2024-06-03"),
            line("L-U6-2", "INV-U-6", 2, DUP_SVC, 1, DUP_RATE, service_date="2024-06-04"),
        ],
    )
    assert not audit(auditor, matcher, [occ])["INV-U-6"].flagged


def test_reused_invoice_identifier_is_two_physical_occurrences(auditor, matcher):
    """Clause 11.2.

    Two records share an identifier.  They are different invoices for
    different patients; collapsing them would lose a whole invoice's lines.
    """
    first = invoice(
        "INV-U-7",
        [line("L-U7-1", "INV-U-7", 1, DUP_SVC, 1, DUP_RATE, service_date="2024-06-03")],
        patient_id="PT-TEST-1",
        invoice_date="2024-07-01",
        occurrence_index=0,
    )
    reuse = invoice(
        "INV-U-7",
        [line("L-U8-1", "INV-U-7", 1, DUP_SVC, 3, DUP_RATE, service_date="2024-06-20")],
        patient_id="PT-TEST-2",
        invoice_date="2024-08-01",
        occurrence_index=1,
    )
    result = audit(auditor, matcher, [first, reuse])["INV-U-7"]
    assert "duplicate_service" not in categories(result)
    assert "cross_invoice_duplicate" not in categories(result)
    assert categories(result) == {"duplicate_invoice_id"}
    assert len(result.occurrence_ids) == 2
    # The reusing record is the subject of the claim.
    assert result.billed_total_cents == DUP_RATE * 3
    assert result.expected_total_cents == DUP_RATE * 3


def test_a_finding_on_the_superseded_record_still_surfaces(auditor, matcher):
    first = invoice(
        "INV-U-9",
        [line("L-U9-1", "INV-U-9", 1, DUP_SVC, 1, DUP_RATE, service_date="2024-06-03")],
        contract_number="INS-WRONG-9999",
        occurrence_index=0,
    )
    reuse = invoice(
        "INV-U-9",
        [line("L-U10-1", "INV-U-9", 1, DUP_SVC, 1, DUP_RATE, service_date="2024-06-20")],
        invoice_date="2024-08-01",
        occurrence_index=1,
    )
    result = audit(auditor, matcher, [first, reuse])["INV-U-9"]
    assert categories(result) == {"duplicate_invoice_id", "contract_number_mismatch"}


# ==========================================================================
# Whole invoices
# ==========================================================================
#
# End-to-end invoices against the synthetic contract.
#
# These exercise the whole path -- match, index, price, detect, reconstruct --
# on invoices whose correct answer is worked out by hand from the contract text
# in ``conftest.py``.  None of them is derived from a Hospital 1 label.

def test_a_wholly_correct_invoice_is_not_flagged(auditor, matcher):
    """Base rate, weekend uplift, bundle and discount all at once."""
    occ = invoice(
        "INV-S-1",
        [
            line("L-S1-1", "INV-S-1", 1, "Routine Hepatic Infusion Therapy", 3, 10000,
                 service_date="2024-06-03"),
            line("L-S1-2", "INV-S-1", 2, "Weekend Vascular Transport Service", 1, 22000,
                 service_date="2024-06-08"),          # Saturday, +10%
            line("L-S1-3", "INV-S-1", 3, "Bundled Alpha Theatre Time", 2, 30000,
                 service_date="2024-06-05"),
            line("L-S1-4", "INV-S-1", 4, "Bundled Beta Specimen Analysis", 1, 9000,
                 service_date="2024-06-05"),
        ],
        invoice_date="2024-07-01",
    )
    result = audit(auditor, matcher, [occ])["INV-S-1"]
    assert not result.flagged
    assert result.expected_total_cents == 30000 + 22000 + 60000 + 9000
    assert result.expected_total_cents == result.billed_total_cents
    assert result.confidence_band == "high"


def test_structural_defects_do_not_change_the_money(auditor, matcher):
    occ = invoice(
        "INV-S-2",
        [line("L-S2-1", "INV-S-2", 1, "Routine Hepatic Infusion Therapy", 1, 10000)],
        contract_number="INS-SOMEONE-ELSE",
    )
    result = audit(auditor, matcher, [occ])["INV-S-2"]
    assert categories(result) == {"contract_number_mismatch"}
    assert result.expected_total_cents == result.billed_total_cents == 10000
    assert result.confidence_band == "high"


def test_line_arithmetic_and_invoice_total_are_separate_findings(auditor, matcher):
    occ = invoice(
        "INV-S-3",
        [
            line("L-S3-1", "INV-S-3", 1, "Routine Hepatic Infusion Therapy", 2, 10000,
                 line_total_cents=19999),
        ],
        invoice_total_cents=12345,
    )
    result = audit(auditor, matcher, [occ])["INV-S-3"]
    assert categories(result) == {"line_total_arithmetic", "invoice_total_mismatch"}
    # The corrected figure is built from the contract, not from either wrong number.
    assert result.expected_total_cents == 20000


def test_a_malformed_service_date_blocks_reconstruction(auditor, matcher):
    occ = invoice(
        "INV-S-4",
        [
            line("L-S4-1", "INV-S-4", 1, "Volume Ophthalmic Specimen Analysis", 1, 25000,
                 service_date="2024-02-31"),
        ],
    )
    result = audit(auditor, matcher, [occ])["INV-S-4"]
    assert "malformed_service_date" in categories(result)
    assert result.confidence_band == "low"


def test_a_service_date_outside_the_term(auditor, matcher):
    occ = invoice(
        "INV-S-5",
        [line("L-S5-1", "INV-S-5", 1, "Routine Hepatic Infusion Therapy", 1, 10000,
              service_date="2023-12-31")],
    )
    result = audit(auditor, matcher, [occ])["INV-S-5"]
    assert "service_date_out_of_window" in categories(result)


def test_a_service_date_after_the_invoice_date(auditor, matcher):
    occ = invoice(
        "INV-S-6",
        [line("L-S6-1", "INV-S-6", 1, "Routine Hepatic Infusion Therapy", 1, 10000,
              service_date="2024-06-10")],
        invoice_date="2024-06-01",
    )
    result = audit(auditor, matcher, [occ])["INV-S-6"]
    assert categories(result) == {"service_date_after_invoice_date"}
    assert result.expected_total_cents == 10000


def test_out_of_term_does_not_also_report_after_invoice_date(auditor, matcher):
    """Clause 11.3 states both requirements together; this is one defect."""
    occ = invoice(
        "INV-S-7",
        [line("L-S7-1", "INV-S-7", 1, "Routine Hepatic Infusion Therapy", 1, 10000,
              service_date="2026-01-01")],
        invoice_date="2024-06-01",
    )
    result = audit(auditor, matcher, [occ])["INV-S-7"]
    assert categories(result) == {"service_date_out_of_window"}


def test_a_plain_wrong_rate_is_reported_as_such(auditor, matcher):
    """No single adjustment explains the figure, so it is not named as one."""
    occ = invoice(
        "INV-S-8",
        [line("L-S8-1", "INV-S-8", 1, "Routine Hepatic Infusion Therapy", 2, 13337)],
    )
    result = audit(auditor, matcher, [occ])["INV-S-8"]
    assert categories(result) == {"unit_price_mismatch"}
    assert result.expected_total_cents == 20000


def test_several_independent_defects_on_one_invoice(auditor, matcher):
    occ = invoice(
        "INV-S-9",
        [
            line("L-S9-1", "INV-S-9", 1, "Routine Hepatic Infusion Therapy", 1, 99999),
            line("L-S9-2", "INV-S-9", 2, "Weekend Vascular Transport Service", 1, 20000,
                 service_date="2024-06-09"),     # Sunday, uplift omitted
            line("L-S9-3", "INV-S-9", 3, "Interpretive Dance Session", 1, 500),
        ],
        contract_number="INS-SOMEONE-ELSE",
        invoice_date="2024-07-01",
    )
    result = audit(auditor, matcher, [occ])["INV-S-9"]
    assert categories(result) == {
        "contract_number_mismatch",
        "unit_price_mismatch",
        "premium_omitted",
        "unknown_service",
    }
    assert result.correction_reconstructable is False


def test_the_pricing_trace_explains_the_figure(auditor, matcher, engine):
    """Every corrected amount must be derivable from a readable trace."""
    from tests.hospital_1.conftest import build_context

    occ = invoice(
        "INV-S-10",
        [line("L-S10-1", "INV-S-10", 1, "Weekend Vascular Transport Service", 3, 22000,
              service_date="2024-06-08")],
        invoice_date="2024-07-01",
    )
    ctx, by_line = build_context(auditor, matcher, [occ])
    priced = engine.price_line(by_line["L-S10-1"], "Weekend Vascular Transport Service", ctx)
    labels = [step.label for step in priced.trace]
    values = [step.unit_rate_cents for step in priced.trace]
    assert labels[0] == "base rate" and values[0] == 20000
    assert any("non-business-day uplift" in l for l in labels)
    assert values[-1] == 66000
    assert priced.trace_text().splitlines()[0] == "base rate: 20000"


# ==========================================================================
# Hospital 1 source data
# ==========================================================================

def test_hospital_1_jsonl_and_csv_agree():
    """Licenses the decision to treat the JSONL as canonical."""
    occurrences = load_occurrences(ROOT / "invoices" / "hospital_1_invoices.jsonl")
    problems = cross_check_against_csv(
        occurrences,
        ROOT / "invoices" / "hospital_1_invoices.csv",
        ROOT / "invoices" / "hospital_1_line_items.csv",
    )
    assert problems == []


def test_hospital_1_has_more_occurrences_than_identifiers():
    occurrences = load_occurrences(ROOT / "invoices" / "hospital_1_invoices.jsonl")
    ids = {o.invoice_id for o in occurrences}
    assert len(occurrences) > len(ids), "no reused identifiers found; check the loader"
    reused = [o for o in occurrences if o.occurrence_index > 0]
    assert all(o.occurrence_id.endswith("#1") for o in reused)
