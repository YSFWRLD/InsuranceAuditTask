"""Hospital 3: deterministic, structural service identification.

A description is read through the Hospital 3 ``Lexicon``
(``normalization.py``): each token is a contract word, a global expansion, a
contextual token with several possible readings, or unreadable.  Every
combination of contextual readings is a *reading* of the whole description.
A reading survives if at least one contracted service contains all of its
words; readings no service can carry are eliminated by structure alone.

The result is one of:

``MATCHED``     exactly one contracted service is defensibly identified: its
                qualifier, specialty and at least one concept word are all
                present, nothing contradicts it, and no other service survives
``AMBIGUOUS``   one or more contracted services remain viable, but a
                discriminating word is missing, several services tie, or a
                word could not be read
``UNKNOWN``     every contracted service is contradicted by a word that was read

This is the rule Hospitals 4 and 5 apply; the lexicon, not the rule, is
Hospital 3's own.  "Contracted" here means *named in the contract package*,
including the two services Amendment No. 1 adds: identity is read from the
text and never from the date.  Whether the identified service was contracted
on the line's Service Date is a separate question the audit answers
(``service_not_contracted_on_date``).

Where the text leaves a bounded set of candidates, a gated Jev review of the
description (``semantic.py``) may settle it; the review sees words, never
money.  Identity inputs are the description and -- for one narrow tie-break
-- the billed unit-basis token.  A price, a total, a date, an invoice id or a
patient id is never an input; a test pins the signatures.
"""

from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .contract import H3Contract
from .normalization import Lexicon, description_tokens, normalize_description

MATCHED, AMBIGUOUS, UNKNOWN = "MATCHED", "AMBIGUOUS", "UNKNOWN"

#: Bumped whenever structural matching or line identity could change.
MATCHER_VERSION = "h3-matcher-1"

#: Hard bound on readings per description; refused loudly rather than truncated.
MAX_READINGS = 512


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """A contracted service that some surviving reading does not contradict."""

    service: str
    unit_basis: str
    qualifier_evidenced: bool
    specialty_evidenced: bool
    concept_words_evidenced: tuple[str, ...]
    missing_words: tuple[str, ...]

    @property
    def missing_fields(self) -> tuple[str, ...]:
        out = []
        if not self.qualifier_evidenced:
            out.append("qualifier")
        if not self.specialty_evidenced:
            out.append("specialty")
        if not self.concept_words_evidenced:
            out.append("concept")
        return tuple(out)

    @property
    def complete(self) -> bool:
        return not self.missing_fields


@dataclass(frozen=True)
class StructuralMatch:
    """What the text alone says about one set of description tokens."""

    #: Sorted distinct tokens: the cache key.
    raw_key: str
    #: Two descriptions with the same identity key get the same identity: the
    #: words of the one surviving reading plus any unread tokens, or the raw
    #: tokens when several readings survive.
    identity_key: str
    status: str
    service: Optional[str]
    #: text | text_contextual | missing_discriminator | tied_candidates |
    #: unrecognised_token | contradiction | no_recognised_words
    method: str
    #: Every consistent service, sorted.  Never truncated.
    candidates: tuple[Candidate, ...]
    words_read: tuple[str, ...]
    unread_tokens: tuple[str, ...]
    #: token -> {"alternatives": [...], "surviving": [...]} for contextual tokens.
    contextual: dict = field(default_factory=dict)

    @property
    def candidate_services(self) -> tuple[str, ...]:
        return tuple(c.service for c in self.candidates)

    def candidate(self, service: str) -> Optional[Candidate]:
        return next((c for c in self.candidates if c.service == service), None)


@dataclass(frozen=True)
class LineIdentity:
    """A description's identity for one line, after the unit-basis tie-break
    and any gated missing-word review."""

    status: str
    service: Optional[str]
    #: text | text_contextual | unit_basis_tiebreak | jev_missing_word |
    #: unit_basis_tiebreak_after_review | jev_ambiguous_contracted |
    #: jev_none_of_the_above | <an AMBIGUOUS or UNKNOWN structural method>
    method: str
    #: The pre-tie-break candidate set, whole.
    candidates: tuple[str, ...]
    used_unit_basis_for_identity: bool
    #: True while an uncontracted service remains a possible reading, so no
    #: contract price can be vouched for.  Cleared only by evidence that the
    #: line is one of the contracted candidates.
    open_world: bool = False

    @property
    def possible_services(self) -> tuple[str, ...]:
        if self.status == MATCHED:
            return (self.service,)  # type: ignore[return-value]
        if self.status == AMBIGUOUS:
            return self.candidates
        return ()


# --------------------------------------------------------------------------
# Matcher
# --------------------------------------------------------------------------

