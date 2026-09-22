"""Hospital 2: deterministic service identification and candidate generation.

The invoice description is free text (README: "not a contract term, not a
code").  This module decides, from text alone, one of:

``MATCHED``     one contracted service is clearly identified
``UNKNOWN``     nothing in the contract plausibly matches, or the text names
                something that contradicts every candidate
``AMBIGUOUS``   the text leaves a genuine choice -- these, and only these, go to
                the semantic stage (``semantic.py``) with a bounded candidate set

It never looks at a price, a total, an invoice id or a patient id.  Those are
not even inputs: the public functions take a description and, for one narrow
tie-break, a billed unit-basis token.

"Clear" is stricter than "best".  Every Hospital 2 service name is
<qualifier> <specialty> <service type> (verified by the contract parser).  A
description that omits the qualifier or the specialty -- "FRACT OUTPATIENT
RADIOTHERAPY" -- may be an abbreviated contracted service or a service the
contract does not carry at all, and the text cannot tell those apart.  A wide
score margin does not help: when the distinguishing word is missing, every
alternative loses it equally.  So a match counts as clear only when the
winner's qualifier and specialty are both evidenced in the text.  Everything
else goes to the semantic stage rather than being guessed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Optional

from .contract import H2Contract

MATCHED, AMBIGUOUS, UNKNOWN = "MATCHED", "AMBIGUOUS", "UNKNOWN"

#: Bumped whenever deterministic matching could change.  Persisted semantic
#: decisions made against a different version are discarded as stale.
MATCHER_VERSION = "h2-matcher-1"

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

#: Provider billing references, e.g. "/SA-3924".  Non-semantic: they vary
#: independently of the service (one description text appears with many
#: different numbers) and are never read as a hidden service code.
_REF_SUFFIX = re.compile(r"/\s*[A-Z]{1,4}-\d+\s*$", re.I)

#: General clinical/English abbreviations that prefix or vowel-dropping rules
#: cannot recover, or recover ambiguously.  Each entry was checked against how
#: the token is actually used across Hospital 2 descriptions (e.g. ``TM`` only
#: ever appears as "THTR TM", theatre time).  These are facts about medical
#: shorthand -- no entry maps a description to a service.
ABBREVIATIONS: dict[str, str] = {
    "ent": "otolaryngologic",
    "gi": "gastrointestinal",
    "msk": "musculoskeletal",
    "hm": "home",
    "rm": "room",
    "bd": "bed",
    "wd": "ward",
    "cr": "care",
    "cs": "case",
    "tm": "time",
    "rtn": "routine",
    "obs": "observation",
    "obst": "obstetric",
    "inpt": "inpatient",
    "outpt": "outpatient",
    "std": "standard",
    "supv": "supervised",
    "spclst": "specialist",
    "asst": "assisted",
    "vst": "visit",
    "svc": "service",
    "img": "imaging",
    "pnl": "panel",
    "plng": "planning",
    "thtr": "theatre",
    "spcm": "specimen",
    "anly": "analysis",
    # Specialties this contract does not carry.  Listing them makes such a
    # word *recognised*, so a description naming one contradicts every
    # candidate instead of being ignored as noise.
    "onc": "oncology",
    "oncology": "oncology",
    "uro": "urologic",
    "urologic": "urologic",
    "immun": "immunologic",
    "immunologic": "immunologic",
}

_NOISE = frozenset({"the", "of", "and", "for", "a", "an", "to", "with", "per"})


def strip_reference(description: str) -> str:
    return _REF_SUFFIX.sub(" ", description)


def normalize_description(description: str) -> str:
    text = strip_reference(description)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text)
    return " ".join(text.lower().split())


def tokenize(description: str) -> tuple[str, ...]:
    return tuple(t for t in normalize_description(description).split() if t not in _NOISE)


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


# Evidence weights, ordered by strength.  Coarse on purpose.
_W_EXACT, _W_ALIAS, _W_PREFIX, _W_SUBSEQ, _W_SHORT = 1.00, 0.98, 0.95, 0.88, 0.90


@lru_cache(maxsize=None)
def token_similarity(desc_token: str, service_token: str) -> float:
    if desc_token == service_token:
        return _W_EXACT
    alias = ABBREVIATIONS.get(desc_token)
    if alias is not None:
        # An explicit alias is authoritative: "tm" is "time", so it must not
        # also be read as the subsequence of "telemetry".
        return _W_ALIAS if alias == service_token else 0.0
    if len(desc_token) < 2:
        return 0.0
    penalty = _W_SHORT if len(desc_token) == 2 else 1.0
    if service_token.startswith(desc_token):
        return _W_PREFIX * penalty
    if desc_token[0] == service_token[0] and _is_subsequence(desc_token, service_token):
        return _W_SUBSEQ * penalty
    return 0.0


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    service: str
    clause_id: str
    unit_basis: str
    score: float
    qualifier_evidenced: bool
    specialty_evidenced: bool
    contradictions: tuple[str, ...]
    unmatched_service_words: tuple[str, ...]


@dataclass(frozen=True)
class DeterministicMatch:
    """What text alone says about one normalised description."""

    normalized_description: str
    tokens: tuple[str, ...]
    status: str
    service: Optional[str]
    method: str
    #: Bounded, ranked, plausible candidates (at most ``MAX_CANDIDATES``).
    candidates: tuple[Candidate, ...]
    #: Candidates within ``MIN_MARGIN`` of the best -- a genuine textual tie.
    tied: tuple[str, ...]
    needs_semantic: bool
    evidence: dict = field(default_factory=dict)

    def candidate(self, service: str) -> Optional[Candidate]:
        return next((c for c in self.candidates if c.service == service), None)


@dataclass(frozen=True)
class LineIdentity:
    """A description's identity *for one line*, after the unit-basis tie-break."""

    status: str
    service: Optional[str]
    method: str
    candidates: tuple[str, ...]
    used_unit_basis_for_identity: bool


