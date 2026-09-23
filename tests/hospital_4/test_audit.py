"""Hospital 4 audit: global state, interval uncertainty, reconstruction, outputs."""

import csv
import datetime as _dt
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from src.hospital_4.audit import (
    AuditPolicy, H4Auditor, H4Matcher, H4Pipeline, PricingEngine, StageInputs, audit_resolved, resolve_lines,
)
from src.hospital_4.contract import parse_contract
from src.shared.money import apply_percentage_change
from src.shared.submission import SUBMISSION_COLUMNS
from tests.hospital_4.conftest import (
    BUNDLE_A, BUNDLE_B, CAPPED, CONTRACT_NUMBER, DISCOUNT, EXCLUDED, PLAIN, PREMIUM, TRIGGER, TWO_TIER,
    billed, categories, invoice, line, line_trace, patched_contract, rate,
)

REPO = Path(__file__).resolve().parents[2]


def expected(result):
    return result.result.expected_total_cents


# -- bundles (Section 7) ---------------------------------------------------------------

def test_bundle_applies_to_both_services_across_invoices(contract, run):
    a = invoice("INV-A", [billed(contract, "LA", BUNDLE_A, 2, price=11875)], invoice_date="2024-07-01")
    b = invoice("INV-B", [billed(contract, "LB", BUNDLE_B, 3, price=4900)], invoice_date="2024-07-02")
    out = run(a, b)
    assert not out["INV-A"].result.flagged and not out["INV-B"].result.flagged
    assert expected(out["INV-A"]) == 2 * 11875 and expected(out["INV-B"]) == 3 * 4900


def test_bundle_not_applied_is_named_on_both_lines(contract, run):
    occ = invoice("INV-A", [billed(contract, "LA", BUNDLE_A, 2), billed(contract, "LB", BUNDLE_B, 3)])
    r = run(occ)["INV-A"]
    assert categories(r, "LA") == {"bundle_not_applied"} and categories(r, "LB") == {"bundle_not_applied"}
    assert expected(r) == 2 * 11875 + 3 * 4900


def test_bundle_is_not_assumed_from_a_possible_partner(contract, run):
    # "ther FOC inf" omits the specialty: it might be the bundle partner, so the
    # bundle state of the certain line is left open -- neither applied nor denied.
    occ = invoice("INV-A", [billed(contract, "LA", BUNDLE_A, 2),
                            line("LB", "ther FOC inf", 3, 5775, unit_basis="per_unit_dispensed")])
    r = run(occ)["INV-A"]
    assert line_trace(r, "LA")["pricing"]["bundle"]["state"] == "uncertain"
    assert categories(r, "LA") == set()                 # base rate is one of the open readings
    # LB's only contracted reading is the partner, whose bundled rate is 4900: it is
    # wrong under every contracted reading (and otherwise names no contracted service).
    assert categories(r, "LB") == {"bundle_not_applied"}
    assert expected(r) is None and not r.result.pricing_complete


# -- threshold premiums (Section 5) ----------------------------------------------------

def test_premium_uses_the_service_day_aggregate_within_an_invoice(contract, run):
    occ = invoice("INV-A", [billed(contract, "L1", PREMIUM, 4, price=4571), billed(contract, "L2", PREMIUM, 4, price=4571)])
    r = run(occ)["INV-A"]
    assert line_trace(r, "L1")["pricing"]["premium"]["state"] == "applies"
    assert line_trace(r, "L1")["pricing"]["patient_day_quantity"] == [8, 8]
    assert categories(r, "L1") == set()
    assert categories(r, "L2") == {"duplicate_service"}          # clause 11.3: the repeat pays nothing
    assert expected(r) == 4 * 4571


def test_premium_aggregates_across_invoices(contract, run):
    a = invoice("INV-A", [billed(contract, "L1", PREMIUM, 4, price=4571)], invoice_date="2024-07-01")
    b = invoice("INV-B", [billed(contract, "L2", PREMIUM, 4, price=4571)], invoice_date="2024-07-02")
    out = run(a, b)
    assert not out["INV-A"].result.flagged and expected(out["INV-A"]) == 4 * 4571
    assert categories(out["INV-B"]) == {"cross_invoice_duplicate"} and expected(out["INV-B"]) == 0


