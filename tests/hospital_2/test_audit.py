"""Hospital 2 deterministic audit against the real contract and synthetic invoices.

Every expected figure is worked out by hand from the contract's own rates.
"""

from __future__ import annotations

import csv
import datetime as _dt
from decimal import Decimal

import pytest

from src.hospital_2.audit import AuditContext, PricingEngine, resolve_lines
from src.shared.data import parse_iso_date
from tests.hospital_2.conftest import (
    BUNDLE_A, BUNDLE_B, CAPPED, COMPOUND, DAILY, DISCOUNT, EXCLUDED, MONDAY, PLAIN,
    SATURDAY, SUNDAY, TRIGGER, WEEKEND, cats, invoice, line, mappings_for, run, svc_line,
)

D0 = _dt.date(2024, 6, 3)


def day(n: int) -> str:
    return (D0 + _dt.timedelta(days=n)).isoformat()


# -- rounding and pricing order ------------------------------------------------------

def test_half_up_rounding_on_a_daily_aggregate_uplift(contract, matcher):
    """52775 x 1.30 = 68607.5 -> 68608: an exact half rounds up (clause 3.1)."""
    ok = invoice("INV-R1", [svc_line(contract, "L-R1", DAILY, 13, 68608)])
    r = run(contract, matcher, [ok])["INV-R1"]
    assert not r.result.flagged
    assert r.result.expected_total_cents == 68608 * 13


def test_half_up_rounding_on_a_volume_discount(contract, matcher):
    """9675 x 0.90 = 8707.5 -> 8708."""
    lines = [svc_line(contract, "L-D1", DISCOUNT, 61, date=day(0)),
             svc_line(contract, "L-D2", DISCOUNT, 1, 8708, date=day(1))]
    r = run(contract, matcher, [invoice("INV-R2", lines, invoice_date="2024-07-01")])["INV-R2"]
    assert not r.result.flagged


def test_pricing_order_and_per_step_rounding(contract, matcher):
    """Clause 3.2: bundle substitution, facility, plan tier, uplift, discount --
    each rounded -- then times quantity."""
    occ = invoice("INV-O", [svc_line(contract, "L-O", BUNDLE_A, 3)])
    resolved = resolve_lines([occ], matcher, mappings_for(contract, matcher, [occ]))
    engine = PricingEngine(contract)
    rl = resolved[0]
    si = engine.stage_inputs(rl, BUNDLE_A, AuditContext(resolved))
    t = engine.compute(rl, BUNDLE_A, si, bundle=True, uplift=Decimal("0.125"), discount=Decimal("0.075"))
    assert t["base_rate_cents"] == 33275
    assert t["after_bundle_cents"] == 27275                 # substituted first
    assert t["after_facility_cents"] == t["after_plan_cents"] == 27275
    assert t["after_uplift_cents"] == 30684                 # 30684.375 -> 30684
    assert t["effective_unit_rate_cents"] == 28383          # 28382.7   -> 28383
    assert t["expected_line_total_cents"] == 28383 * 3
    assert t["source_clause_ids"][:2] == ["14.2", "16.4"]


def test_no_floating_point_money(contract, matcher):
    occ = invoice("INV-F", [svc_line(contract, "L-F1", DAILY, 13, 68608),
                            svc_line(contract, "L-F2", WEEKEND, 2, 6636, date=SATURDAY)])
    r = run(contract, matcher, [occ])["INV-F"]
    for value in (r.result.expected_total_cents, r.result.billed_total_cents,
                  r.result.maximum_contractually_payable_total_cents):
        assert type(value) is int
    for trace in r.occurrences[0].traces:
        for k, v in trace.items():
            if k.endswith("_cents") and v is not None:
                assert type(v) is int, k


# -- base pricing and unit basis ------------------------------------------------------

def test_correct_base_price_is_clean(contract, matcher):
    r = run(contract, matcher, [invoice("INV-B", [svc_line(contract, "L-B", PLAIN, 2)])])["INV-B"]
    assert not r.result.flagged and r.result.expected_total_cents == 141675 * 2
    assert r.result.correction_reconstructable and r.result.confidence_band == "high"