# --------------------------------------------------------------------------
# Matcher
# --------------------------------------------------------------------------

class H2Matcher:
    MIN_PLAUSIBLE = 0.55
    MIN_MATCH = 0.72
    MIN_MARGIN = 0.05
    CONTRADICTION_DECAY = 0.75
    MAX_CANDIDATES = 5

    def __init__(self, contract: H2Contract):
        self.contract = contract
        self._words = {
            name: tuple(w.lower() for w in name.split()) for name in sorted(contract.services)
        }
        self._vocabulary = {w for ws in self._words.values() for w in ws}
        self._cache: dict[str, DeterministicMatch] = {}

    def _recognised(self, token: str) -> bool:
        """Is this a word we know means something -- contract or clinical?"""
        if token in ABBREVIATIONS:
            return True
        return any(token_similarity(token, v) > 0 for v in self._vocabulary)

    def _score(self, tokens: tuple[str, ...], name: str) -> Candidate:
        svc = self.contract.services[name]
        words = self._words[name]
        pairs = sorted(
            ((token_similarity(d, s), i, j) for i, d in enumerate(tokens) for j, s in enumerate(words)),
            key=lambda p: (-p[0], p[1], p[2]),
        )
        used_d: set[int] = set()
        used_s: set[int] = set()
        total = 0.0
        for w, i, j in pairs:
            if w <= 0 or i in used_d or j in used_s:
                continue
            used_d.add(i)
            used_s.add(j)
            total += w
        if not tokens or total == 0:
            score = 0.0
        else:
            recall, precision = total / len(words), total / len(tokens)
            score = 2 * recall * precision / (recall + precision)
        contradictions = tuple(
            t for i, t in enumerate(tokens) if i not in used_d and self._recognised(t)
        )
        score *= self.CONTRADICTION_DECAY ** len(contradictions)
        return Candidate(
            service=name,
            clause_id=svc.clause_id,
            unit_basis=svc.unit_basis,
            score=round(score, 4),
            qualifier_evidenced=0 in used_s,
            specialty_evidenced=1 in used_s,
            contradictions=contradictions,
            unmatched_service_words=tuple(w for j, w in enumerate(words) if j not in used_s),
        )

    def match(self, description: str) -> DeterministicMatch:
        normalized = normalize_description(description)
        if normalized not in self._cache:
            self._cache[normalized] = self._match(normalized)
        return self._cache[normalized]

    def _match(self, normalized: str) -> DeterministicMatch:
        tokens = tuple(t for t in normalized.split() if t not in _NOISE)
        ranked = sorted(
            (self._score(tokens, name) for name in self._words),
            key=lambda c: (-c.score, c.service),
        )
        best = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else None
        plausible = tuple(c for c in ranked if c.score >= self.MIN_PLAUSIBLE)[: self.MAX_CANDIDATES]
        tied = tuple(c.service for c in plausible if best.score - c.score < self.MIN_MARGIN)
        evidence = {
            "best_score": best.score,
            "runner_up": runner.service if runner else None,
            "runner_up_score": runner.score if runner else 0.0,
            "unrecognised_tokens": [t for t in tokens if not self._recognised(t)],
        }

        def result(status, service, method, needs_semantic):
            return DeterministicMatch(
                normalized_description=normalized, tokens=tokens, status=status,
                service=service, method=method, candidates=plausible, tied=tied,
                needs_semantic=needs_semantic, evidence=evidence,
            )

        if not plausible:
            # Nothing reaches plausibility.  With a recognised word pointing
            # elsewhere that is a contradiction; with no recognised words at all
            # there is simply nothing to go on.  Either way the text does not
            # identify a contracted service.
            method = "contradiction" if best.contradictions else "no_plausible_candidate"
            return result(UNKNOWN, None, method, False)

        clear = (
            best.score >= self.MIN_MATCH
            and len(tied) == 1
            and not best.contradictions
            and best.qualifier_evidenced
            and best.specialty_evidenced
        )
        if clear:
            return result(MATCHED, best.service, "text_margin", False)

        if len(tied) > 1:
            method = "tied_candidates"
        elif best.contradictions:
            method = "contradiction_present"
        elif not (best.qualifier_evidenced and best.specialty_evidenced):
            method = "missing_discriminator"
        else:
            method = "weak_evidence"
        return result(AMBIGUOUS, None, method, True)


