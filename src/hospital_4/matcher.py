"""Hospital 4: deterministic, structural service identification.

The invoice description is free text (README: "not a contract term, not a
code").  This module decides, from text alone, one of:

``MATCHED``     the text identifies exactly one contracted service
``AMBIGUOUS``   the text is consistent with one or more contracted services
                but does not carry enough discriminating evidence to choose
``UNKNOWN``     every contracted service is contradicted by a recognised word

It never looks at a price, a total, an invoice id or a patient id.  Those are
not inputs: the public functions take a description and, for one narrow
tie-break, a billed unit-basis token.  A test pins the signatures.

**Structure, not similarity.**  Every Hospital 4 service name is
<qualifier> <specialty> <concept> (the contract parser verifies the three
vocabularies are disjoint).  A description is expanded word by word through
a transparent abbreviation table into contract words, then compared with each
service as a *set*: word order is irrelevant, and a recognised word that a
service does not contain contradicts that service.

**"Identified" is stricter than "only one fits".**  A description that omits
its qualifier or its specialty ("ADMIN ANAES ORTHO") may be an abbreviated
contracted service or a service this contract does not carry at all, and the
text cannot tell those apart -- every alternative is missing the same word.
So MATCHED requires the qualifier *and* the specialty *and* at least one
concept word to be present, no contradicting word, and exactly one consistent
service.  This is the rule Hospital 2's matcher applies, adopted after
Hospital 1's one known matcher defect (a description missing its specialty
word matched confidently).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .contract import H4Contract

MATCHED, AMBIGUOUS, UNKNOWN = "MATCHED", "AMBIGUOUS", "UNKNOWN"

#: Bumped whenever deterministic matching could change.  Recorded in every
#: Hospital 4 artifact.
MATCHER_VERSION = "h4-matcher-1"

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

#: Provider billing references, e.g. "/CW-8569".  Non-semantic: the same
#: description text appears with many different numbers, and none is ever read
#: as a service code.
_REF_SUFFIX = re.compile(r"/\s*[A-Z]{1,4}-\d+\s*$", re.I)

#: Billing shorthand observed in Hospital 4 descriptions, each mapped to one
#: contract word.  Every entry was checked against how the token is used across
#: the Hospital 4 data (``tests/hospital_4/test_matching.py`` asserts each maps
#: to a contract word or to a listed non-contract word).  Single word to single
#: word only: no entry maps a description to a service.
ABBREVIATIONS: dict[str, str] = {
    # qualifiers
    "adv": "advanced", "amb": "ambulatory", "asst": "assisted", "beds": "bedside",
    "compr": "comprehensive", "cont": "continuous", "elect": "elective",
    "emer": "emergency", "ext": "extended", "foc": "focused", "inpt": "inpatient",
    "intens": "intensive", "interm": "intermittent", "outpt": "outpatient",
    "postop": "postoperative", "preop": "preoperative", "rtn": "routine",
    "spclst": "specialist", "std": "standard", "supv": "supervised",
    # specialties
    "card": "cardiac", "ent": "otolaryngologic", "ger": "geriatric",
    "gi": "gastrointestinal", "haem": "haematology", "hep": "hepatic",
    "immun": "immunologic", "infect": "infectious", "metab": "metabolic",
    "msk": "musculoskeletal", "neuro": "neurological", "obst": "obstetric",
    "onc": "oncology", "ophth": "ophthalmic", "ortho": "orthopaedic",
    "paed": "paediatric", "pall": "palliative", "psych": "psychiatric",
    "pulm": "pulmonary", "ren": "renal", "rheum": "rheumatologic",
    "urol": "urologic", "vasc": "vascular",
    # concept words
    "admin": "administration", "anaes": "anaesthesia", "anly": "analysis",
    "bd": "bed", "biop": "biopsy", "conf": "conference", "consult": "consultation",
    "cr": "care", "crit": "critical", "cs": "case", "diag": "diagnostic",
    "dial": "dialysis", "disch": "discharge", "endosc": "endoscopic",
    "fract": "fraction", "hm": "home", "img": "imaging", "inf": "infusion",
    "interp": "interpretation", "lab": "laboratory", "monit": "monitoring",
    "nurs": "nursing", "nutr": "nutritional", "obs": "observation",
    "occ": "occupancy", "physio": "physiotherapy", "plng": "planning",
    "pnl": "panel", "proc": "procedure", "prog": "programme",
    "radiother": "radiotherapy", "recov": "recovery", "rehab": "rehabilitation",
    "rm": "room", "sess": "session", "spcm": "specimen", "steril": "sterilisation",
    "supp": "support", "svc": "service", "telem": "telemetry", "ther": "therapy",
    "thtr": "theatre", "tm": "time", "transf": "transfusion", "transp": "transport",
    "vent": "ventilation", "vst": "visit", "wd": "ward", "wnd": "wound",
    # clinical words this contract does not carry.  Listing them makes such a
    # word *recognised*, so a description naming one contradicts every service
    # instead of being ignored.
    "derm": "dermatologic", "endo": "endocrine", "isol": "isolation",
    "disp": "dispensing", "pharm": "pharmacy",
}

#: Full forms of the non-contract words above, recognised when spelled out.
NON_CONTRACT_WORDS = frozenset({"dermatologic", "endocrine", "isolation", "dispensing", "pharmacy"})

_NOISE = frozenset({"the", "of", "and", "for", "a", "an", "to", "with", "per"})


def strip_reference(description: str) -> str:
    return _REF_SUFFIX.sub(" ", description)


def normalize_description(description: str) -> str:
    """Lower case, reference suffix removed, punctuation collapsed; word order kept."""
    text = re.sub(r"[^A-Za-z0-9]+", " ", strip_reference(description))
    return " ".join(text.lower().split())


def expand_tokens(description: str) -> tuple[str, ...]:
    """Each word of the normalised description, abbreviations expanded."""
    return tuple(ABBREVIATIONS.get(t, t) for t in normalize_description(description).split()
                 if t not in _NOISE)


def canonical_key(description: str) -> str:
    """Order-insensitive identity key: the sorted set of expanded words.

    Two descriptions with the same key get the same identity decision, so
    this is the cluster key.
    """
    return " ".join(sorted(set(expand_tokens(description))))


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """A contracted service the description does not contradict."""

    service: str
    unit_basis: str
    qualifier_evidenced: bool
    specialty_evidenced: bool
    concept_words_evidenced: tuple[str, ...]
    missing_words: tuple[str, ...]


@dataclass(frozen=True)
class DeterministicMatch:
    """What text alone says about one canonical description."""

    canonical_key: str
    tokens: tuple[str, ...]
    status: str
    service: Optional[str]
    #: text | missing_discriminator | tied_candidates | unrecognised_token |
    #: contradiction | no_recognised_words
    method: str
    #: Every consistent service, sorted.  Never truncated: the audit carries an
    #: unresolved line as this whole set.
    candidates: tuple[Candidate, ...]
    evidence: dict = field(default_factory=dict)

    @property
    def candidate_services(self) -> tuple[str, ...]:
        return tuple(c.service for c in self.candidates)

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

class H4Matcher:
    def __init__(self, contract: H4Contract):
        self.contract = contract
        self._words = {name: frozenset(w.lower() for w in name.split()) for name in sorted(contract.services)}
        self.qualifiers = frozenset(s.qualifier for s in contract.services.values())
        self.specialties = frozenset(s.specialty for s in contract.services.values())
        self.concept_words = frozenset(w for s in contract.services.values() for w in s.concept.split())
        self.vocabulary = self.qualifiers | self.specialties | self.concept_words
        self._cache: dict[str, DeterministicMatch] = {}

    def recognised(self, word: str) -> bool:
        return word in self.vocabulary or word in NON_CONTRACT_WORDS

    def match(self, description: str) -> DeterministicMatch:
        key = canonical_key(description)
        if key not in self._cache:
            self._cache[key] = self._match(key)
        return self._cache[key]

    def _match(self, key: str) -> DeterministicMatch:
        tokens = tuple(key.split())
        present = set(tokens)
        unrecognised = sorted(t for t in present if not self.recognised(t))
        recognised = present - set(unrecognised)

        candidates = []
        for name, words in self._words.items():
            if not recognised <= words:
                continue  # a recognised word this service does not contain
            svc = self.contract.services[name]
            candidates.append(Candidate(
                service=name,
                unit_basis=svc.unit_basis,
                qualifier_evidenced=svc.qualifier in present,
                specialty_evidenced=svc.specialty in present,
                concept_words_evidenced=tuple(w for w in svc.concept.split() if w in present),
                missing_words=tuple(w for w in name.lower().split() if w not in present),
            ))
        evidence = {
            "qualifier": sorted(present & self.qualifiers),
            "specialty": sorted(present & self.specialties),
            "concept_words": sorted(present & self.concept_words),
            "non_contract_words": sorted(present & NON_CONTRACT_WORDS),
            "unrecognised_tokens": unrecognised,
        }

        def result(status, service, method):
            return DeterministicMatch(key, tokens, status, service, method, tuple(candidates), evidence)

        if not candidates:
            return result(UNKNOWN, None, "contradiction" if recognised else "no_recognised_words")
        if unrecognised:
            # A word we cannot read might be the discriminating one.
            return result(AMBIGUOUS, None, "unrecognised_token")
        if len(candidates) > 1:
            return result(AMBIGUOUS, None, "tied_candidates")
        only = candidates[0]
        if only.qualifier_evidenced and only.specialty_evidenced and only.concept_words_evidenced:
            return result(MATCHED, only.service, "text")
        return result(AMBIGUOUS, None, "missing_discriminator")


def resolve_line_identity(det: DeterministicMatch, billed_unit_basis: str) -> LineIdentity:
    """Apply the unit-basis tie-break for one line, and only where it is allowed.

    Allowed only when the text leaves a genuine tie between two or more
    consistent services and exactly one of them is contracted on the unit
    basis the line carries.  When it decides, ``used_unit_basis_for_identity``
    is set, and the audit must not then turn the same basis into a
    ``wrong_unit_basis`` accusation.  Compound bases ("per_hour_per_item") are
    compared as the whole token.
    """
    cands = det.candidate_services
    if det.status != AMBIGUOUS or det.method != "tied_candidates":
        return LineIdentity(det.status, det.service, det.method, cands, False)
    supporting = [c.service for c in det.candidates if c.unit_basis == billed_unit_basis]
    if len(supporting) == 1:
        return LineIdentity(MATCHED, supporting[0], "unit_basis_tiebreak", cands, True)
    return LineIdentity(det.status, None, det.method, cands, False)


def cluster_descriptions(descriptions: Iterable[str], matcher: H4Matcher) -> dict[str, dict]:
    """Group raw descriptions by canonical key: one identity decision per cluster."""
    clusters: dict[str, dict] = {}
    for raw in descriptions:
        key = canonical_key(raw)
        c = clusters.setdefault(key, {"canonical_key": key, "raw": Counter(), "normalized": Counter()})
        c["raw"][raw] += 1
        c["normalized"][normalize_description(raw)] += 1
    for key, c in clusters.items():
        c["line_count"] = sum(c["raw"].values())
        c["raw_descriptions"] = [r for r, _ in c.pop("raw").most_common()]
        c["normalized_descriptions"] = [r for r, _ in c.pop("normalized").most_common()]
        c["deterministic"] = matcher.match(key)
    return clusters