def test_wrong_price_is_named_as_a_plain_mismatch(contract, matcher):
    r = run(contract, matcher, [invoice("INV-P", [svc_line(contract, "L-P", PLAIN, 1, 123456)])])["INV-P"]
    assert cats(r) == {"unit_price_mismatch"}
    assert r.result.expected_total_cents == 141675


def test_clause_4_2_compound_basis(contract, matcher):
    ok = invoice("INV-C1", [svc_line(contract, "L-C1", COMPOUND, 3)])
    bad = invoice("INV-C2", [svc_line(contract, "L-C2", COMPOUND, 3, basis="per_hour")])
    res = run(contract, matcher, [ok, bad])
    assert not res["INV-C1"].result.flagged
    assert cats(res["INV-C2"]) == {"wrong_unit_basis"}


# -- uplifts -----------------------------------------------------------------------------

@pytest.mark.parametrize("date,price,expected", [
    (MONDAY, 5925, set()),
    (SATURDAY, 6636, set()),
    (SUNDAY, 6636, set()),
    (SUNDAY, 5925, {"premium_omitted"}),
    (MONDAY, 6636, {"premium_incorrectly_applied"}),
])
def test_non_business_day_uplift(contract, matcher, date, price, expected):
    r = run(contract, matcher, [invoice("INV-W", [svc_line(contract, "L-W", WEEKEND, 1, price, date=date)])])["INV-W"]
    assert cats(r) == expected


@pytest.mark.parametrize("qty,price,expected", [
    (12, 52775, set()),                       # 12 does not exceed 12
    (13, 68608, set()),
    (13, 52775, {"premium_omitted"}),
    (12, 68608, {"premium_incorrectly_applied"}),
])
def test_daily_aggregate_uplift_threshold(contract, matcher, qty, price, expected):
    r = run(contract, matcher, [invoice("INV-A", [svc_line(contract, "L-A", DAILY, qty, price)])])["INV-A"]
    assert cats(r) == expected


def test_daily_aggregates_span_invoices(contract, matcher):
    """Clause 3.4: 7 + 6 nights for one patient on one Service Day exceed 12,
    though neither invoice alone does."""
    a = invoice("INV-A1", [svc_line(contract, "L-A1", DAILY, 7, 68608)])
    b = invoice("INV-A2", [svc_line(contract, "L-A2", DAILY, 6, 68608)], invoice_date="2024-07-02")
    res = run(contract, matcher, [a, b])
    assert not res["INV-A1"].result.flagged and not res["INV-A2"].result.flagged
    other_patient = invoice("INV-A3", [svc_line(contract, "L-A3", DAILY, 6, 68608)], patient="PT-2")
    assert cats(run(contract, matcher, [a, other_patient])["INV-A3"]) == {"premium_incorrectly_applied"}


# -- cumulative discounts ---------------------------------------------------------------

def test_cumulative_discounts_span_all_patients(contract, matcher):
    occs = [
        invoice("INV-U1", [svc_line(contract, "L-U1", DISCOUNT, 60, date=day(0))], patient="PT-1"),
        invoice("INV-U2", [svc_line(contract, "L-U2", DISCOUNT, 1, date=day(1))], patient="PT-2"),
        invoice("INV-U3", [svc_line(contract, "L-U3", DISCOUNT, 1, 8708, date=day(2))], patient="PT-3"),
        invoice("INV-U4", [svc_line(contract, "L-U4", DISCOUNT, 1, 9675, date=day(3))], patient="PT-4"),
    ]
    res = run(contract, matcher, occs)
    assert not res["INV-U2"].result.flagged, "prior 60 does not exceed 60"
    assert not res["INV-U3"].result.flagged, "prior 61 exceeds 60: 10% applies"
    assert cats(res["INV-U4"]) == {"volume_discount_omitted"}


def test_same_date_cumulative_ordering_uses_line_id(contract, matcher):
    """All on one date.  By line id, L-2 sees 30 and L-3 sees 61, so only L-3
    is discounted -- even though L-3 is on the first invoice in file order."""
    occs = [
        invoice("INV-S3", [svc_line(contract, "L-3", DISCOUNT, 1, 8708)], patient="PT-3"),
        invoice("INV-S1", [svc_line(contract, "L-1", DISCOUNT, 30)], patient="PT-1"),
        invoice("INV-S2", [svc_line(contract, "L-2", DISCOUNT, 31)], patient="PT-2"),
    ]
    res = run(contract, matcher, occs)
    assert not any(r.result.flagged for r in res.values())