def test_premium_omitted_is_detected(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PREMIUM, 7)]))["INV-A"]
    assert categories(r) == {"premium_omitted"} and expected(r) == 7 * 4571


def test_premium_ambiguity_propagates(contract, run):
    # 5 certain units + 3 that might be the same service: [5, 8] straddles 6.
    occ = invoice("INV-A", [billed(contract, "L1", PREMIUM, 5),
                            line("L2", "hep INF therapy", 3, 3975, unit_basis="per_unit_dispensed")])
    r = run(occ)["INV-A"]
    t = line_trace(r, "L1")
    assert t["pricing"]["patient_day_quantity"] == [5, 8] and t["pricing"]["premium"]["state"] == "uncertain"
    assert not r.result.flagged                        # the billed rate is one of the readings
    assert expected(r) is None and not r.result.pricing_complete


# -- cumulative discounts (Section 8) --------------------------------------------------

def _history(contract, service, qty, date, invoice_id="INV-H", patient="PT-H"):
    return invoice(invoice_id, [billed(contract, f"{invoice_id}-L", service, qty, date=date)],
                   patient=patient, invoice_date="2024-07-01")


def test_discount_is_prior_exclusive_and_not_on_the_crossing_line(contract, run):
    hist = _history(contract, DISCOUNT, 119, "2024-06-01")
    crossing = invoice("INV-X", [billed(contract, "X1", DISCOUNT, 5, date="2024-06-02")], patient="PT-2")
    after = invoice("INV-Y", [billed(contract, "Y1", DISCOUNT, 1, date="2024-06-03", price=52155)], patient="PT-3")
    out = run(hist, crossing, after)
    assert line_trace(out["INV-X"], "X1")["pricing"]["prior_cumulative"] == [119, 119]
    assert not out["INV-X"].result.flagged and expected(out["INV-X"]) == 5 * 57950
    assert line_trace(out["INV-Y"], "Y1")["pricing"]["prior_cumulative"] == [124, 124]
    assert not out["INV-Y"].result.flagged and expected(out["INV-Y"]) == 52155


def test_discount_threshold_is_exceeded_not_reached(contract, run):
    hist = _history(contract, DISCOUNT, 120, "2024-06-01")
    at = invoice("INV-X", [billed(contract, "X1", DISCOUNT, 1, date="2024-06-02")], patient="PT-2")
    assert not run(hist, at)["INV-X"].result.flagged      # prior 120 does not exceed 120


def test_same_day_lines_count_in_line_identifier_order(contract, run):
    hist = _history(contract, DISCOUNT, 120, "2024-06-01")
    first = invoice("INV-X", [billed(contract, "Z-A", DISCOUNT, 1, date="2024-06-02")], patient="PT-2")
    second = invoice("INV-Y", [billed(contract, "Z-B", DISCOUNT, 1, date="2024-06-02", price=52155)], patient="PT-3")
    out = run(hist, first, second)
    assert line_trace(out["INV-Y"], "Z-B")["pricing"]["prior_cumulative"] == [121, 121]
    assert not out["INV-X"].result.flagged and not out["INV-Y"].result.flagged


def test_deeper_discount_replaces_the_shallower(contract, run):
    hist = _history(contract, TWO_TIER, 241, "2024-06-01")
    r = run(hist, invoice("INV-X", [billed(contract, "X1", TWO_TIER, 1, date="2024-06-02", price=110005)],
                          patient="PT-2"))["INV-X"]
    assert not r.result.flagged and expected(r) == 110005    # 157150 x 0.70, not x 0.85 x 0.70


def test_cumulative_ambiguity_interval_crossing(contract, run):
    hist = _history(contract, DISCOUNT, 115, "2024-06-01")
    # Advanced or Standard oncology ward bed; billed per_hour, so the basis cannot break the tie.
    maybe = invoice("INV-M", [line("M1", "ONC ward BED occ", 10, 57950, date="2024-06-02", unit_basis="per_hour")],
                    patient="PT-M")
    later = invoice("INV-X", [billed(contract, "X1", DISCOUNT, 1, date="2024-06-03")], patient="PT-2")
    r = run(hist, maybe, later)["INV-X"]
    t = line_trace(r, "X1")["pricing"]
    assert t["prior_cumulative"] == [115, 125] and t["discount"]["state"] == "uncertain"
    assert not r.result.flagged and expected(r) is None