class H3Matcher:
    def __init__(self, contract: H3Contract, lexicon: Lexicon):
        self.contract = contract
        self.lexicon = lexicon
        self.vocabulary = contract.vocabulary
        self._words = {name: frozenset(name.lower().split()) for name in sorted(contract.services)}
        self._cache: dict[str, StructuralMatch] = {}

    def match(self, description: str) -> StructuralMatch:
        tokens = tuple(sorted(set(description_tokens(description))))
        key = " ".join(tokens)
        if key not in self._cache:
            self._cache[key] = self._match(tokens)
        return self._cache[key]

    # -- internals -------------------------------------------------------------

    def _consistent(self, words: frozenset[str]) -> list[str]:
        return [name for name, sw in self._words.items() if words <= sw]

    def _candidate(self, name: str, words: frozenset[str]) -> Candidate:
        s = self.contract.services[name]
        return Candidate(
            service=name, unit_basis=s.unit_basis,
            qualifier_evidenced=s.qualifier in words, specialty_evidenced=s.specialty in words,
            concept_words_evidenced=tuple(w for w in s.concept.split() if w in words),
            missing_words=tuple(w for w in name.lower().split() if w not in words))

    def _match(self, tokens: tuple[str, ...]) -> StructuralMatch:
        raw_key = " ".join(tokens)
        fixed: set[str] = set()
        contextual: dict[str, tuple[str, ...]] = {}
        unread: list[str] = []
        for t in tokens:
            readings = self.lexicon.readings(t, self.vocabulary)
            if readings is None:
                unread.append(t)
            elif len(readings) == 1 and t not in self.lexicon.contextual:
                fixed.add(readings[0])
            else:
                # A contextual token -- even one with a single reading -- is
                # read only where some contracted service fits; otherwise it
                # stays unread (below), never a contradiction.
                contextual[t] = readings

        ctx_tokens = sorted(contextual)
        combos = list(itertools.islice(itertools.product(*(contextual[t] for t in ctx_tokens)), MAX_READINGS + 1))
        if len(combos) > MAX_READINGS:
            raise ValueError(f"description {raw_key!r} has more than {MAX_READINGS} readings")

        surviving: list[tuple[frozenset[str], tuple, list[str]]] = []
        for combo in combos:
            words = frozenset(fixed.union(combo))
            cands = self._consistent(words) if words else []
            if cands:
                surviving.append((words, combo, cands))

        ctx_record = {t: {"alternatives": list(contextual[t]),
                          "surviving": sorted({c[1][i] for c in surviving})}
                      for i, t in enumerate(ctx_tokens)}

        def result(status, service, method, cands, words_read, identity_key, unread_tokens):
            return StructuralMatch(raw_key, identity_key, status, service, method, tuple(cands),
                                   tuple(sorted(words_read)), tuple(sorted(unread_tokens)), ctx_record)

        if not surviving:
            # No reading survives.  A contextual token none of whose readings
            # fits might mean something else entirely: treat it as unread and
            # see whether the remaining words still fit a service.
            words = frozenset(fixed)
            cands = self._consistent(words) if words else []
            unread_all = unread + ctx_tokens
            ident = " ".join(sorted(words) + [f"?{t}" for t in sorted(unread_all)])
            if cands and ctx_tokens:
                return result(AMBIGUOUS, None, "unrecognised_token",
                              [self._candidate(n, words) for n in cands], words, ident, unread_all)
            method = "contradiction" if words else "no_recognised_words"
            return result(UNKNOWN, None, method, [], words, ident, unread_all)

        # Union of the survivors; each candidate keeps its best-evidenced reading.
        best: dict[str, Candidate] = {}
        for words, _, cands in surviving:
            for n in cands:
                c = self._candidate(n, words)
                prev = best.get(n)
                if prev is None or (-len(c.missing_fields), len(c.concept_words_evidenced)) > (
                        -len(prev.missing_fields), len(prev.concept_words_evidenced)):
                    best[n] = c
        cands = [best[n] for n in sorted(best)]
        if len(surviving) == 1:
            words_read = surviving[0][0]
            ident = " ".join(sorted(words_read) + [f"?{t}" for t in sorted(unread)])
        else:
            words_read = frozenset().union(*(s[0] for s in surviving))
            ident = "readings:" + raw_key

        if unread:
            return result(AMBIGUOUS, None, "unrecognised_token", cands, words_read, ident, unread)
        if len(cands) > 1:
            return result(AMBIGUOUS, None, "tied_candidates", cands, words_read, ident, unread)
        only = cands[0]
        if only.complete:
            return result(MATCHED, only.service, "text_contextual" if ctx_tokens else "text",
                          cands, words_read, ident, unread)
        return result(AMBIGUOUS, None, "missing_discriminator", cands, words_read, ident, unread)


