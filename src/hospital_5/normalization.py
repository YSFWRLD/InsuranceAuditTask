"""Hospital 5: broad normalisation candidates, and the reviewed lexicon.

The invoice description is free text.  Hospital 4 read it through a
hand-written abbreviation table.  Hospital 5 does not: this module *proposes*
mappings from invoice tokens to contract vocabulary with transparent,
deterministic heuristics, deliberately broadly, and Jev (``semantic.py``)
*reviews* every proposal.  The reviewed verdicts, not this module, decide what
a token may mean.

    generation  (here)      broad and cheap; allowed to be wrong
    review      (Jev)       one bounded judgment per proposal
    acceptance  (gates)     configurable thresholds, applied in code
    use         (matcher)   safe-global tokens expand everywhere; context-
                            required tokens carry several readings that the
                            surrounding words must decide between

Normalisation may expand evidence; it may never create it.  Nothing in this
module reads a price, a quantity, a total, an invoice id or a patient id: the
inputs are the contract vocabulary and the description strings alone.

Heuristics (each proposal records which produced it):

``prefix``                the token begins the contract word ("neuro")
``subsequence``           same first letter, letters in order ("thtr", "svc")
``spelling_variant``      a British/American or ae/e spelling of the word
``clinical_acronym``      a short seed list of initialisms that no letter
                          heuristic can reach ("ent" -> otolaryngologic)
``concept_phrase``        the whole multi-word concept the word belongs to
                          ("img" -> "diagnostic imaging")
``specialty_phrase``      a specialty placed in front of the concept word
                          ("consult" -> "palliative consultation")

The two phrase heuristics exist to be rejected: they add a contract word the
description never wrote.  Proposing them anyway lets the review show that it
refuses to invent a word, instead of that refusal being assumed.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .contract import H5Contract

#: Bumped whenever normalisation or candidate generation could change.
#: Recorded in every Hospital 5 semantic artifact.
NORMALIZATION_VERSION = "h5-normalization-1"

#: Provider billing references, e.g. "/PH-3348".  Non-semantic; stripped for
#: matching only -- the raw description is kept everywhere for provenance.
_REF_SUFFIX = re.compile(r"/\s*[A-Z]{1,4}-\d+\s*$", re.I)

#: Function words that carry no identity.  None occurs in the Hospital 5
#: corpus today; the list exists so one appearing later is not read as an
#: unrecognised clinical word.
NOISE_WORDS = frozenset({"the", "of", "and", "for", "a", "an", "to", "with", "per", "at", "on", "in"})

#: Initialisms whose letters are not an in-order subsequence of the word they
#: stand for.  Proposals only: each still goes to review like any other.
CLINICAL_ACRONYMS: dict[str, tuple[str, ...]] = {
    "ent": ("otolaryngologic",),   # ear, nose and throat
}

#: British -> American spelling transformations applied to contract words, so
#: an American-spelled or American-abbreviated token can be proposed
#: ("pediatric", "ped").  Every rule is length-preserving or shortening, so
#: applying them to a fixed point terminates.
_SPELLING_RULES: tuple[tuple[str, str], ...] = (
    ("isation", "ization"), ("gramme", "gram"), ("aem", "em"), ("ae", "e"),
    ("oe", "e"), ("tre", "ter"), ("our", "or"),
)

HEURISTICS = ("prefix", "subsequence", "spelling_variant", "clinical_acronym",
              "concept_phrase", "specialty_phrase")


# --------------------------------------------------------------------------
# Description normalisation
# --------------------------------------------------------------------------

def strip_reference(description: str) -> str:
    return _REF_SUFFIX.sub(" ", description)


def normalize_description(description: str) -> str:
    """Lower case, reference suffix removed, punctuation collapsed; word order kept."""
    text = re.sub(r"[^A-Za-z0-9]+", " ", strip_reference(description))
    return " ".join(text.lower().split())


def description_tokens(description: str) -> tuple[str, ...]:
    return tuple(t for t in normalize_description(description).split() if t not in NOISE_WORDS)


def corpus_tokens(descriptions: Iterable[str]) -> Counter:
    """Token -> number of distinct normalised descriptions it appears in."""
    distinct = {normalize_description(d) for d in descriptions}
    return Counter(t for d in distinct for t in set(d.split()) if t not in NOISE_WORDS)


# --------------------------------------------------------------------------
# Contract vocabulary
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractVocabulary:
    """The words Hospital 5 service names are made of, by slot."""

    qualifiers: frozenset[str]
    specialties: frozenset[str]
    concepts: frozenset[str]
    concept_words: frozenset[str]
    #: specialty -> concepts it is contracted with (for phrase proposals).
    specialty_concepts: dict[str, frozenset[str]]

    @classmethod
    def from_contract(cls, contract: H5Contract) -> "ContractVocabulary":
        sc: dict[str, set[str]] = {}
        for s in contract.services.values():
            sc.setdefault(s.specialty, set()).add(s.concept)
        return cls(contract.qualifiers, contract.specialties, contract.concepts, contract.concept_words,
                   {k: frozenset(v) for k, v in sc.items()})

    @property
    def words(self) -> frozenset[str]:
        return self.qualifiers | self.specialties | self.concept_words

    def slot(self, word: str) -> str:
        if word in self.qualifiers:
            return "qualifier"
        if word in self.specialties:
            return "specialty"
        if word in self.concept_words:
            return "concept"
        raise KeyError(word)

    def as_state(self) -> dict:
        """The vocabulary as Jev sees it: words only, sorted, no rates."""
        return {"qualifiers": sorted(self.qualifiers), "specialties": sorted(self.specialties),
                "service_concepts": sorted(self.concepts)}


# --------------------------------------------------------------------------
# Candidate generation
# --------------------------------------------------------------------------

def _is_subsequence(token: str, word: str) -> bool:
    it = iter(word)
    return all(ch in it for ch in token)


def spelling_variants(word: str) -> frozenset[str]:
    """Alternative spellings of a contract word (``paediatric`` -> ``pediatric``)."""
    out = {word}
    changed = True
    while changed:
        changed = False
        for w in list(out):
            for old, new in _SPELLING_RULES:
                if old in w:
                    v = w.replace(old, new)
                    if v not in out:
                        out.add(v)
                        changed = True
    return frozenset(out - {word})


@dataclass(frozen=True)
class NormalizationCandidate:
    """One proposed reading of one invoice token."""

    token: str
    #: The contract word or phrase proposed ("neurological", "diagnostic imaging").
    proposed: str
    #: Which heuristics produced it, sorted.
    heuristics: tuple[str, ...]
    #: The slot(s) of the proposed words ("specialty", "concept").
    slots: tuple[str, ...]

    @property
    def candidate_id(self) -> str:
        return f"{self.token}->{self.proposed}"

    @property
    def proposed_words(self) -> tuple[str, ...]:
        return tuple(self.proposed.split())

    @property
    def is_phrase(self) -> bool:
        return len(self.proposed_words) > 1


def _word_heuristics(token: str, word: str) -> list[str]:
    found = []
    if len(token) < 2 or token == word:
        return found
    variants = spelling_variants(word)
    if word.startswith(token) or any(v.startswith(token) and v != token for v in variants):
        found.append("prefix")
    elif token[0] == word[0] and (_is_subsequence(token, word) or any(_is_subsequence(token, v) for v in variants)):
        found.append("subsequence")
    if token in variants:
        found.append("spelling_variant")
    if word in CLINICAL_ACRONYMS.get(token, ()):
        found.append("clinical_acronym")
    return found


def propose_candidates(tokens: Iterable[str], vocab: ContractVocabulary) -> list[NormalizationCandidate]:
    """Every proposal for every corpus token that is not already a contract word.

    Word proposals come from the letter heuristics; phrase proposals are
    derived from concept-word proposals only (see the module docstring).
    Sorted by token, then proposal, so the list -- and every review request
    built from it -- is deterministic.
    """
    out: dict[tuple[str, str], set[str]] = {}
    for token in sorted(set(tokens)):
        if token in vocab.words or token in NOISE_WORDS or not token.isalpha():
            continue
        word_hits = {w: h for w in sorted(vocab.words) if (h := _word_heuristics(token, w))}
        for w, hs in word_hits.items():
            out.setdefault((token, w), set()).update(hs)
        for w in word_hits:
            if w not in vocab.concept_words:
                continue
            for concept in sorted(vocab.concepts):
                cw = concept.split()
                if w in cw and len(cw) > 1:
                    out.setdefault((token, concept), set()).add("concept_phrase")
            for specialty, concepts in sorted(vocab.specialty_concepts.items()):
                for concept in sorted(concepts):
                    if concept.split()[0] == w:
                        out.setdefault((token, f"{specialty} {w}"), set()).add("specialty_phrase")
    result = []
    for (token, proposed), hs in sorted(out.items()):
        slots = tuple(vocab.slot(w) for w in proposed.split())
        result.append(NormalizationCandidate(token, proposed, tuple(sorted(hs)), slots))
    return result


def colliding_words(token: str, candidates: Iterable[NormalizationCandidate]) -> list[str]:
    """Every contract word (not phrase) proposed for ``token``: the collisions
    a reviewer must see to judge whether one reading is safe globally."""
    return sorted({c.proposed for c in candidates if c.token == token and not c.is_phrase})


# --------------------------------------------------------------------------
# The reviewed lexicon
# --------------------------------------------------------------------------

SAFE_GLOBAL, CONTEXT_REQUIRED, UNSAFE = (
    "safe_global_normalization", "context_required", "unsafe_normalization")


@dataclass(frozen=True)
class Lexicon:
    """What each invoice token may mean, after review and gating.

    ``global_map``   token -> the one expansion used everywhere
    ``contextual``   token -> the accepted readings; the surrounding words
                     must decide between them (see the matcher)

    A token in neither is read only if it is itself a contract word.
    """

    name: str
    global_map: dict[str, tuple[str, ...]] = field(default_factory=dict)
    contextual: dict[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)

    def readings(self, token: str, vocabulary: frozenset[str]) -> Optional[tuple[tuple[str, ...], ...]]:
        """The possible expansions of one token, or None if it cannot be read."""
        if token in vocabulary:
            return ((token,),)
        if token in self.global_map:
            return (self.global_map[token],)
        if token in self.contextual:
            return self.contextual[token]
        return None

    @staticmethod
    def exact_only() -> "Lexicon":
        """The deterministic baseline: only literal contract words are read."""
        return Lexicon("exact_contract_words_only")


def build_lexicon(decisions: dict[str, str], candidates: Iterable[NormalizationCandidate], *,
                  include_contextual: bool = True) -> Lexicon:
    """Turn gated per-proposal decisions into a lexicon.

    ``decisions`` maps candidate id -> SAFE_GLOBAL | CONTEXT_REQUIRED | UNSAFE
    (the gate's output, not Jev's raw choice).  A token becomes global only
    if exactly one reading was accepted and that reading was judged safe
    globally.  Two or more accepted readings make the token contextual even
    if each was individually judged safe: one token cannot globally mean two
    things.
    """
    accepted: dict[str, list[tuple[str, NormalizationCandidate]]] = {}
    for c in candidates:
        d = decisions.get(c.candidate_id)
        if d in (SAFE_GLOBAL, CONTEXT_REQUIRED):
            accepted.setdefault(c.token, []).append((d, c))
    global_map: dict[str, tuple[str, ...]] = {}
    contextual: dict[str, tuple[tuple[str, ...], ...]] = {}
    for token, items in sorted(accepted.items()):
        if len(items) == 1 and items[0][0] == SAFE_GLOBAL:
            global_map[token] = items[0][1].proposed_words
        elif include_contextual:
            contextual[token] = tuple(sorted(c.proposed_words for _, c in items))
    name = "reviewed_global_and_contextual" if include_contextual else "reviewed_global_only"
    return Lexicon(name, global_map, contextual)