def test_ambiguity_that_cannot_cross_the_threshold_leaves_pricing_certain(contract, run):
    hist = _history(contract, DISCOUNT, 50, "2024-06-01")
    maybe = invoice("INV-M", [line("M1", "ONC ward BED occ", 10, 57950, date="2024-06-02", unit_basis="per_hour")],
                    patient="PT-M")
    later = invoice("INV-X", [billed(contract, "X1", DISCOUNT, 1, date="2024-06-03")], patient="PT-2")
    r = run(hist, maybe, later)["INV-X"]
    assert line_trace(r, "X1")["pricing"]["discount"]["state"] == "does_not_apply"
    assert r.result.correction_reconstructable and expected(r) == 57950


# -- daily limits (Section 6) ----------------------------------------------------------

def test_daily_cap_on_a_single_line_prices_the_limit(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", CAPPED, 13)]))["INV-A"]
    assert categories(r) == {"daily_cap_exceeded"}
    assert r.result.correction_reconstructable and expected(r) == 8 * rate(contract, CAPPED)
    assert line_trace(r, "L1")["pricing"]["payable_quantity"] == 8


def test_daily_cap_alternative_policy_declines_to_reconstruct(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", CAPPED, 13)]),
            policy=AuditPolicy(cap_excess_priced_at_cap=False))["INV-A"]
    assert r.result.flagged and expected(r) is None
    assert r.result.maximum_contractually_payable_total_cents == 8 * rate(contract, CAPPED)


def test_h4_cap_policy_contrasts_with_h1(contract, run):
    """H4 clause 6.1 says the excess is not payable, so the limit is priced.
    H1's cap clause has no such sentence, and H1 declines to reconstruct.
    The two policies differ because the two texts differ."""
    from src.hospital_1 import audit as h1_audit

    h1_text = (REPO / "contracts" / "hospital_1" / "provider_services_agreement.md").read_text(encoding="utf-8")
    assert "not payable" in contract.conventions["6.1"]
    assert "Maximum billable units per Patient per Service Day" in h1_text
    assert "in excess of the limit is not payable" not in h1_text
    assert AuditPolicy().cap_excess_priced_at_cap is True
    assert h1_audit.AuditPolicy().cap_breach_blocks_reconstruction is True
    r = run(invoice("INV-A", [billed(contract, "L1", CAPPED, 13)]))["INV-A"]
    assert expected(r) == 8 * rate(contract, CAPPED) and r.result.correction_reconstructable
    f = next(f for f in r.represented.findings if f.category == "daily_cap_exceeded")
    assert not f.blocks_reconstruction


def test_daily_cap_across_lines_and_invoices(contract, run):
    a = invoice("INV-A", [billed(contract, "L1", CAPPED, 5)], invoice_date="2024-07-01")
    b = invoice("INV-B", [billed(contract, "L2", CAPPED, 6)], invoice_date="2024-07-02")
    out = run(a, b)
    assert categories(out["INV-A"]) == {"daily_cap_exceeded"}
    assert expected(out["INV-A"]) == 5 * rate(contract, CAPPED)          # its own quantity is within the limit
    assert categories(out["INV-B"]) == {"daily_cap_exceeded", "cross_invoice_duplicate"}
    assert expected(out["INV-B"]) == 0


def test_quantity_at_the_limit_is_fine(contract, run):
    assert not run(invoice("INV-A", [billed(contract, "L1", CAPPED, 8)]))["INV-A"].result.flagged


# -- repeats (clause 11.3) -------------------------------------------------------------

def test_repeat_within_an_invoice(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1), billed(contract, "L2", PLAIN, 1)]))["INV-A"]
    assert categories(r, "L2") == {"duplicate_service"} and categories(r, "L1") == set()
    assert expected(r) == rate(contract, PLAIN)


