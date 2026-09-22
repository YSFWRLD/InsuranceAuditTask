"""Hospital 2 deterministic matching: text decides identity, never price."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.hospital_2.matcher import (
    AMBIGUOUS,
    MATCHED,
    UNKNOWN,
    H2Matcher,
    cluster_descriptions,
    normalize_description,
    resolve_line_identity,
)
from src.shared.data import load_occurrences
from tests.hospital_2.conftest import (
    PLAIN,
    cats,
    invoice,
    line,
    run,
)

ROOT = Path(__file__).resolve().parents[2]
TIED = "ENT REHAB PROG"
TIED_DAY = "Intensive Otolaryngologic Rehabilitation Programme"      # per_day
TIED_VISIT = "Intermittent Otolaryngologic Rehabilitation Programme"  # per_visit


# -- reference suffixes --------------------------------------------------------

@pytest.mark.parametrize("suffix", ["", " /SA-1234", " /SA-9999", "/SA-0001", " / SA-42"])
def test_sa_suffix_does_not_affect_identity(matcher, suffix):
    base = matcher.match("ADV GI ENDOSC PROC")
    other = matcher.match("ADV GI ENDOSC PROC" + suffix)
    assert other.normalized_description == base.normalized_description == "adv gi endosc proc"
    assert (other.status, other.service) == (base.status, base.service)


def test_sa_numbers_are_not_read_as_service_codes(matcher):
    """Two different services with the same SA number stay different services."""
    a = matcher.match("ADV GI ENDOSC PROC /SA-5000")
    b = matcher.match("INTENS PAED INF THER /SA-5000")
    assert a.service != b.service


# -- obvious abbreviations -----------------------------------------------------

@pytest.mark.parametrize("description,service", [
    ("ADV GI ENDOSC PROC", "Advanced Gastrointestinal Endoscopic Procedure"),
    ("PROC ADV GI ENDOSCOPIC /SA-3753", "Advanced Gastrointestinal Endoscopic Procedure"),
    ("INTENS PAED INF THER", "Intensive Paediatric Infusion Therapy"),
    ("EMER REN INF THER", "Emergency Renal Infusion Therapy"),
    ("COMPR ENT CS CONF", "Comprehensive Otolaryngologic Case Conference"),
    ("SUPV RHEUM CRIT CR OCC", "Supervised Rheumatologic Critical Care Occupancy"),
    ("PREOP OBST HM VST", "Preoperative Obstetric Home Visit"),
])
def test_obvious_abbreviations_resolve(matcher, description, service):
    m = matcher.match(description)
    assert (m.status, m.service, m.method) == (MATCHED, service, "text_margin")


# -- the three outcomes ----------------------------------------------------------

def test_genuinely_close_descriptions_stay_ambiguous(matcher):
    m = matcher.match(TIED)
    assert m.status == AMBIGUOUS and m.service is None
    assert set(m.tied) == {TIED_DAY, TIED_VISIT}
    assert m.needs_semantic


def test_a_missing_specialty_is_not_a_clear_match(matcher):
    """One candidate fits, but the word that would tell it apart from an
    uncontracted service of the same kind is absent.  Not guessed."""
    m = matcher.match("ADV ENDOSCOPIC PROC")
    assert m.status == AMBIGUOUS
    assert m.method == "missing_discriminator"
    assert [c.service for c in m.candidates] == ["Advanced Gastrointestinal Endoscopic Procedure"]
    assert m.needs_semantic


@pytest.mark.parametrize("description", ["ONC WD BD OCC", "ROUTINE HEP CS CONF", "STD GI TELEM MONIT"])
def test_contradictory_descriptions_become_unknown(matcher, description):
    m = matcher.match(description)
    assert m.status == UNKNOWN and m.service is None
    assert not m.needs_semantic


def test_candidates_are_bounded(matcher):
    for d in ["OCC", "RM OCC", "ISOL RM OCC", "ADV"]:
        assert len(matcher.match(d).candidates) <= H2Matcher.MAX_CANDIDATES


# -- identity inputs ------------------------------------------------------------

def test_matching_has_no_price_or_id_inputs():
    assert set(inspect.signature(H2Matcher.match).parameters) == {"self", "description"}
    assert set(inspect.signature(resolve_line_identity).parameters) == {"det", "billed_unit_basis"}


def test_billed_price_perturbation_does_not_change_identity(contract, matcher):
    """Same descriptions, wildly different prices: identical identities."""
    descs = ["ADV GI ENDOSC PROC", TIED, "ADV ENDOSCOPIC PROC", "ONC WD BD OCC", PLAIN]
    def occ(prices, inv):
        return invoice(inv, [line(f"{inv}-{i}", d, 1, p, basis="per_visit") for i, (d, p) in enumerate(zip(descs, prices))])
    a = run(contract, matcher, [occ([100] * 5, "INV-A")])["INV-A"]
    b = run(contract, matcher, [occ([999999, 1, 377700, 12, 141675], "INV-B")])["INV-B"]
    ident = lambda r: [(rl.status, rl.service, rl.source) for rl in r.occurrences[0].lines]
    assert ident(a) == ident(b)


# -- unit basis ----------------------------------------------------------------

def test_unit_basis_breaks_only_a_genuine_textual_tie(matcher):
    tied = matcher.match(TIED)
    visit = resolve_line_identity(tied, "per_visit")
    assert (visit.status, visit.service, visit.method) == (MATCHED, TIED_VISIT, "unit_basis_tiebreak")
    assert visit.used_unit_basis_for_identity is True
    day = resolve_line_identity(tied, "per_day")
    assert day.service == TIED_DAY and day.used_unit_basis_for_identity
    neither = resolve_line_identity(tied, "per_hour")
    assert neither.status == AMBIGUOUS and not neither.used_unit_basis_for_identity


def test_unit_basis_does_not_touch_a_clear_match(matcher):
    clear = matcher.match("ADV GI ENDOSC PROC")
    r = resolve_line_identity(clear, "per_hour")
    assert r.service == "Advanced Gastrointestinal Endoscopic Procedure"
    assert r.used_unit_basis_for_identity is False


def test_unit_basis_does_not_resolve_a_missing_discriminator(matcher):
    """With one textual candidate there is no tie to break."""
    r = resolve_line_identity(matcher.match("ADV ENDOSCOPIC PROC"), "per_procedure")
    assert r.status == AMBIGUOUS and not r.used_unit_basis_for_identity


def test_consumed_unit_basis_cannot_create_wrong_unit_basis(contract, matcher):
    consumed = invoice("INV-C", [line("L-C", TIED, 1, 30775, basis="per_visit")])
    r = run(contract, matcher, [consumed])["INV-C"]
    rl = r.occurrences[0].lines[0]
    assert rl.used_unit_basis_for_identity and rl.service == TIED_VISIT
    assert "wrong_unit_basis" not in cats(r)
    assert not r.result.flagged


def test_a_clear_match_on_the_wrong_basis_is_reported(contract, matcher):
    wrong = invoice("INV-W", [line("L-W", PLAIN, 1, 141675, basis="per_day")])
    assert cats(run(contract, matcher, [wrong])["INV-W"]) == {"wrong_unit_basis"}


# -- clustering ----------------------------------------------------------------

def test_repeated_descriptions_form_one_cluster(matcher):
    clusters = cluster_descriptions(["ADV GI PROC", "ADV  GI PROC /SA-1", "ADV GI PROC /SA-2", "adv gi proc"], matcher)
    assert list(clusters) == ["adv gi proc"]
    assert clusters["adv gi proc"]["line_count"] == 4
    assert len(clusters["adv gi proc"]["raw_descriptions"]) == 4


def test_hospital_2_descriptions_cluster_as_observed(matcher):
    occ = load_occurrences(ROOT / "invoices" / "hospital_2_invoices.jsonl")
    raw = [l.description for o in occ for l in o.line_items]
    clusters = cluster_descriptions(raw, matcher)
    assert (len(occ), len(raw), len(set(raw)), len(clusters)) == (1132, 14360, 506, 441)
    assert normalize_description("INPT  ORTHO RECOV RM OCC /SA-8297") == "inpt ortho recov rm occ"


# -- structural anti-circularity -----------------------------------------------

def _code_only(path: Path) -> str:
    """Executable tokens only: comments and strings (prose) removed."""
    import io
    import tokenize

    toks = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    return " ".join(t.string for t in toks if t.type not in (tokenize.COMMENT, tokenize.STRING))


@pytest.mark.parametrize("module", ["matcher.py", "semantic.py"])
def test_identity_code_never_reads_prices_totals_or_ids(module):
    code = _code_only(ROOT / "src" / "hospital_2" / module)
    for field in ("unit_price_cents", "line_total_cents", "invoice_total_cents",
                  "patient_id", "invoice_id", "labels"):
        assert field not in code, f"{module} refers to {field}"


@pytest.mark.parametrize("module", ["contract.py", "matcher.py", "semantic.py", "audit.py"])
def test_hospital_2_does_not_depend_on_hospital_1_or_labels(module):
    code = _code_only(ROOT / "src" / "hospital_2" / module)
    assert "hospital_1" not in code and "labels" not in code