def test_the_line_crossing_a_threshold_is_not_split(contract, matcher):
    """Prior 50, this line 20: the whole line is undiscounted (prior does not exceed 60)."""
    occs = [invoice("INV-X", [svc_line(contract, "L-X1", DISCOUNT, 50, date=day(0)),
                              svc_line(contract, "L-X2", DISCOUNT, 20, date=day(1))])]
    assert not run(contract, matcher, occs)["INV-X"].result.flagged


# -- bundles, caps, exclusions -----------------------------------------------------------

def test_bundles_span_invoices(contract, matcher):
    a = invoice("INV-B1", [svc_line(contract, "L-B1", BUNDLE_A, 1, 27275)])
    b = invoice("INV-B2", [svc_line(contract, "L-B2", BUNDLE_B, 1, 73625)], invoice_date="2024-07-05")
    res = run(contract, matcher, [a, b])
    assert not res["INV-B1"].result.flagged and not res["INV-B2"].result.flagged
    a2 = invoice("INV-B3", [svc_line(contract, "L-B3", BUNDLE_A, 1)], patient="PT-9")
    b2 = invoice("INV-B4", [svc_line(contract, "L-B4", BUNDLE_B, 1)], patient="PT-9")
    res2 = run(contract, matcher, [a2, b2])
    assert cats(res2["INV-B3"]) == cats(res2["INV-B4"]) == {"bundle_not_applied"}
    alone = invoice("INV-B5", [svc_line(contract, "L-B5", BUNDLE_A, 1, 27275)])
    assert cats(run(contract, matcher, [alone])["INV-B5"]) == {"bundle_incorrectly_applied"}


@pytest.mark.parametrize("qty,flagged", [(5, False), (6, False), (7, True)])
def test_daily_cap(contract, matcher, qty, flagged):
    r = run(contract, matcher, [invoice("INV-K", [svc_line(contract, "L-K", CAPPED, qty)])])["INV-K"]
    assert ("daily_cap_exceeded" in cats(r)) is flagged
    if flagged:
        # Detected for certain; the delivered quantity is not known.
        assert r.result.expected_total_cents is None
        assert r.result.maximum_contractually_payable_total_cents == 237000 * 6
        assert not r.result.pricing_complete and not r.result.correction_reconstructable


@pytest.mark.parametrize("offset,violation", [
    (0, True), (1, True), (7, True), (-7, True), (-1, True), (8, False), (-8, False), (30, False),
])
def test_exclusion_windows_span_invoices_in_both_directions(contract, matcher, offset, violation):
    excluded = invoice("INV-E1", [svc_line(contract, "L-E1", EXCLUDED, 1, date=day(10))],
                       invoice_date="2024-08-01")
    trigger = invoice("INV-E2", [svc_line(contract, "L-E2", TRIGGER, 1, date=day(10 + offset))],
                      invoice_date="2024-08-02")
    res = run(contract, matcher, [excluded, trigger])
    assert ("exclusion_window_violation" in cats(res["INV-E1"])) is violation
    assert not res["INV-E2"].result.flagged, "the trigger service is never the excluded one"
    if violation:
        assert res["INV-E1"].result.expected_total_cents == 0


def test_exclusion_is_per_patient(contract, matcher):
    excluded = invoice("INV-E3", [svc_line(contract, "L-E3", EXCLUDED, 1)], patient="PT-1")
    trigger = invoice("INV-E4", [svc_line(contract, "L-E4", TRIGGER, 1)], patient="PT-2")
    assert not run(contract, matcher, [excluded, trigger])["INV-E3"].result.flagged


# -- invoice-level checks -----------------------------------------------------------------

