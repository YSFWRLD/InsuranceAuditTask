"""Hospital 3: deterministic description normalisation, with its own evidence.

The invoice description is free text.  Hospital 4 read it through a
hand-written abbreviation table; Hospital 5 proposed readings broadly and had
Jev review each one.  Hospital 3 does neither.  Every reading here is
derived from **Hospital 3's own contract vocabulary** by transparent letter
rules, and its scope is decided by **Hospital 3's own descriptions**:

``prefix``           the token begins the contract word ("pulm" -> pulmonary)
``subsequence``      same first letter, letters in order ("thtr" -> theatre)
``spelling_variant`` a British/American or ae/e spelling of the word
``clinical_acronym`` an initialism no letter rule can reach; only "ent"
                     (ear, nose and throat -> otolaryngologic), and only in
                     context, with Hospital 3 corpus evidence (see below)

A token's readings are every contract word one of these rules reaches.  The
token is then:

``global``      exactly one reading, at least three letters, not an acronym,
                and **slot-consistent in the Hospital 3 corpus**: wherever the
                token appears, no other word of the description occupies the
                same qualifier or specialty slot.  Read everywhere.
``contextual``  otherwise.  Every reading is carried, and the matcher keeps
                only readings under which some contracted service contains
                all the description's words; if none fits, the token stays
                *unread* -- it never becomes a contradiction.
``unread``      no rule reaches a contract word.

No abbreviation is taken from another hospital's table: the tables in
``hospital_4.matcher`` and the reviews in ``hospital_5`` are not consulted.
Normalisation may expand evidence; it may never create it.  Nothing in this
module reads a price, a quantity, a total, an invoice id or a patient id: its
inputs are the contract vocabulary and description strings alone.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .contract import H3Contract

#: Bumped whenever normalisation or lexicon construction could change.
NORMALIZATION_VERSION = "h3-normalization-1"

#: Provider billing references, e.g. "/RM-4002".  Non-semantic; stripped for
#: matching only -- the raw description is kept everywhere for provenance.
_REF_SUFFIX = re.compile(r"/\s*[A-Z]{1,4}-\d+\s*$", re.I)

#: Function words that carry no identity.
NOISE_WORDS = frozenset({"the", "of", "and", "for", "a", "an", "to", "with", "per", "at", "on", "in"})

#: Initialisms whose letters are not an in-order subsequence of the word they
#: stand for.  Always contextual.  The Hospital 3 evidence each entry must
#: carry is computed by ``acronym_evidence`` and pinned by tests: the token
#: never shares a description with another specialty word, and every
#: description using it has the same qualifier and concept words as some
#: contracted service of that specialty.
CLINICAL_ACRONYMS: dict[str, str] = {
    "ent": "otolaryngologic",   # ear, nose and throat
}

#: The recorded review of each clinical acronym.  A reading here is never
#: global: it is read only where a contracted service contains every other
#: word of the description (the matcher's contextual rule).
ACRONYM_REVIEWS: dict[str, dict] = {
    "ent": {
        "canonical": "otolaryngologic",
        "scope": "hospital_3_contextual",
        "global": False,
        "status": "human_reviewed",
        "reviewed": "2026-09-25",
        "record": "prompts/hospital_3/002_h3_finalization_review.md; outputs/hospital_3/decision_log.md item 18",
        "reason": "ENT (ear, nose and throat) stands for otolaryngology. In Hospital 3 it occupies the specialty "
                  "slot of 19 description spellings (15 token sets, 424 lines), never beside another specialty "
                  "word; every one fits a contracted Otolaryngologic service, 7 of the 15 token sets also occur "
                  "with the specialty spelled out, and no Hospital 3 contract word or description suggests "
                  "another meaning. No price, total or invoice was consulted.",
    },
}

#: British -> American spelling transformations applied to contract words.
_SPELLING_RULES: tuple[tuple[str, str], ...] = (
    ("isation", "ization"), ("gramme", "gram"), ("aem", "em"), ("ae", "e"),
    ("oe", "e"), ("tre", "ter"), ("our", "or"),
)

#: Tokens shorter than this are never global, however unique their reading.
MIN_GLOBAL_LENGTH = 3

GLOBAL, CONTEXTUAL, UNREAD = "global", "contextual", "unread"


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


# --------------------------------------------------------------------------
# Letter rules
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


def word_rules(token: str, word: str) -> tuple[str, ...]:
    """Which letter rules let ``token`` stand for the contract word ``word``."""
    found = []
    if len(token) < 2 or token == word:
        return ()
    variants = spelling_variants(word)
    if word.startswith(token) or any(v.startswith(token) and v != token for v in variants):
        found.append("prefix")
    elif token[0] == word[0] and (_is_subsequence(token, word) or any(_is_subsequence(token, v) for v in variants)):
        found.append("subsequence")
    if token in variants:
        found.append("spelling_variant")
    if CLINICAL_ACRONYMS.get(token) == word:
        found.append("clinical_acronym")
    return tuple(found)


# --------------------------------------------------------------------------
# The lexicon
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenEvidence:
    token: str
    #: Distinct normalised descriptions containing the token.
    descriptions: int
    #: contract word -> the rules that reach it
    readings: dict
    classification: str
    #: Why the token is not global, when it is not.
    reason: str = ""
    #: Slot conflicts found in the corpus: other words in the same slot.
    slot_conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Lexicon:
    """What each invoice token may mean.

    ``global_map``   token -> the one contract word used everywhere
    ``contextual``   token -> the possible contract words; the surrounding
                     words must decide between them (see the matcher)

    A token in neither is read only if it is itself a contract word.
    """

    name: str
    global_map: dict[str, str] = field(default_factory=dict)
    contextual: dict[str, tuple[str, ...]] = field(default_factory=dict)
    evidence: dict[str, TokenEvidence] = field(default_factory=dict)

    def readings(self, token: str, vocabulary: frozenset[str]) -> Optional[tuple[str, ...]]:
        """The possible contract words for one token, or None if it cannot be read."""
        if token in vocabulary:
            return (token,)
        if token in self.global_map:
            return (self.global_map[token],)
        if token in self.contextual:
            return self.contextual[token]
        return None

    @staticmethod
    def exact_only() -> "Lexicon":
        """The baseline: only literal contract words are read."""
        return Lexicon("exact_contract_words_only")

    def without(self, tokens: Iterable[str]) -> "Lexicon":
        """The same lexicon with ``tokens`` unread (for ablations and tests)."""
        drop = set(tokens)
        return Lexicon(f"{self.name}_without_{'_'.join(sorted(drop))}",
                       {t: w for t, w in self.global_map.items() if t not in drop},
                       {t: w for t, w in self.contextual.items() if t not in drop},
                       {t: e for t, e in self.evidence.items() if t not in drop})


def corpus_token_sets(descriptions: Iterable[str]) -> list[frozenset[str]]:
    """The distinct token sets of the distinct normalised descriptions, sorted."""
    distinct = {normalize_description(d) for d in descriptions}
    return sorted({frozenset(t for t in d.split() if t not in NOISE_WORDS) for d in distinct}, key=sorted)


def build_lexicon(contract: H3Contract, descriptions: Iterable[str], *,
                  include_acronyms: bool = True) -> Lexicon:
    """Derive the lexicon from the contract vocabulary and the corpus.

    Deterministic: the same contract and descriptions always give the same
    lexicon.  ``include_acronyms=False`` leaves ``ent`` unread (ablation).
    """
    vocab = contract.vocabulary
    sets = corpus_token_sets(descriptions)
    count = Counter(t for s in sets for t in s)
    readings: dict[str, dict[str, tuple[str, ...]]] = {}
    for token in sorted(count):
        if token in vocab or not token.isalpha():
            continue
        hits = {w: r for w in sorted(vocab) if (r := word_rules(token, w))}
        if token in CLINICAL_ACRONYMS and not include_acronyms:
            hits = {w: r for w, r in hits.items() if r != ("clinical_acronym",)}
        if hits:
            readings[token] = hits

    # Tokens with a single reading, before the corpus check: used to read the
    # *other* words of a description when looking for slot conflicts.
    single = {t: next(iter(h)) for t, h in readings.items() if len(h) == 1}

    def slot_word(tok: str) -> Optional[str]:
        return tok if tok in vocab else single.get(tok)

    global_map: dict[str, str] = {}
    contextual: dict[str, tuple[str, ...]] = {}
    evidence: dict[str, TokenEvidence] = {}
    for token in sorted(count):
        if token in vocab or token in NOISE_WORDS:
            continue
        hits = readings.get(token)
        if not hits:
            evidence[token] = TokenEvidence(token, count[token], {}, UNREAD, "no letter rule reaches a contract word")
            continue
        words = tuple(sorted(hits))
        reason = ""
        conflicts: tuple[str, ...] = ()
        if len(words) > 1:
            reason = "several contract words are reachable"
        elif len(token) < MIN_GLOBAL_LENGTH:
            reason = f"shorter than {MIN_GLOBAL_LENGTH} letters"
        elif "clinical_acronym" in hits[words[0]]:
            reason = "clinical acronym: read only where a contracted service fits"
        else:
            slot = contract.slot(words[0])
            if slot in ("qualifier", "specialty"):
                others = {slot_word(o) for s in sets if token in s for o in s if o != token}
                conflicts = tuple(sorted(w for w in others if w and w != words[0] and contract.slot(w) == slot))
                if conflicts:
                    reason = f"shares a description with another {slot} word"
        if reason:
            contextual[token] = words
            cls = CONTEXTUAL
        else:
            global_map[token] = words[0]
            cls = GLOBAL
        evidence[token] = TokenEvidence(token, count[token], {w: list(hits[w]) for w in words}, cls, reason, conflicts)
    name = "h3_orthographic" if include_acronyms else "h3_orthographic_without_acronyms"
    return Lexicon(name, global_map, contextual, evidence)


# --------------------------------------------------------------------------
# Acronym evidence
# --------------------------------------------------------------------------

def acronym_evidence(contract: H3Contract, descriptions: Iterable[str], lexicon: Lexicon) -> dict[str, dict]:
    """Hospital 3 corpus evidence for each clinical acronym.

    For ``ent`` -> otolaryngologic: in how many distinct descriptions it
    appears; whether any of them also carries another specialty word (which
    would contradict a specialty reading); and, for each description, whether
    its other words, read through the lexicon, are exactly the qualifier and
    concept words of a contracted service of that specialty, and whether the
    same qualifier and concept also appear with the specialty spelled out.
    """
    sets = corpus_token_sets(descriptions)
    vocab = contract.vocabulary
    out = {}
    for token, word in CLINICAL_ACRONYMS.items():
        spec_words = contract.specialties
        using = [s for s in sets if token in s]

        def read(tok: str) -> set[str]:
            r = lexicon.readings(tok, vocab)
            return set(r) if r else set()

        def key(tokens) -> frozenset:
            return frozenset(frozenset(read(o)) for o in tokens if read(o))

        spelled_keys = {key(t - {word}) for t in sets if word in t}
        other_specialty = []
        fits, spelled = [], []
        for s in using:
            others = [o for o in s if o != token]
            if any(read(o) and read(o) <= spec_words for o in others):
                other_specialty.append(" ".join(sorted(s)))
            fits.append(any(
                all(read(o) & set(svc.name.lower().split()) for o in others)
                and any(svc.qualifier in read(o) for o in others)
                for svc in contract.services.values() if svc.specialty == word))
            spelled.append(key(others) in spelled_keys)
        out[token] = {
            "reading": word,
            "distinct_descriptions": len(using),
            "with_another_specialty_word": other_specialty,
            "descriptions_fitting_a_contracted_service_of_the_specialty": sum(fits),
            "descriptions_also_seen_with_the_specialty_spelled_out": sum(spelled),
            "scope": "contextual only: read where a contracted service contains every other word",
            "review": ACRONYM_REVIEWS.get(token),
        }
    return out
