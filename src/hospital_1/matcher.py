"""Hospital 1: map a free-text billing description onto a contracted service.

The matcher is deterministic and label-free.  It knows two things: the list of
service names from the contract, and a dictionary of *general* textual and
clinical abbreviations.  It is never told that a particular description means a
particular service.

Three outcomes are possible and all three survive into the audit:

``MATCHED``     one service is clearly best supported by the text
``AMBIGUOUS``   several services remain plausible and the text cannot choose
``UNKNOWN``     nothing in the contract plausibly matches

Two things the matcher must never do:

* use the billed price to choose a service -- the price is the thing under
  audit, so pricing-based matching would make the audit circular;
* force a choice when the evidence is a tie.  A tie is a result.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from ..shared.models import MatchStatus, ServiceMatch, UnitBasis

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

#: Trailing provider reference numbers, e.g. "... Biop /NG-3022".  These are
#: internal hospital references and carry no service information.
_REF_SUFFIX = re.compile(r"/\s*[A-Z]{1,4}-\d+\s*$", re.I)

#: General abbreviations that are *not* recoverable by prefix or vowel-dropping
#: rules.  Each entry is a standard clinical or English contraction, written
#: here because the expansion is not derivable from the letters alone.  Adding
#: a description-to-service mapping here would be label fitting; adding
#: "ent -> otolaryngologic" is a fact about medical English.
ABBREVIATIONS: dict[str, str] = {
    "ent": "otolaryngologic",
    "gi": "gastrointestinal",
    "msk": "musculoskeletal",
    "ophth": "ophthalmic",
    "opth": "ophthalmic",
    "ob": "obstetric",
    "obs": "obstetric",
    "psych": "psychiatric",
    "neuro": "neurological",
    "cardio": "cardiac",
    "derm": "dermatologic",
    "onc": "oncology",
    "paeds": "paediatric",
    "peds": "paediatric",
    "ped": "paediatric",
    "pt": "physiotherapy",
    "rehab": "rehabilitation",
    "icu": "critical",
    "iv": "infusion",
    "rx": "pharmaceutical",
    "dx": "diagnostic",
    "abx": "infectious",
    "resp": "pulmonary",
    "pulm": "pulmonary",
    "endo": "endocrine",
    "heme": "haematology",
    "haem": "haematology",
    "imm": "immunologic",
    "rheum": "rheumatologic",
    "vasc": "vascular",
    "uro": "urologic",
    "geri": "geriatric",
    "ger": "geriatric",
    "palli": "palliative",
    "nutr": "nutritional",
    "anaes": "anaesthesia",
    "anesth": "anaesthesia",
    "op": "procedure",
    "obsv": "observation",
    "rm": "room",
    "hm": "home",
    "hr": "hour",
    "svc": "service",
    "prog": "programme",
    "occ": "occupancy",
    "rtn": "routine",
    "std": "standard",
    "adv": "advanced",
    "amb": "ambulatory",
    "ext": "extended",
    "compr": "comprehensive",
    "cont": "continuous",
    "interm": "intermittent",
    "intens": "intensive",
    "preop": "preoperative",
    "postop": "postoperative",
    "periop": "perioperative",
    "spec": "specialist",
    "supv": "supervised",
    "elec": "elective",
    "emerg": "emergency",
    "inpt": "inpatient",
    "outpt": "outpatient",
    "focus": "focused",
    "assist": "assisted",
    "biop": "biopsy",
    "bx": "biopsy",
    "proc": "procedure",
    "wnd": "wound",
    "cr": "care",
    "spcm": "specimen",
    "anly": "analysis",
    "nurs": "nursing",
    "recov": "recovery",
    "endosc": "endoscopic",
    "radiother": "radiotherapy",
    "physio": "physiotherapy",
    "telem": "telemetry",
    "lab": "laboratory",
    "infect": "infectious",
    "card": "cardiac",
    "ortho": "orthopaedic",
    "orth": "orthopaedic",
    "metab": "metabolic",
    "disp": "dispensing",
    "consult": "consultation",
    "eval": "evaluation",
    "mgmt": "management",
    "tx": "therapy",
    "sess": "session",
    "svs": "services",
}

#: Tokens that carry no service identity in a billing description.  Kept short
#: on purpose: an over-eager stop list destroys evidence.
_NOISE_TOKENS = frozenset({"the", "of", "and", "for", "a", "an", "to", "with", "per"})


def normalize_description(description: str) -> str:
    """Casing, punctuation, whitespace and provider reference suffixes."""
    text = _REF_SUFFIX.sub(" ", description)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text)
    return " ".join(text.lower().split())


def tokenize(description: str) -> list[str]:
    return [t for t in normalize_description(description).split() if t not in _NOISE_TOKENS]


def expand_token(token: str) -> str:
    """Apply the abbreviation dictionary; otherwise return the token."""
    return ABBREVIATIONS.get(token, token)


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


# --------------------------------------------------------------------------
# Token-level similarity
# --------------------------------------------------------------------------

# Weights are ordered by how much evidence each kind of agreement carries.
# They are deliberately coarse: a finer scale would be tuning dressed up as
# modelling.
_W_EXACT = 1.00
_W_ALIAS = 0.98
_W_PREFIX = 0.95
_W_SUBSEQ = 0.88
_W_SHORT_PENALTY = 0.90   # applied to 2-character abbreviations


@lru_cache(maxsize=None)
def token_similarity(desc_token: str, service_token: str) -> float:
    """How strongly ``desc_token`` evidences ``service_token`` (0.0 - 1.0)."""
    if desc_token == service_token:
        return _W_EXACT

    expanded = expand_token(desc_token)
    if expanded == service_token:
        return _W_ALIAS
    # An alias may itself need prefix matching: "rehab" -> "rehabilitation"
    # is exact, but "psych" -> "psychiatric" arrives via the table.
    if expanded != desc_token and service_token.startswith(expanded):
        return _W_ALIAS * _W_PREFIX

    if len(desc_token) < 2:
        return 0.0

    penalty = _W_SHORT_PENALTY if len(desc_token) == 2 else 1.0

    if service_token.startswith(desc_token):
        return _W_PREFIX * penalty

    # Vowel-dropped abbreviations ("wnd" -> "wound", "spcm" -> "specimen").
    # Requiring the first letter to agree keeps this from matching noise.
    if desc_token[0] == service_token[0] and _is_subsequence(desc_token, service_token):
        return _W_SUBSEQ * penalty

    return 0.0


# --------------------------------------------------------------------------
# Matcher
# --------------------------------------------------------------------------

class ServiceMatcher:
    """Rank contracted services against a free-text description."""

    #: A candidate below this score is not considered plausible at all.
    MIN_PLAUSIBLE = 0.55
    #: A candidate must reach this to be selectable as a MATCHED result.
    MIN_MATCH = 0.72
    #: ...and must beat the runner-up by this much.
    MIN_MARGIN = 0.05
    #: Score retained per *contradicting* token.  A description token that is
    #: itself contract vocabulary but which a candidate cannot account for is
    #: not a missing word, it is a word pointing somewhere else: "Advanced
    #: Orthopaedic Recovery Room" is not "Advanced Cardiac Recovery Room
    #: Occupancy" with a typo, it is a service this contract does not carry.
    #: One such token is treated as roughly cancelling one matched token.
    CONTRADICTION_DECAY = 0.75

    def __init__(self, service_names: Iterable[str], unit_bases: dict[str, UnitBasis]):
        self.service_names = sorted(service_names)
        self.unit_bases = unit_bases
        self._service_tokens = {
            name: tuple(dict.fromkeys(normalize_description(name).split()))
            for name in self.service_names
        }
        #: Every word the rate schedule uses, for contradiction detection.
        self._vocabulary = {t for toks in self._service_tokens.values() for t in toks}
        self._cache: dict[tuple[str, Optional[str]], ServiceMatch] = {}

    def _in_vocabulary(self, token: str) -> bool:
        return any(token_similarity(token, v) > 0 for v in self._vocabulary)

    # -- scoring ------------------------------------------------------------

    def _score(self, desc_tokens: list[str], service: str) -> float:
        """Symmetric F1 over a greedy one-to-one token assignment.

        Recall alone would let "Routine Urologic Biopsy Procedure Extra Words"
        score perfectly; precision alone would reward matching a one-word
        description against a six-word service.  Both matter.
        """
        svc_tokens = self._service_tokens[service]
        if not desc_tokens or not svc_tokens:
            return 0.0

        pairs = []
        for i, d in enumerate(desc_tokens):
            for j, s in enumerate(svc_tokens):
                w = token_similarity(d, s)
                if w > 0:
                    pairs.append((w, i, j))
        if not pairs:
            return 0.0

        # Greedy assignment, strongest evidence first.  Ties broken by index so
        # the result never depends on dict or set iteration order.
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
        used_d: set[int] = set()
        used_s: set[int] = set()
        total = 0.0
        for w, i, j in pairs:
            if i in used_d or j in used_s:
                continue
            used_d.add(i)
            used_s.add(j)
            total += w

        recall = total / len(svc_tokens)
        precision = total / len(desc_tokens)
        if recall + precision == 0:
            return 0.0
        score = 2 * recall * precision / (recall + precision)

        contradictions = sum(
            1
            for i, d in enumerate(desc_tokens)
            if i not in used_d and self._in_vocabulary(d)
        )
        return score * (self.CONTRADICTION_DECAY ** contradictions)

    def rank(self, description: str) -> list[tuple[str, float]]:
        tokens = tokenize(description)
        scored = [(name, self._score(tokens, name)) for name in self.service_names]
        # Sort by score desc, then name asc: fully deterministic.
        scored.sort(key=lambda p: (-p[1], p[0]))
        return scored

    # -- public API ---------------------------------------------------------

    def match(
        self,
        description: str,
        billed_unit_basis: Optional[UnitBasis] = None,
    ) -> ServiceMatch:
        key = (description, billed_unit_basis.value if billed_unit_basis else None)
        if key in self._cache:
            return self._cache[key]
        result = self._match_uncached(description, billed_unit_basis)
        self._cache[key] = result
        return result

    def _match_uncached(
        self,
        description: str,
        billed_unit_basis: Optional[UnitBasis],
    ) -> ServiceMatch:
        normalized = normalize_description(description)
        ranked = self.rank(description)
        best_name, best_score = ranked[0]
        runner_name, runner_score = ranked[1] if len(ranked) > 1 else (None, 0.0)

        plausible = [n for n, s in ranked if s >= self.MIN_PLAUSIBLE]

        def build(status, service, method, used_basis=False) -> ServiceMatch:
            return ServiceMatch(
                description=description,
                normalized_description=normalized,
                status=status,
                service=service,
                match_score=round(best_score, 4),
                runner_up=runner_name,
                runner_up_score=round(runner_score, 4),
                candidate_services=tuple(plausible[:8]),
                match_method=method,
                used_unit_basis_for_matching=used_basis,
            )

        if best_score < self.MIN_MATCH:
            # Nothing in the contract is plausibly this service.
            return build(MatchStatus.UNKNOWN, None, "below_match_threshold")

        if best_score - runner_score >= self.MIN_MARGIN:
            return build(MatchStatus.MATCHED, best_name, "text_margin")

        # --- text leaves a genuine tie ------------------------------------
        # Only now may the billed unit basis be consulted, and only to break a
        # tie the text could not break.  If it does the choosing, the audit
        # must not then turn round and claim the unit basis was wrong: that
        # would be counting one piece of evidence twice.
        tied = [n for n, s in ranked if best_score - s < self.MIN_MARGIN]
        if billed_unit_basis is not None:
            supporting = [n for n in tied if self.unit_bases.get(n) is billed_unit_basis]
            if len(supporting) == 1:
                return build(
                    MatchStatus.MATCHED, supporting[0], "unit_basis_tiebreak",
                    used_basis=True,
                )

        return build(MatchStatus.AMBIGUOUS, None, "tied_candidates")


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------

def write_match_audit(line_matches: Iterable[ServiceMatch], path: Path) -> None:
    """One row per distinct description, for a human to check the matcher.

    ``line_matches`` is one match per invoice line, in file order.  A
    description's row shows its first line's match -- the same description can
    resolve differently on another line only through the unit-basis tie-break,
    and ``used_unit_basis_for_matching`` flags exactly those rows.
    """
    first: dict[str, ServiceMatch] = {}
    counts: Counter[str] = Counter()
    for m in line_matches:
        counts[m.description] += 1
        first.setdefault(m.description, m)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "description",
                "normalized_description",
                "occurrences",
                "matched_service",
                "status",
                "match_score",
                "runner_up",
                "runner_up_score",
                "match_method",
                "used_unit_basis_for_matching",
                "candidate_services",
            ]
        )
        for desc in sorted(first):
            m = first[desc]
            writer.writerow(
                [
                    desc,
                    m.normalized_description,
                    counts[desc],
                    m.service or "",
                    m.status.value,
                    f"{m.match_score:.4f}",
                    m.runner_up or "",
                    f"{m.runner_up_score:.4f}",
                    m.match_method,
                    int(m.used_unit_basis_for_matching),
                    "|".join(m.candidate_services),
                ]
            )