def test_duplicate_invoice_occurrences_remain_distinct(contract, matcher):
    first = invoice("INV-DUP", [svc_line(contract, "L-D1", PLAIN, 1)], patient="PT-1",
                    invoice_date="2024-07-01", index=0)
    second = invoice("INV-DUP", [svc_line(contract, "L-D9", PLAIN, 2)], patient="PT-2",
                     invoice_date="2024-08-15", index=1)
    r = run(contract, matcher, [first, second])["INV-DUP"]
    assert r.result.occurrence_ids == ["INV-DUP#0", "INV-DUP#1"]
    assert [a.occurrence.patient_id for a in r.occurrences] == ["PT-1", "PT-2"]
    assert [[rl.line_id for rl in a.lines] for a in r.occurrences] == [["L-D1"], ["L-D9"]]
    assert cats(r) == {"duplicate_invoice_id"}
    # The reusing record is the subject of the claim.
    assert r.result.billed_total_cents == r.result.expected_total_cents == 141675 * 2
    dup = [f for f in r.occurrences[1].findings if f.category == "duplicate_invoice_id"]
    assert len(dup) == 1 and not [f for f in r.occurrences[0].findings if f.category == "duplicate_invoice_id"]


@pytest.mark.parametrize("invoice_date,late", [("2024-08-29", False), ("2024-08-30", True)])
def test_sixty_day_submission_rule(contract, matcher, invoice_date, late):
    occ = invoice("INV-L", [svc_line(contract, "L-L", PLAIN, 1)], discharge="2024-06-30",
                  invoice_date=invoice_date)
    assert ("late_invoice_submission" in cats(run(contract, matcher, [occ])["INV-L"])) is late


def test_contract_number_and_facility(contract, matcher):
    occ = invoice("INV-N", [svc_line(contract, "L-N", PLAIN, 1)],
                  contract_number="INS-H9-2024-0000", facility="F-OTHER")
    r = run(contract, matcher, [occ])["INV-N"]
    assert cats(r) == {"contract_number_mismatch", "facility_mismatch"}
    assert r.result.expected_total_cents == 141675 and r.result.confidence_band == "high"


def test_arithmetic_findings(contract, matcher):
    occ = invoice("INV-M", [svc_line(contract, "L-M", PLAIN, 2, total=1)], total=5)
    assert cats(run(contract, matcher, [occ])["INV-M"]) == {"line_total_arithmetic", "invoice_total_mismatch"}


# -- dates ---------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["2024-13-05", "31/02/2024", "2025-06-31", "not-a-date", "2025-02-30"])
def test_malformed_dates_stay_malformed(contract, matcher, raw):
    assert parse_iso_date(raw) is None
    occ = invoice("INV-MD", [svc_line(contract, "L-MD", PLAIN, 1, date=raw)])
    r = run(contract, matcher, [occ])["INV-MD"]
    assert "malformed_service_date" in cats(r)
    assert r.occurrences[0].lines[0].service_date is None
    assert r.occurrences[0].lines[0].line.service_date_raw == raw
    assert not r.result.correction_reconstructable


def test_dates_outside_term_or_after_invoice(contract, matcher):
    out = invoice("INV-T1", [svc_line(contract, "L-T1", PLAIN, 1, date="2026-01-05")], invoice_date="2024-07-01")
    after = invoice("INV-T2", [svc_line(contract, "L-T2", PLAIN, 1, date="2024-07-05")], invoice_date="2024-07-01")
    res = run(contract, matcher, [out, after])
    assert cats(res["INV-T1"]) == {"service_date_out_of_contract"}
    assert cats(res["INV-T2"]) == {"service_date_after_invoice_date"}


# -- identity -------------------------------------------------------------------------------

def test_unknown_service_is_flagged_and_not_priced(contract, matcher):
    occ = invoice("INV-UK", [svc_line(contract, "L-UK1", PLAIN, 1),
                             line("L-UK2", "ONC WD BD OCC", 1, 50000, basis="per_day")])
    r = run(contract, matcher, [occ])["INV-UK"]
    assert "unknown_service" in cats(r)
    assert r.result.expected_total_cents is None
    assert not r.result.pricing_complete and not r.result.correction_reconstructable
    assert r.result.confidence_band == "low"