# --------------------------------------------------------------------------
# Line identity
# --------------------------------------------------------------------------

def unit_basis_tiebreak(match: StructuralMatch, billed_unit_basis: str) -> Optional[str]:
    """The one candidate contracted on the billed basis, when the text leaves a
    genuine tie between two or more services.  Compound bases
    ("per_hour_per_item") compare as the whole token.

    The basis can only choose *among* services the description already
    names; it never turns a missing-word or unreadable description into an
    identity."""
    if match.status != AMBIGUOUS or match.method != "tied_candidates":
        return None
    supporting = [c.service for c in match.candidates if c.unit_basis == billed_unit_basis]
    return supporting[0] if len(supporting) == 1 else None


#: A gated missing-word decision, keyed by (identity key, billed basis).
#: outcome: "service" | "ambiguous_contracted" | "unknown" | "unresolved".
@dataclass(frozen=True)
class MissingWordDecision:
    outcome: str
    service: Optional[str] = None
    reason: str = ""


def resolve_line_identity(match: StructuralMatch, billed_unit_basis: str,
                          decision: Optional[MissingWordDecision] = None) -> LineIdentity:
    """Text first; then the unit-basis tie-break; then a gated review decision.

    ``decision`` is the gated Jev missing-word outcome for this line's
    (identity key, billed basis), or None.  If the tie-break decides,
    ``used_unit_basis_for_identity`` is set and the audit must not then turn
    the same basis into a ``wrong_unit_basis`` accusation."""
    cands = match.candidate_services
    if match.status in (MATCHED, UNKNOWN):
        return LineIdentity(match.status, match.service, match.method, cands, False, False)
    chosen = unit_basis_tiebreak(match, billed_unit_basis)
    if chosen is not None:
        return LineIdentity(MATCHED, chosen, "unit_basis_tiebreak", cands, True, False)
    if decision is not None:
        if decision.outcome == "service":
            if decision.service not in cands:
                raise ValueError(f"reviewed service {decision.service!r} is not a candidate of {match.identity_key!r}")
            svc = match.candidate(decision.service)
            # The reviewer saw the billed basis as secondary evidence.  If it
            # agrees with the chosen service and not with some alternative, it
            # may have tipped the choice: treat it as used for identity.
            used = (svc.unit_basis == billed_unit_basis and
                    any(c.unit_basis != billed_unit_basis for c in match.candidates if c.service != svc.service))
            return LineIdentity(MATCHED, decision.service, "jev_missing_word", cands, used, False)
        if decision.outcome == "ambiguous_contracted":
            # The review closed the world to the contracted candidates, so the
            # text now leaves a genuine tie among them: the narrow unit-basis
            # tie-break is allowed exactly as for a textual tie.
            supporting = [c.service for c in match.candidates if c.unit_basis == billed_unit_basis]
            if len(supporting) == 1:
                return LineIdentity(MATCHED, supporting[0], "unit_basis_tiebreak_after_review", cands, True, False)
            return LineIdentity(AMBIGUOUS, None, "jev_ambiguous_contracted", cands, False, False)
        if decision.outcome == "unknown":
            return LineIdentity(UNKNOWN, None, "jev_none_of_the_above", cands, False, False)
    return LineIdentity(AMBIGUOUS, None, match.method, cands, False, True)


# --------------------------------------------------------------------------
# Clusters
# --------------------------------------------------------------------------

def cluster_descriptions(descriptions: Iterable[str], matcher: H3Matcher) -> dict[str, dict]:
    """Group raw descriptions by identity key: one identity decision per cluster."""
    clusters: dict[str, dict] = {}
    for raw in descriptions:
        m = matcher.match(raw)
        c = clusters.setdefault(m.identity_key, {"identity_key": m.identity_key, "raw": Counter(),
                                                 "normalized": Counter(), "raw_keys": set()})
        c["raw"][raw] += 1
        c["normalized"][normalize_description(raw)] += 1
        c["raw_keys"].add(m.raw_key)
    for key, c in clusters.items():
        c["line_count"] = sum(c["raw"].values())
        c["raw_descriptions"] = [r for r, _ in sorted(c.pop("raw").items(), key=lambda kv: (-kv[1], kv[0]))]
        c["normalized_descriptions"] = [r for r, _ in sorted(c.pop("normalized").items(),
                                                                key=lambda kv: (-kv[1], kv[0]))]
        c["matches"] = [matcher._cache[k] for k in sorted(c.pop("raw_keys"))]
    return clusters