def resolve_line_identity(
    det: DeterministicMatch, billed_unit_basis: str
) -> LineIdentity:
    """Apply the unit-basis tie-break for one line, and only where it is allowed.

    Allowed only when the text leaves a genuine tie between two or more
    plausible candidates and exactly one of them is billed on the unit basis
    the line carries.  When it decides, ``used_unit_basis_for_identity`` is
    set, and the audit must not then turn the same basis into a
    ``wrong_unit_basis`` accusation.
    """
    cands = tuple(c.service for c in det.candidates)
    if det.status != AMBIGUOUS or len(det.tied) < 2:
        return LineIdentity(det.status, det.service, det.method, cands, False)
    supporting = [
        s for s in det.tied if det.candidate(s).unit_basis == billed_unit_basis
    ]
    if len(supporting) == 1:
        return LineIdentity(MATCHED, supporting[0], "unit_basis_tiebreak", cands, True)
    return LineIdentity(det.status, det.service, det.method, cands, False)


def cluster_descriptions(descriptions: Iterable[str], matcher: H2Matcher) -> dict[str, dict]:
    """Group raw descriptions by normalised text: one decision per cluster."""
    clusters: dict[str, dict] = {}
    for raw in descriptions:
        key = normalize_description(raw)
        c = clusters.setdefault(key, {"normalized_description": key, "raw": Counter()})
        c["raw"][raw] += 1
    for key, c in clusters.items():
        c["line_count"] = sum(c["raw"].values())
        c["raw_descriptions"] = [r for r, _ in c.pop("raw").most_common()]
        c["deterministic"] = matcher.match(key)
    return clusters