def test_unresolved_identity_propagates_into_cumulative_utilisation(contract, matcher):
    """118 certain units of a discounted service (12% once prior utilisation
    exceeds 120), then 5 units on a description whose only candidate is that
    service but which omits its specialty -- unresolved.  The next certain
    line's prior utilisation is 118..123: it may or may not be discounted.
    Neither reading is flagged, and the corrected total is not vouched for."""
    agep = "Advanced Gastrointestinal Endoscopic Procedure"
    assert [(t.exceeds_units, t.discount_fraction) for t in contract.volume_discounts[agep]] == [(120, Decimal("0.12"))]
    base = [
        invoice("INV-P1", [svc_line(contract, "L-P1", agep, 118, date=day(0))], patient="PT-1"),
        invoice("INV-P2", [line("L-P2", "ADV ENDOSCOPIC PROC", 5, 377750, date=day(1), basis="per_procedure")],
                patient="PT-2"),
    ]
    for price in (377750, 332420):                     # 377750 x 0.88 = 332420
        occs = base + [invoice("INV-P3", [svc_line(contract, "L-P3", agep, 1, price, date=day(2))], patient="PT-3")]
        res = run(contract, matcher, occs)
        assert res["INV-P2"].occurrences[0].lines[0].status == "AMBIGUOUS"
        r = res["INV-P3"]
        assert not r.result.flagged, price
        assert not r.result.correction_reconstructable
        assert any("straddling a discount threshold" in u for u in r.result.uncertainty_reasons)

    # Without the unresolved line the same discounted price is a certain error.
    certain = [base[0], invoice("INV-P3", [svc_line(contract, "L-P3", agep, 1, 332420, date=day(2))], patient="PT-3")]
    assert cats(run(contract, matcher, certain)["INV-P3"]) == {"volume_discount_incorrectly_applied"}


def test_an_unresolved_line_is_flagged_only_if_wrong_under_every_reading(contract, matcher):
    occ_ok = invoice("INV-Q1", [line("L-Q1", "ADV ENDOSCOPIC PROC", 1, 377750, basis="per_procedure")])
    occ_bad = invoice("INV-Q2", [line("L-Q2", "ADV ENDOSCOPIC PROC", 1, 1234, basis="per_procedure")])
    res = run(contract, matcher, [occ_ok, occ_bad])
    assert not res["INV-Q1"].result.flagged
    assert not res["INV-Q1"].result.correction_reconstructable, "identity is still unverified"
    assert cats(res["INV-Q2"]) == {"unit_price_mismatch"}
    assert res["INV-Q2"].result.expected_total_cents is None


# -- reused invoice numbers: what the submitted row represents ----------------------------

def _reused(contract, first_price):
    a = invoice("INV-R", [svc_line(contract, "L-RA", PLAIN, 1, first_price)], patient="PT-A",
                invoice_date="2024-07-01", index=0)
    b = invoice("INV-R", [svc_line(contract, "L-RB", PLAIN, 2)], patient="PT-B",
                invoice_date="2024-08-01", index=1)
    return a, b