def test_repeat_across_invoices_the_first_billing_stands(contract, run):
    # INV-B has the earlier invoice date, so it is the first billing.
    a = invoice("INV-A", [billed(contract, "L1", PLAIN, 1)], invoice_date="2024-07-05")
    b = invoice("INV-B", [billed(contract, "L2", PLAIN, 1)], invoice_date="2024-07-01")
    out = run(a, b)
    assert not out["INV-B"].result.flagged
    assert categories(out["INV-A"]) == {"cross_invoice_duplicate"} and expected(out["INV-A"]) == 0


def test_different_patients_or_days_are_not_repeats(contract, run):
    a = invoice("INV-A", [billed(contract, "L1", PLAIN, 1)], patient="PT-1")
    b = invoice("INV-B", [billed(contract, "L2", PLAIN, 1)], patient="PT-2")
    c = invoice("INV-C", [billed(contract, "L3", PLAIN, 1, date="2024-06-04")], patient="PT-1")
    assert not any(r.result.flagged for r in run(a, b, c).values())


# -- exclusion windows (Section 9) -----------------------------------------------------

@pytest.mark.parametrize("trigger_date,excluded_date,violates", [
    ("2024-06-01", "2024-06-10", True),     # trigger earlier
    ("2024-06-20", "2024-06-10", True),     # trigger later: clause 9.1 "either direction"
    ("2024-06-01", "2024-06-22", True),     # exactly 21 days: inside (inclusive reading)
    ("2024-06-01", "2024-06-23", False),    # 22 days: outside
])
def test_exclusion_window(contract, run, trigger_date, excluded_date, violates):
    t = invoice("INV-T", [billed(contract, "T1", TRIGGER, 1, date=trigger_date)], invoice_date="2024-07-01")
    e = invoice("INV-E", [billed(contract, "E1", EXCLUDED, 2, date=excluded_date)], invoice_date="2024-07-01")
    out = run(t, e)
    assert ("exclusion_window_violation" in categories(out["INV-E"])) is violates
    assert expected(out["INV-E"]) == (0 if violates else 2 * rate(contract, EXCLUDED))
    assert not out["INV-T"].result.flagged          # the table is directional: the trigger stays payable


def test_exclusion_is_per_patient(contract, run):
    t = invoice("INV-T", [billed(contract, "T1", TRIGGER, 1)], patient="PT-1")
    e = invoice("INV-E", [billed(contract, "E1", EXCLUDED, 2)], patient="PT-2")
    assert not run(t, e)["INV-E"].result.flagged


def test_a_possible_trigger_does_not_prove_a_violation(contract, run):
    # "OUTPT UROL": endoscopic procedure or theatre time?  Billed on a basis
    # neither uses, so the tie-break cannot settle it: it only *might* be the trigger.
    t = invoice("INV-T", [line("T1", "OUTPT UROL", 1, 372925, unit_basis="per_hour_per_item")])
    e = invoice("INV-E", [billed(contract, "E1", EXCLUDED, 2, date="2024-06-05")])
    r = run(t, e)["INV-E"]
    assert "exclusion_window_violation" not in categories(r)
    assert expected(r) is None and any("not asserted" in u for u in r.result.uncertainty_reasons)


# -- identity-independent checks -------------------------------------------------------

def test_line_arithmetic(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 2, total=1)]))["INV-A"]
    assert "line_total_arithmetic" in categories(r) and expected(r) == 2 * rate(contract, PLAIN)


def test_invoice_arithmetic(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1)], total=5))["INV-A"]
    assert categories(r) == {"invoice_total_mismatch"} and expected(r) == rate(contract, PLAIN)


def test_contract_number_mismatch(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1)], contract_number="INS-H2-2024-1183"))["INV-A"]
    assert categories(r) == {"contract_number_mismatch"}
    assert r.result.confidence_band == "high" and expected(r) == rate(contract, PLAIN)


def test_malformed_date_blocks_reconstruction(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1, date="2024-02-30")]))["INV-A"]
    assert "malformed_service_date" in categories(r) and expected(r) is None


def test_date_outside_the_term(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1, date="2023-12-31")], invoice_date="2024-01-05"))["INV-A"]
    assert categories(r) == {"service_date_out_of_window"}


