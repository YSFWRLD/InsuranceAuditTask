"""Hospital 4 service identity: structural, conservative, and blind to price."""

import ast
import inspect
from pathlib import Path

import pytest

from src.hospital_4 import audit as audit_mod
from src.hospital_4 import matcher as matcher_mod
from src.hospital_4.matcher import (
    ABBREVIATIONS, AMBIGUOUS, MATCHED, NON_CONTRACT_WORDS, UNKNOWN, canonical_key, cluster_descriptions,
    normalize_description, resolve_line_identity,
)
from tests.hospital_4.conftest import (
    COMPOUND, TIE_HOUR, TIE_VISIT, billed, categories, invoice, line,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "hospital_4"


def test_exact_canonical_description(matcher, contract):
    for name in contract.services:
        m = matcher.match(name)
        assert (m.status, m.service, m.method) == (MATCHED, name, "text"), name


def test_word_order_does_not_matter(matcher):
    a = matcher.match("Preoperative Haematology Imaging Interpretation")
    b = matcher.match("interpretation IMAGING haematology preoperative")
    assert a.canonical_key == b.canonical_key and b.service == a.service


def test_abbreviations_expand(matcher):
    m = matcher.match("PREOP haem IMG interpretation")
    assert (m.status, m.service) == (MATCHED, "Preoperative Haematology Imaging Interpretation")
    m = matcher.match("monit POSTOP otolaryngologic TELEM")
    assert m.service == "Postoperative Otolaryngologic Telemetry Monitoring"
    m = matcher.match("ent monit postop telem")
    assert m.service == "Postoperative Otolaryngologic Telemetry Monitoring"


@pytest.mark.parametrize("suffix", ["", " /CW-8569", " /CW-1", "/cw-0042 "])
def test_cw_reference_is_removed(matcher, suffix):
    m = matcher.match("INTENSIVE  - paediatric CONSULT" + suffix)
    assert (m.status, m.service) == (MATCHED, "Intensive Paediatric Consultation")
    assert "cw" not in normalize_description("x /CW-8569").split()


def test_strong_unique_match_needs_all_three_slots(matcher):
    m = matcher.match("PREOP haem IMG interp")
    assert m.status == MATCHED
    assert m.candidates[0].qualifier_evidenced and m.candidates[0].specialty_evidenced


def test_missing_specialty_is_ambiguous_even_with_one_candidate(matcher):
    m = matcher.match("COMPR img INTERP")   # Comprehensive <?> Imaging Interpretation
    assert m.status == AMBIGUOUS and m.method == "missing_discriminator"
    assert m.candidate_services == ("Comprehensive Urologic Imaging Interpretation",)


def test_missing_qualifier_is_ambiguous(matcher):
    m = matcher.match("renal ANAES admin")
    assert m.status == AMBIGUOUS and m.method == "missing_discriminator"
    assert m.candidate_services == ("Intermittent Renal Anaesthesia Administration",)


def test_missing_concept_is_ambiguous(matcher):
    m = matcher.match("SPCLST immunologic")
    assert m.status == AMBIGUOUS and m.candidate_services == ("Specialist Immunologic Consultation",)


@pytest.mark.parametrize("description", [
    "Advanced Cardiac Ward Bed Occupancy",       # a real specialty, contradicting every Advanced ward bed
    "LAB - emer DERM",                           # a specialty this contract does not carry
    "OUTPT metabolic PHARM disp",                # pharmacy dispensing: not contracted
    "CONSULT rtn NEURO",                         # Routine Neurological Consultation: not contracted
])
def test_contradictory_description_is_unknown(matcher, description):
    m = matcher.match(description)
    assert m.status == UNKNOWN and m.method == "contradiction" and m.candidates == ()


def test_genuine_text_tie(matcher):
    m = matcher.match("CARDIAC physio SESS")
    assert m.status == AMBIGUOUS and m.method == "tied_candidates"
    assert set(m.candidate_services) == {TIE_HOUR, TIE_VISIT}


def test_unit_basis_breaks_only_a_genuine_tie(matcher):
    m = matcher.match("CARDIAC physio SESS")
    assert resolve_line_identity(m, "per_hour").service == TIE_HOUR
    assert resolve_line_identity(m, "per_visit").service == TIE_VISIT
    ident = resolve_line_identity(m, "per_day")          # neither candidate: stays ambiguous
    assert ident.status == AMBIGUOUS and not ident.used_unit_basis_for_identity


def test_unit_basis_does_not_resolve_a_missing_discriminator(matcher):
    m = matcher.match("renal ANAES admin")
    ident = resolve_line_identity(m, "per_hour")          # the only candidate's basis
    assert ident.status == AMBIGUOUS and not ident.used_unit_basis_for_identity


def test_unit_basis_does_not_touch_a_clear_match(matcher):
    m = matcher.match("Intensive Paediatric Consultation")
    ident = resolve_line_identity(m, "per_hour")          # wrong basis for this service
    assert ident.service == "Intensive Paediatric Consultation" and not ident.used_unit_basis_for_identity


def test_compound_basis_is_compared_whole(matcher, contract):
    assert contract.services[COMPOUND].unit_basis == "per_hour_per_item"
    m = matcher.match("interm UROL telem MONIT")
    assert m.service == COMPOUND


def test_basis_used_for_identity_is_not_reused_for_wrong_unit_basis(contract, run):
    # Both tied services exist; per_hour picks the Emergency session.  The same
    # basis cannot then be called wrong.
    occ = invoice("INV-A", [billed(contract, "L1", TIE_HOUR, 2, description="CARDIAC physio SESS")])
    r = run(occ)["INV-A"]
    assert "wrong_unit_basis" not in categories(r)
    assert r.represented.lines[0].used_unit_basis_for_identity


def test_clear_match_on_the_wrong_basis_is_reported(contract, run):
    occ = invoice("INV-A", [billed(contract, "L1", "Intensive Paediatric Consultation", 1, unit_basis="per_hour")])
    assert "wrong_unit_basis" in categories(run(occ)["INV-A"])


def test_matcher_accepts_no_price_or_id_input():
    for fn in (matcher_mod.H4Matcher.match, matcher_mod.canonical_key, matcher_mod.normalize_description,
               matcher_mod.expand_tokens):
        params = set(inspect.signature(fn).parameters) - {"self"}
        assert params == {"description"}, (fn.__name__, params)
    assert set(inspect.signature(resolve_line_identity).parameters) == {"det", "billed_unit_basis"}


def test_billed_price_never_changes_identity(contract, run):
    for price in (1, 13975, 999999):
        occ = invoice("INV-A", [line("L1", "AMB obst CS conf", 2, price, unit_basis="per_hour")])
        rl = run(occ)["INV-A"].represented.lines[0]
        assert (rl.status, rl.service) == (MATCHED, "Ambulatory Obstetric Case Conference")


@pytest.mark.parametrize("path", sorted(p.name for p in SRC.glob("*.py")))
def test_no_matched_by_price_path_exists(path):
    tree = ast.parse((SRC / path).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
            {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not any("MATCHED_BY_PRICE" in n for n in names)


def test_identity_code_never_reads_prices_totals_or_ids():
    src = inspect.getsource(matcher_mod)
    for forbidden in ("unit_price_cents", "line_total_cents", "invoice_total_cents", "patient_id", "invoice_id"):
        assert forbidden not in src, forbidden
    resolve_src = inspect.getsource(audit_mod.resolve_lines)
    assert "unit_price" not in resolve_src and "total" not in resolve_src


def test_repeated_calls_are_deterministic(matcher, contract):
    a = [matcher.match(d) for d in ("gi - IMG interp", "CARDIAC physio SESS", "LAB - emer DERM")]
    fresh = matcher_mod.H4Matcher(contract)
    b = [fresh.match(d) for d in ("gi - IMG interp", "CARDIAC physio SESS", "LAB - emer DERM")]
    assert a == b


def test_descriptions_cluster_by_canonical_key(matcher):
    raw = ["INTENS - paed CONSULT /CW-2377", "paed CONSULT intens", "intensive paediatric consultation /CW-1"]
    clusters = cluster_descriptions(raw, matcher)
    assert len(clusters) == 1
    (c,) = clusters.values()
    assert c["line_count"] == 3 and c["deterministic"].status == MATCHED
    assert canonical_key(raw[0]) == "consultation intensive paediatric"


def test_every_abbreviation_maps_to_a_contract_or_listed_word(matcher):
    for short, full in ABBREVIATIONS.items():
        assert " " not in short and " " not in full, short
        assert full in matcher.vocabulary or full in NON_CONTRACT_WORDS, (short, full)
        assert short not in matcher.vocabulary, f"{short!r} shadows a contract word"


def test_observed_hospital_4_coverage(matcher):
    """Documentation of the observed data, not a runtime constant."""
    from src.shared.data import load_occurrences
    from src.hospital_4.audit import JSONL
    descriptions = [l.description for o in load_occurrences(JSONL) for l in o.line_items]
    clusters = cluster_descriptions(descriptions, matcher)
    statuses = [c["deterministic"].status for c in clusters.values()]
    assert (len(clusters), statuses.count(MATCHED), statuses.count(AMBIGUOUS), statuses.count(UNKNOWN)) == \
        (211, 157, 41, 13)