def test_earlier_occurrence_findings_do_not_contaminate_the_submitted_row(contract, matcher, tmp_path):
    """Occurrence A carries its own pricing error; occurrence B only reuses the number."""
    a, b = _reused(contract, first_price=123456)
    h = run(contract, matcher, [a, b])["INV-R"]
    # Internally both occurrences stay separate, each with its own findings.
    assert [o.occurrence.occurrence_id for o in h.occurrences] == ["INV-R#0", "INV-R#1"]
    assert {f.category for f in h.occurrences[0].findings} == {"unit_price_mismatch"}
    assert {f.category for f in h.occurrences[1].findings} == {"duplicate_invoice_id"}
    # The submitted row is occurrence B, and only B.
    assert h.represented.occurrence.occurrence_id == "INV-R#1"
    assert cats(h) == {"duplicate_invoice_id"}
    assert h.result.billed_total_cents == 141675 * 2
    assert h.result.expected_total_cents == 141675 * 2
    assert h.result.correction_reconstructable
    # A's evidence is still written out, attributed to A.
    from src.hospital_2.audit import write_findings
    path = tmp_path / "findings.csv"
    write_findings([h], path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert {(r["occurrence_id"], r["category"]) for r in rows} == {
        ("INV-R#0", "unit_price_mismatch"), ("INV-R#1", "duplicate_invoice_id")}


def test_the_represented_occurrence_keeps_its_own_findings(contract, matcher):
    a, b = _reused(contract, first_price=141675)
    b = invoice("INV-R", [svc_line(contract, "L-RB", PLAIN, 2, 99999)], patient="PT-B",
                invoice_date="2024-08-01", index=1)
    h = run(contract, matcher, [a, b])["INV-R"]
    assert cats(h) == {"duplicate_invoice_id", "unit_price_mismatch"}
    assert h.result.billed_total_cents == 99999 * 2


# -- expected totals only when the correction is defensible --------------------------------

def _submission_row(results, tmp_path):
    from pathlib import Path
    from src.shared.submission import write_submission
    path = tmp_path / "submission.csv"
    root = Path(__file__).resolve().parents[2]
    write_submission([h.result for h in results], path, template=root / "submission_template.csv")
    text = path.read_text(encoding="utf-8")
    return text, list(csv.DictReader(text.splitlines()))


def test_unreconstructable_rows_submit_a_blank_expected_total(contract, matcher, tmp_path):
    unresolved = invoice("INV-X1", [line("L-X1", "ADV ENDOSCOPIC PROC", 1, 377750, basis="per_procedure")])
    capped = invoice("INV-X2", [svc_line(contract, "L-X2", CAPPED, 7)])
    unknown = invoice("INV-X3", [line("L-X3", "ONC WD BD OCC", 1, 5000, basis="per_day")])
    exact = invoice("INV-X4", [svc_line(contract, "L-X4", PLAIN, 3)])
    res = run(contract, matcher, [unresolved, capped, unknown, exact])
    for inv in ("INV-X1", "INV-X2", "INV-X3"):
        assert not res[inv].result.correction_reconstructable
        assert res[inv].result.expected_total_cents is None
    assert res["INV-X4"].result.correction_reconstructable
    assert res["INV-X4"].result.expected_total_cents == 141675 * 3

    # The unresolved line matches its only candidate's rate: a provisional
    # total exists, but only as a diagnostic, never in the scored column.
    assert res["INV-X1"].provisional_expected_total_cents == 377750
    assert not res["INV-X1"].result.pricing_complete

    text, rows = _submission_row(list(res.values()), tmp_path)
    by_id = {r["invoice_id"]: r for r in rows}
    assert by_id["INV-X1"]["expected_total_cents"] == ""
    assert by_id["INV-X2"]["expected_total_cents"] == ""
    assert by_id["INV-X3"]["expected_total_cents"] == ""
    assert by_id["INV-X4"]["expected_total_cents"] == str(141675 * 3)
    for literal in ("None", "null", "NaN", "nan"):
        assert literal not in text


def test_predictions_keep_the_provisional_total_in_its_own_column(contract, matcher, tmp_path):
    from src.hospital_2.audit import write_predictions
    occ = invoice("INV-Y", [line("L-Y", "ADV ENDOSCOPIC PROC", 2, 377750, basis="per_procedure")])
    path = tmp_path / "predictions.csv"
    write_predictions(list(run(contract, matcher, [occ]).values()), path)
    row = next(csv.DictReader(path.open(encoding="utf-8")))
    assert row["expected_total_cents"] == ""
    assert row["provisional_expected_total_cents"] == str(377750 * 2)
    assert row["correction_reconstructable"] == "0" and row["pricing_complete"] == "0"
    assert "None" not in path.read_text(encoding="utf-8")


def test_semantic_resolution_restores_reconstructability(contract, matcher, tmp_path):
    """The same invoice before and after a verified mapping: no manual patch."""
    occ = [invoice("INV-Z", [line("L-Z", "ADV ENDOSCOPIC PROC", 2, 377750, basis="per_procedure"),
                             svc_line(contract, "L-Z2", PLAIN, 1)])]
    before = run(contract, matcher, occ)["INV-Z"]
    assert not before.result.correction_reconstructable
    assert before.result.expected_total_cents is None

    verified = {"ADV ENDOSCOPIC PROC": {
        "status": "MATCHED", "service": "Advanced Gastrointestinal Endoscopic Procedure",
        "source": "semantic_verified", "verified": True, "unresolved_reason": None}}
    after = run(contract, matcher, occ, overrides=verified)["INV-Z"]
    assert after.result.pricing_complete and after.result.correction_reconstructable
    assert after.result.expected_total_cents == 377750 * 2 + 141675
    _, rows = _submission_row([after], tmp_path)
    assert rows[0]["expected_total_cents"] == str(377750 * 2 + 141675)