def test_date_after_the_invoice_date(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1, date="2024-07-02")], invoice_date="2024-07-01"))["INV-A"]
    assert categories(r) == {"service_date_after_invoice_date"}


def test_reused_invoice_id_row_represents_the_later_occurrence(contract, run):
    first = invoice("INV-A", [billed(contract, "L1", BUNDLE_A, 1, price=999)], patient="PT-1", index=0)
    later = invoice("INV-A", [billed(contract, "L2", BUNDLE_B, 2, price=4900)], patient="PT-1", index=1)
    r = run(first, later)["INV-A"]
    assert r.result.occurrence_ids == ["INV-A#0", "INV-A#1"]
    # The earlier occurrence stays in the hospital-wide context: its line completes the bundle.
    assert line_trace(r, "L2")["pricing"]["bundle"]["state"] == "applies"
    assert categories(r) == {"duplicate_invoice_id"}          # the earlier record's price error is not copied
    assert "unit_price_mismatch" in {f.category for f in r.occurrences[0].findings}
    assert r.result.billed_total_cents == 2 * 4900 and expected(r) == 2 * 4900


# -- identity-dependent outcomes -------------------------------------------------------

def test_unresolved_service_gives_no_guessed_total(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1),
                              line("L2", "renal ANAES admin", 2, 6600, unit_basis="per_hour")]))["INV-A"]
    assert not r.result.flagged and expected(r) is None and not r.result.pricing_complete
    assert r.provisional_expected_total_cents == rate(contract, PLAIN) + 2 * 6600
    assert r.result.confidence_band == "low"


def test_structural_finding_survives_unresolved_identity(contract, run):
    r = run(invoice("INV-A", [line("L1", "renal ANAES admin", 2, 6600, unit_basis="per_hour")],
                    contract_number="INS-H1-2024-0417"))["INV-A"]
    assert categories(r) == {"contract_number_mismatch"}
    assert r.result.confidence_band == "high" and expected(r) is None


def test_unknown_service_is_flagged_and_not_priced(contract, run):
    r = run(invoice("INV-A", [billed(contract, "L1", PLAIN, 1),
                              line("L2", "LAB - emer DERM", 1, 5000, unit_basis="per_test")]))["INV-A"]
    assert "unknown_service" in categories(r) and expected(r) is None
    assert r.provisional_expected_total_cents == rate(contract, PLAIN) + 5000


def test_price_wrong_under_every_reading_is_flagged(contract, run):
    r = run(invoice("INV-A", [line("L1", "renal ANAES admin", 2, 1, unit_basis="per_hour")]))["INV-A"]
    assert categories(r) == {"unit_price_mismatch"} and r.result.confidence_band == "low"


# -- rounding and stage order (clause 4.1, 4.2) ----------------------------------------

def test_half_up_rounding():
    assert apply_percentage_change(30, Decimal("0.15")) == 35      # 34.5 -> 35, not banker's 34
    assert apply_percentage_change(5, Decimal("-0.10")) == 5       # 4.5 -> 5


def test_rounding_happens_after_every_stage(tmp_path, contract_text):
    # Base 4 cents: 4 x 1.15 = 4.6 -> 5; 5 x 0.90 = 4.5 -> 5.  Rounding once at
    # the end would give 4 x 1.15 x 0.90 = 4.14 -> 4.
    c = patched_contract(tmp_path, contract_text, "| Standard Hepatic Infusion Therapy | per unit dispensed | GBP 39.75 |",
                         "| Standard Hepatic Infusion Therapy | per unit dispensed | GBP 0.04 |")
    si = StageInputs(premium_options=(Decimal("0.15"),), discount_options=(Decimal("0.10"),))
    t = PricingEngine(c).compute(PREMIUM, si, 3)
    assert (t["after_premium_cents"], t["effective_unit_rate_cents"], t["expected_line_total_cents"]) == (5, 5, 15)


def test_overlapping_adjustments_apply_in_contract_order(tmp_path, contract_text):
    text = contract_text.replace(
        "| Supervised Vascular Radiotherapy Fraction | more than 10 items | +15% |",
        "| Supervised Vascular Radiotherapy Fraction | more than 10 items | +15% |\n"
        "| Ambulatory Obstetric Case Conference | more than 2 hours | +20% |").replace(
        "| Standard Orthopaedic Critical Care Occupancy | one hundred and twenty (120) | ten percent (10%) |",
        "| Standard Orthopaedic Critical Care Occupancy | one hundred and twenty (120) | ten percent (10%) |\n"
        "| Ambulatory Obstetric Case Conference | one (1) | ten percent (10%) |")
    path = tmp_path / "contract.md"
    path.write_text(text, encoding="utf-8")
    c = parse_contract(path)
    assert c.threshold_premiums[BUNDLE_A].exceeds_units == 2 and c.volume_discounts[BUNDLE_A][0].exceeds_units == 1
    matcher = H4Matcher(c)
    history = invoice("INV-H", [billed(c, "H1", BUNDLE_A, 2, date="2024-06-01")], patient="PT-H")
    target = invoice("INV-A", [billed(c, "A1", BUNDLE_A, 3, price=12825), billed(c, "A2", BUNDLE_B, 1, price=4900)])
    occs = [history, target]
    out = {r.result.invoice_id: r for r in audit_resolved(occs, resolve_lines(occs, matcher), H4Auditor(c))}
    t = line_trace(out["INV-A"], "A1")["pricing"]
    # bundle 11875 -> premium x1.20 = 14250 -> discount x0.90 = 12825
    assert (t["after_bundle_cents"], t["after_premium_cents"], t["effective_unit_rate_cents"]) == (11875, 14250, 12825)
    assert not out["INV-A"].result.flagged


# -- the real Hospital 4 data ----------------------------------------------------------

@pytest.fixture(scope="module")
def pipeline():
    return H4Pipeline()


def test_observed_dataset_counts(pipeline):
    occs = pipeline.occurrences
    assert (len(occs), len({o.invoice_id for o in occs}), sum(len(o.line_items) for o in occs)) == (840, 835, 10560)


def test_audit_is_deterministic_and_offline(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("the Hospital 4 audit must not open a network connection")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    a = [(r.result.invoice_id, r.result.flagged, r.result.expected_total_cents, r.result.confidence)
         for r in H4Pipeline().run()]
    b = [(r.result.invoice_id, r.result.flagged, r.result.expected_total_cents, r.result.confidence)
         for r in H4Pipeline().run()]
    assert a == b and len(a) == 835


def test_every_row_is_well_formed(pipeline):
    rows = [r.result for r in pipeline.run()]
    assert len({r.invoice_id for r in rows}) == len(rows) == 835
    for r in rows:
        assert 0 <= r.confidence <= 1
        assert r.expected_total_cents is None or r.correction_reconstructable
        assert r.flagged == bool(r.findings)


def test_hospital_4_code_uses_no_other_hospital_labels_or_models():
    import ast
    for path in (REPO / "src" / "hospital_4").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} |                    {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        for module in imported:
            for forbidden in ("hospital_1", "hospital_2", "hospital_3", "hospital_5", "semantic",
                              "urllib", "requests", "http", "socket", "dotenv"):
                assert forbidden not in module, (path.name, module)
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not any("labels" in s and ("/" in s or ".csv" in s) for s in strings), path.name
        assert not any("hospital_4_labels" in s or "openrouter" in s.lower() for s in strings), path.name


committed = pytest.mark.skipif(not (REPO / "outputs" / "hospital_4" / "submission.csv").exists(),
                               reason="run `python -m src.main audit h4` first")


@committed
def test_committed_h4_submission_matches_predictions():
    def rows(p):
        with p.open(newline="", encoding="utf-8") as fh:
            return list(csv.reader(fh))
    sub = rows(REPO / "outputs" / "hospital_4" / "submission.csv")
    assert tuple(sub[0]) == SUBMISSION_COLUMNS and len(sub) == 836
    with (REPO / "outputs" / "hospital_4" / "predictions.csv").open(newline="", encoding="utf-8") as fh:
        pred = [[d[c] for c in SUBMISSION_COLUMNS] for d in csv.DictReader(fh)]
    assert pred == sub[1:]
