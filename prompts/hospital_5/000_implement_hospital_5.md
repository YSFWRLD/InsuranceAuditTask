# Prompt 000 — implement Hospital 5

- **Used with:** Claude Code (Claude Opus 5.5)
- **Kind:** development prompt to the coding assistant. Hospital 5's runtime prompts are the Jev question templates [`001_jev_normalization_review.md`](001_jev_normalization_review.md) and [`002_jev_missing_word_resolution.md`](002_jev_missing_word_resolution.md)
- **Produced:** `src/hospital_5/`, `tests/hospital_5/`, `artifacts/hospital_5/`, `outputs/hospital_5/`, and the addition of Hospital 5 to the combined submission
- **Preceded by:** [`prompts/hospital_4/000_implement_hospital_4.md`](../hospital_4/000_implement_hospital_4.md) and the short operational requests after it (see the prompt index)
- **Provenance:** saved when issued (2026-09-24), copied verbatim from the session.

Transcribed verbatim below.

---

Implement Hospital 5 end-to-end in this repository.

Repository:
https://github.com/YSFWRLD/InsuranceAuditTask

Do not just design or write a plan. Inspect the existing repository, implement the code, tests, artifacts, CLI integration, documentation, and final H5 output.

The goal is not merely to reproduce H2/H4 conservatism. The main objective for H5 is:

MAXIMIZE DEFENSIBLE COVERAGE.

We want substantially fewer blank expected totals than H2/H4, but we must achieve that through better semantic resolution and uncertainty handling, NOT by guessing, using billed price as identity evidence, or copying another solution.

==================================================
0. FIRST: INSPECT THE EXISTING PROJECT
==================================================

Before changing anything, inspect:

- EXERCISE.md
- README.md
- DECISION_LOG.md
- requirements.txt
- .env.example

Hospital 4:
- src/hospital_4/contract.py
- src/hospital_4/matcher.py
- src/hospital_4/audit.py
- all Hospital 4 tests
- Hospital 4 artifacts and outputs

Hospital 2:
- src/hospital_2/matcher.py
- src/hospital_2/semantic.py
- src/hospital_2/audit.py
- especially how TypeSafe/Jev requests, exports/imports, prompt provenance, hashes, retries, and artifacts currently work

Shared:
- src/main.py
- src/submission.py
- src/shared/*
- all current tests

Hospital 5 source of truth:
- contracts/hospital_5/network_reimbursement_agreement.md
- invoices/hospital_5_invoices.jsonl
- invoices/hospital_5_line_items.csv if useful

Do not change the meaning or output behavior of H1, H2, or H4.

Before implementation:
1. Run the full existing test suite.
2. Record hashes of existing H1/H2/H4 committed/generated outputs that must remain stable.
3. Inspect H5 data and independently recompute dataset statistics rather than trusting notes in this prompt.

Current known H5 contract:
- Contract: INS-H5-2024-0731
- Pelham Health Network
- GBP
- 2024-01-01 through 2025-12-31
- HALF_UP rounding after EVERY pricing adjustment step, including multipliers of 1.0
- pricing order:
  bundled-rate substitution
  -> facility multiplier
  -> plan-tier multiplier
  -> premium/uplift
  -> cumulative volume discount
  -> multiply final unit rate by payable quantity

The H5 contract has service-specific:
- base rates
- unit bases
- daily caps
- facility multipliers
- plan-tier multipliers
- daily/threshold premiums
- non-business-day uplifts
- bundles
- cumulative volume discounts
- directed exclusions
- invoice/date/duplicate rules

READ THE CONTRACT YOURSELF AND IMPLEMENT ITS EXACT WORDING.

Do not blindly copy an H1 or H4 interpretation when H5 wording differs.

==================================================
1. ARCHITECTURE
==================================================

Create a clean Hospital 5 package following the existing repository style, likely:

src/hospital_5/
    __init__.py
    contract.py
    matcher.py
    semantic.py
    audit.py

Adapt names only if the existing project conventions make another layout clearly better.

Reuse proven H4 concepts where appropriate:

- occurrence-preserving invoice ingestion
- deterministic contract parsing
- MATCHED / AMBIGUOUS / UNKNOWN identity states
- whole candidate sets, never truncated
- global cross-invoice indexes
- exact Decimal/cents pricing
- explicit uncertainty propagation
- deterministic findings
- detection separate from reconstruction
- unit basis only as a narrow secondary tie-break
- never use billed price to identify a service

Do NOT duplicate large amounts of H4 code unnecessarily if a clean shared abstraction is appropriate, but do not perform a dangerous broad refactor of H1/H2/H4 just for elegance.

==================================================
2. THE MAIN IMPROVEMENT OVER H2/H4
==================================================

Our main previous weakness was:

description not confidently identified
-> service becomes ambiguous
-> too much downstream uncertainty
-> expected_total_cents becomes blank

For H5 we need to attack that in TWO places:

A. a much stronger reviewed NORMALIZATION LAYER
B. explicit resolution of MISSING-WORD AMBIGUITY

And then:

C. FINANCIAL EQUIVALENCE for ambiguity that remains

An invoice should NOT become blank merely because semantic identity is uncertain.

An expected total should remain blank only when remaining uncertainty can actually change the corrected monetary result.

==================================================
3. NORMALIZATION LAYER
==================================================

Build an intentionally BROAD candidate vocabulary from BOTH:

- the H5 contract vocabulary
- the actual H5 invoice description corpus

The purpose is to expose as many plausible abbreviation/variant mappings as possible, then let Jev classify whether each proposed mapping is safe.

Do not manually hardcode only a tiny vocabulary and call the problem solved.

Process:

1. Extract all unique H5 raw descriptions.
2. Strip provider reference suffixes such as /PH-#### for matching only.
3. Preserve raw values for audit/provenance.
4. Extract contract vocabulary:
   - qualifiers
   - specialties
   - concept words/phrases
5. Extract invoice tokens and common phrases.
6. Generate broad candidate expansions using transparent deterministic heuristics:
   - exact forms
   - prefixes
   - subsequence-style abbreviations
   - common spelling variants
   - British/American variants
   - obvious shortened forms
   - common multi-token abbreviations
7. Do NOT use any billed financial values when generating candidates.

Important:
The candidate list should be intentionally broad.
Jev is the REVIEWER, not the candidate generator.

Examples of desired behavior:

NEURO -> neurological
should likely be globally safe.

ENDO -> endocrine
must NOT automatically become global because H5 contains both:
- Endocrine
- Endoscopic

Therefore ENDO should be context-dependent unless surrounding words resolve it.

IMG -> diagnostic imaging
must NOT be treated as a safe global normalization because "imaging" is broader than "diagnostic imaging".

CONSULT -> palliative consultation
must be rejected because "palliative" is invented.

Normalization may expand evidence.
Normalization may NEVER create evidence.

==================================================
4. USE JEV CORRECTLY
==================================================

Use TypeSafe System One / Jev.

Existing environment variables already include:
- TYPESAFE_API_KEY
- fallback JEV_API_KEY
- JEV_API_URL
- JEV_MODEL

Default model currently:
jev-1.13.0

Do not hardcode secrets.

Documentation:
https://docs.typesafe.ai/introduction
https://docs.typesafe.ai/primitives
https://docs.typesafe.ai/primitives/choice
https://docs.typesafe.ai/concepts/state
https://docs.typesafe.ai/confidence

IMPORTANT:
Jev is NOT being used as a normal reasoning LLM.

Each Jev question must be ONE narrow bounded judgment.

State contains evidence.
Question asks for one classification.

Do not send one giant prompt asking:
"review the whole mapping system".

Do not ask Jev to calculate prices.
Do not ask Jev to audit an invoice.
Do not ask Jev to apply contract arithmetic.

Use normal Python for those tasks.

==================================================
5. JEV NORMALIZATION REVIEW
==================================================

For each candidate normalization mapping, create a structured state containing:

- task
- Hospital 5 contract vocabulary relevant to the token
- candidate_under_review:
    invoice_token
    proposed_normalization
- actual contract words that could collide
- hard normalization rules
- allowed behavior
- forbidden behavior
- zero financial values

Use a Choice question keyed by an ID.

The question schema must look like:

{
  "normalization_safety": {
    "type": "choice",
    "instructions": {
      "question": "...",
      "focus": "..."
    },
    "criteria": {
      "safe_global_normalization": {...},
      "context_required": {...},
      "unsafe_normalization": {...}
    }
  }
}

NOT:

{
  "type": "choice",
  ...
}

The root of the questions object is keyed by the question identifier.

Meanings:

safe_global_normalization:
The token and expansion preserve the same semantic meaning and can be applied globally across H5.

context_required:
The proposed meaning can be correct, but the token has multiple plausible meanings in H5 and surrounding description context is required.

unsafe_normalization:
The proposal changes, narrows, or invents meaning.

Store the FULL Jev result:
- selected choice
- probabilities
- confidence
- model
- request id if available
- request/input hash
- state hash
- question hash
- timestamp if project conventions already use it

Generate reviewed artifacts such as:

artifacts/hospital_5/normalization_candidates.json
artifacts/hospital_5/jev_normalization_reviews.jsonl
artifacts/hospital_5/safe_global_vocab.json
artifacts/hospital_5/context_required_vocab.json
artifacts/hospital_5/rejected_vocab.json

Use names consistent with current artifact conventions.

Do not automatically promote a result solely because Jev selected the option.

Implement configurable acceptance gates.

For high-stakes global vocabulary promotion, start conservatively.

Example:
- selected choice must equal safe_global_normalization
- selected-option probability must clear a configurable threshold
- confidence must clear a configurable threshold if appropriate

Do not reuse H2's ACCEPT/REJECT gate mechanically because this is a 3-way Choice problem.

Document the thresholds and make them configurable.

Tests MUST pin these known behavior examples:

NEURO -> neurological
expected:
safe_global_normalization

ENDO -> endocrine
given H5 contains Endocrine and Endoscopic
expected:
context_required

IMG -> diagnostic imaging
expected:
not safe_global_normalization

CONSULT -> palliative consultation
expected:
unsafe_normalization

Offline unit tests must not call TypeSafe.
Use fixtures/mocked stored responses for API behavior.

==================================================
6. CONTEXT-REQUIRED NORMALIZATION
==================================================

Do not discard tokens classified context_required.

Represent them as MULTIPLE possible expansions.

Example:

ENDO
-> endocrine
OR
-> endoscopic

Then use the FULL normalized description and H5 structural service vocabulary to see whether surrounding words eliminate one interpretation.

Example:

EXT ENDO THEATRE TIME

The surrounding words may deterministically support:
Extended Endocrine Theatre Time.

MET ENDO PROC

The surrounding words may deterministically support:
Metabolic Endoscopic Procedure.

Prefer deterministic structural resolution after Jev identifies that a token is context-sensitive.

Jev should not be called repeatedly where ordinary candidate intersection resolves the description.

==================================================
7. H5 STRUCTURAL MATCHER
==================================================

Build on H4's structural matcher philosophy.

Each H5 service should be represented from contract structure, likely:

qualifier
specialty
concept

or the actual structure discovered from H5 contract names.

A description can produce:

MATCHED
AMBIGUOUS
UNKNOWN

MATCHED:
exactly one contracted service is defensibly identified.

AMBIGUOUS:
one or more contracted services remain viable but text is missing a discriminator or contains unresolved contextual language.

UNKNOWN:
the description positively conflicts with all known contracted services or otherwise cannot defensibly refer to one.

Never use:
- billed unit price
- billed line total
- invoice total
- invoice ID
- patient ID

as service identity evidence.

Unit basis may be used ONLY as a secondary tie-break when:
- text already leaves multiple genuine candidate services
- exactly one candidate has compatible contracted unit basis

If unit basis decides identity:
set used_unit_basis_for_identity = true

Then that same line MUST NOT later be accused of wrong_unit_basis.

Preserve the pre-tiebreak candidate set.

==================================================
8. MISSING-WORD AMBIGUITY WITH JEV
==================================================

This is the second major Jev use.

Do not send all hard descriptions to a general LLM first.

NO OpenRouter classifier.
NO general LLM proposal step by default.

The flow is:

deterministic normalization
-> structural matching
-> bounded candidate set
-> Jev only for unresolved missing-word identity
-> deterministic pricing

For each recurring ambiguous DESCRIPTION CLUSTER where text leaves a small bounded set of contract candidates, construct a Jev Choice question dynamically.

State should contain:

- normalized description
- occurrence count
- explicit textual evidence
- observed unit basis only as secondary evidence
- ALL viable candidate services
- for every candidate:
    service_name
    qualifier
    specialty
    concept
    unit_basis
    which text fields match
    which are missing
    which explicitly contradict
- allowed evidence
- forbidden evidence

Absolutely exclude:
- billed unit prices
- base rates where not needed for semantic identity
- adjusted rates
- line totals
- invoice totals
- "which candidate makes the invoice math work"
- competitor predictions

Choice criteria should contain:

one option per viable contract service
PLUS:
- ambiguous_contracted_service
- none_of_the_above_or_unknown

Example schema:

{
  "missing_word_resolution": {
    "type": "choice",
    "instructions": {
      "question": "Which interpretation is best supported ...?",
      "focus": "Choose one service only if allowed non-price evidence distinguishes it from all alternatives."
    },
    "criteria": {
      "<candidate_1>": {...},
      "<candidate_2>": {...},
      "ambiguous_contracted_service": {...},
      "none_of_the_above_or_unknown": {...}
    }
  }
}

Tests MUST include the two cases already validated manually:

CASE A:
normalized description:
"comprehensive consultation"

Relevant contract candidate:
Comprehensive Palliative Consultation

Other H5 consultation services have conflicting qualifiers and/or unit bases.

Expected Jev classification:
Comprehensive Palliative Consultation

CASE B:
constructed stress test:
"supervised consultation"
unit basis:
per_procedure

Real H5 services include:
- Supervised Palliative Consultation
- Supervised Vascular Consultation

Both have:
- qualifier = supervised
- concept = consultation
- unit basis = per_procedure

The missing specialty is the ONLY discriminator.

Expected Jev classification:
ambiguous_contracted_service

This test is important:
Jev must be capable of refusing to invent a missing specialty.

Again:
offline tests must use fixtures/mocks, not real network calls.

==================================================
9. CACHE / CLUSTER SEMANTIC DECISIONS
==================================================

Never call Jev line-by-line.

There are many line items but far fewer distinct descriptions.

Cluster first.

One semantic judgment should apply to the normalized description cluster when appropriate.

Persist reviewed cluster decisions with provenance.

Any semantic artifact must be invalidated if relevant inputs change.

Fingerprint at least:
- H5 contract
- matcher version
- normalization version
- Jev state/question template
- model/version
- cluster content

Do not silently reuse stale semantic decisions.

==================================================
10. MAKE JEV EARN ITS PLACE
==================================================

This is critical.

H2's semantic stage added essentially zero verified final coverage.

Do NOT repeat that.

Create an explicit H5 semantic contribution report showing at least:

DETERMINISTIC BASELINE
- raw descriptions
- normalized clusters
- MATCHED clusters
- AMBIGUOUS clusters
- UNKNOWN clusters
- identified lines
- ambiguous lines
- invoices reconstructable
- expected_total blanks

AFTER SAFE GLOBAL NORMALIZATION
- extra clusters resolved
- extra lines resolved
- extra invoices reconstructable
- blanks removed

AFTER CONTEXTUAL NORMALIZATION
- extra clusters resolved
- extra lines resolved
- extra invoices reconstructable
- blanks removed

AFTER JEV MISSING-WORD RESOLUTION
- number of Jev-reviewed clusters
- accepted specific-service decisions
- ambiguous decisions
- unknown decisions
- extra lines resolved
- extra invoices reconstructable
- blanks removed

AFTER FINANCIAL-EQUIVALENCE LOGIC
- additional semantically ambiguous lines/invoices with exact financial result
- additional expected totals recovered

FINAL
- exact expected totals
- remaining blanks
- why each remaining class of blanks still cannot be safely reconstructed

If Jev adds little value, report that honestly.
Do not manufacture usefulness.

==================================================
11. FINANCIAL EQUIVALENCE
==================================================

This is the key improvement after semantic resolution.

Semantic ambiguity DOES NOT automatically imply financial ambiguity.

Suppose a line could be:

Service A
or
Service B

If both candidates produce the same corrected monetary result under the actual invoice context, the line is financially resolved even if semantic identity remains ambiguous.

Implement this explicitly.

Internal concepts should distinguish at least:

- identity_resolved
- semantic_ambiguous_but_financially_resolved
- financially_ambiguous
- pricing_complete
- correction_reconstructable

Do not necessarily expose these exact field names in submission.csv, but retain them in internal artifacts/debug reports.

Rule:

expected_total_cents should be blank ONLY when remaining uncertainty can materially change the corrected invoice total.

Do not blank merely because:
- exact service name is ambiguous
- a date is malformed but date does not affect the amount
- contract number is wrong but pricing is still reconstructable
- some non-financial finding exists

Detection and reconstruction are separate questions.

==================================================
12. DEPENDENCY-AWARE UNCERTAINTY
==================================================

Do not let one ambiguous line contaminate unrelated global state.

For every ambiguous candidate set determine which H5 rules the uncertainty could affect.

Possible dependencies include:

- patient/service/day premium threshold
- daily cap
- bundle membership
- directed exclusion
- cumulative volume discount
- duplicate identity
- weekend uplift
- facility multiplier
- plan tier multiplier

If all candidates have identical effect on a particular rule, that rule is resolved.

If candidate identity matters to only one global index, propagate uncertainty only there.

Use possible/certain state or intervals rather than a simplistic:
"one ambiguous line => everything afterward unknown".

Reuse/adapt the best candidate-set and interval logic already present in H1/H4.

Avoid combinatorial brute-force enumeration of every possible world if deterministic possible/certain bounds are sufficient.

==================================================
13. OCCURRENCE-PRESERVING INPUT
==================================================

Hospital 5 contains reused invoice IDs.

Do NOT deduplicate input records before auditing them.

Preserve every physical JSONL occurrence.

Create an internal occurrence identity separate from invoice_id.

Use the contract's actual ordering rules.

Invoice-level output still follows task requirements, but duplicate/reused IDs must be detected from preserved occurrences.

==================================================
14. CONTRACT PARSER
==================================================

Implement a strict H5 parser.

Parse and validate:

metadata:
- contract number
- provider
- payer
- term
- currency
- rounding

facilities:
- F-MAIN
- F-NORTH
- F-COAST

plan tiers:
- BRONZE
- SILVER
- GOLD

Table 1:
- every service
- unit basis
- base rate
- daily cap

Table 2:
- service x facility multiplier

Table 3:
- service x plan-tier multiplier

Sections 5+:
- premiums
- weekend/non-business-day uplifts
- bundles
- cumulative volume discounts
- exclusions
- invoice/date/duplicate rules

Add parser integrity checks:
- every multiplier row references a valid Table 1 service
- every adjustment references a valid service
- every bundle member exists
- every exclusion member exists
- every discount threshold is internally valid
- all Table 1 services have required facility/tier entries
- no silent missing rule rows

Do not rely on a manually duplicated service list where parsing is practical.

==================================================
15. EXACT PRICING
==================================================

Use Decimal or exact integer-cent-safe arithmetic.

H5 Section 3 pricing order is:

1. bundled base-rate substitution, if applicable
2. facility multiplier
3. plan-tier multiplier
4. premium OR uplift as the contract specifies
5. cumulative volume discount
6. final unit rate x payable quantity

ROUND HALF UP AFTER EVERY STEP.

Even multiplier 1.0 still takes the rounding step.

Never use binary float for money.

Add tests where intermediate rounding changes the final cent result.

==================================================
16. PREMIUMS / DAILY AGGREGATES
==================================================

Read exact H5 wording.

Where premiums depend on aggregate quantity for:
(patient, service, service date)
aggregate across invoices where the contract requires it.

Build the global indexes before line-level pricing.

Ambiguous service identities must contribute to:
- certain counts
- possible counts

Only assert a premium omission/incorrect application when justified under the uncertainty model.

==================================================
17. WEEKEND / NON-BUSINESS-DAY UPLIFTS
==================================================

Implement exact Section 6 behavior.

Business day is based on the contract definition.

Do not apply a weekend uplift to every service.
Only services named by the contract qualify.

Malformed service dates:
- still flag malformed_service_date
- but do not automatically destroy pricing if the date cannot affect that service's monetary result
- if date uncertainty can change weekend uplift, premium grouping, bundle membership, exclusion window, discount ordering, etc., propagate only that uncertainty

==================================================
18. BUNDLES
==================================================

Implement exact Section 7 bundle behavior.

Where the contract says both paired services for the same patient/day receive substituted bundle rates, apply the substitution to both services as stated.

Bundle substitution happens BEFORE facility/tier/premium/discount adjustments.

Bundle detection may require cross-invoice context.

Ambiguous candidates:
if all viable candidates have the same bundle behavior and same financial result, do not mark money unresolved.

==================================================
19. CUMULATIVE VOLUME DISCOUNTS
==================================================

Read Section 8 extremely carefully.

Implement cumulative utilization across the agreement as defined by the contract.

Known H5 wording uses "subsequent units" beyond thresholds.

Do NOT simply apply a discount to an entire line because a line crosses a threshold unless the contract explicitly says so.

Add boundary tests such as:

prior utilization = 79
current quantity = 5
threshold = more than 80 units

Determine exactly which units, if any, receive the discount from contract wording.

Test:
- exactly at threshold
- crossing threshold inside a line
- after threshold
- multiple threshold tiers
- deeper discount replacing earlier discount where applicable
- chronological ordering
- line-ID tie ordering if contract specifies it

Record the interpretation in DECISION_LOG.md.

==================================================
20. DAILY CAPS
==================================================

Do NOT copy H4 daily-cap semantics automatically.

Inspect the exact H5 language.

Table 1 contains Daily Cap values, but determine from the agreement whether:
- excess quantity is explicitly non-payable
- cap only establishes a violation
- another interpretation applies

Then implement exactly what H5 says.

If the wording is ambiguous:
- choose a defensible documented interpretation
- write it in DECISION_LOG.md
- add boundary tests
- do not silently assume H4 behavior

Detection and correction reconstruction may differ.

==================================================
21. EXCLUSIONS
==================================================

Implement all H5 directed exclusion rules exactly.

Respect:
- directionality
- date/window boundaries
- same-patient requirements
- service identity uncertainty

Add boundary tests around every exclusion window.

Do not reverse directed exclusions unless contract wording does so.

==================================================
22. DUPLICATES / DATA INTEGRITY
==================================================

Implement exact H5 Section 10 behavior for:

- contract number mismatch
- service date outside agreement term
- service date after invoice date
- malformed service date
- duplicate invoice ID
- same service billed twice for same patient/service date
- cross-invoice duplicates
- line-total arithmetic mismatch
- invoice-total mismatch
- wrong unit basis
- unknown service

Preserve all findings that apply.
Do not force one-category-only internal logic.

Convert to submission error_category according to the project's existing policy.

==================================================
23. CONFIDENCE
==================================================

Do not claim H5 confidence is calibrated probability.
H5 has no labels.

Confidence should reflect evidence strength.

Keep documentation honest.

Jev's own Choice probabilities/confidence are semantic-review evidence and are not the same thing as the final invoice confidence field.

Final invoice confidence should account for:
- service identity certainty
- rule certainty
- pricing completeness
- reconstructability
- unresolved ambiguity

Follow existing project conventions unless there is a clearly justified improvement.

==================================================
24. NO GENERAL LLM IN H5 V1
==================================================

Do NOT use:
- OpenRouter
- Gemini
- GPT
- Claude API
- embeddings
- an LLM proposer

for H5 service identification in the first implementation.

Use:

deterministic broad candidate generation
+ Jev normalization classification
+ deterministic structural matching
+ Jev bounded missing-word Choice
+ deterministic financial-equivalence analysis
+ deterministic contract pricing

Only add another model later if a measured report proves a remaining class of unresolved descriptions where it would have a clear role.

Do not repeat the H2 architecture of:
LLM proposes -> Jev verifies -> almost nothing accepted.

==================================================
25. JEV ONLINE/OFFLINE WORKFLOW
==================================================

The repository must remain reproducible without network access once reviewed artifacts exist.

Implement both:

A. direct TypeSafe execution when TYPESAFE_API_KEY/JEV_API_KEY is available

B. export/import workflow compatible with manually running requests in TypeSafe Playground

Follow H2's existing provenance approach where useful.

Tests must require no API keys.

The normal final H5 audit should be able to run offline from committed reviewed semantic artifacts.

If semantic artifacts are stale relative to the contract/matcher/question templates:
fail clearly rather than silently using them.

==================================================
26. CLI
==================================================

Integrate H5 cleanly into src/main.py following existing patterns.

Provide discoverable commands for things like:

- H5 audit
- H5 semantic status
- generate normalization candidates
- export Jev normalization requests
- run Jev normalization requests directly
- import normalization results
- export missing-word Jev requests
- run missing-word Jev requests directly
- import missing-word results
- semantic contribution report
- regenerate H5 submission

Exact CLI names should match current project conventions.

Do not break existing commands.

==================================================
27. OUTPUTS / ARTIFACTS
==================================================

Generate H5 output using the exact submission schema required by EXERCISE.md:

invoice_id
flagged
error_category
expected_total_cents
billed_total_cents
confidence

Include EVERY unique H5 invoice ID required by the task.

Do not omit correct invoices.

For unsupported exact totals:
expected_total_cents may remain blank.

But our goal is to make blanks rare by proving more totals, not by filling them with guesses.

Never use:
expected_total = billed_total
as a fallback merely to avoid blanks.

Never carry billed line totals into expected totals for unresolved lines unless the contract independently proves the billed amount is correct under every viable interpretation.

Integrate H5 into:
outputs/submission.csv

without changing H2/H4 rows unexpectedly.

Create useful H5 diagnostic artifacts such as:
- service mapping report
- unresolved cluster report
- Jev contribution report
- finding counts
- reconstruction coverage
- blank-reason report
- contract parse summary

==================================================
28. TESTS
==================================================

Add extensive Hospital 5 tests.

At minimum cover:

CONTRACT
- metadata
- service count
- facility/tier matrices
- premiums
- uplifts
- bundles
- discounts
- exclusions

NORMALIZATION
- suffix stripping
- spelling normalization
- broad candidate generation
- safe global mapping
- context-required mapping
- unsafe mapping
- stale artifact rejection

JEV
- correct request schema
- root question ID
- Choice criteria
- offline fixtures
- NEURO -> safe
- ENDO -> context_required
- IMG -> not globally safe
- invented specialty -> unsafe
- comprehensive consultation -> specific service
- supervised consultation -> ambiguous

MATCHING
- exact
- reordered
- abbreviations
- missing qualifier
- missing specialty
- unknown words
- multiple candidates
- unit-basis tie-break
- cannot later flag wrong_unit_basis if unit basis was used for identity

PRICING
- HALF_UP after every stage
- facility multiplier
- plan multiplier
- premium/uplift
- bundles
- discounts
- crossing discount threshold inside quantity
- deeper discount tier
- daily cap interpretation
- exclusions

GLOBAL
- cross-invoice daily aggregates
- duplicate IDs
- duplicate services
- bundle across invoices
- cumulative discount across invoices
- ambiguity propagation

FINANCIAL EQUIVALENCE
- two identities same corrected amount -> total remains reconstructable
- two identities different corrected amount -> unresolved
- ambiguity irrelevant to unrelated global rules
- ambiguous candidate only affects one dependency

DATA INTEGRITY
- malformed dates
- term violations
- invoice-date ordering
- contract mismatch
- arithmetic mismatch

REGRESSION
- all existing H1/H2/H4 tests still pass
- existing H1/H2/H4 output hashes remain byte-stable where expected

==================================================
29. QUALITY / STYLE
==================================================

Keep code readable enough to explain to a reviewer.

Use:
- small functions
- dataclasses where useful
- type hints
- clear comments explaining WHY, not obvious syntax
- deterministic ordering everywhere
- no hidden randomness
- no magic undocumented thresholds
- no silent exception swallowing

Do not over-engineer with a database.
This dataset is small enough for in-memory Python indexes.

Do not add SQLite/DuckDB unless a concrete existing repository requirement makes it necessary.

==================================================
30. DOCUMENTATION
==================================================

Update:

README.md
DECISION_LOG.md
.env.example if new H5-specific settings are added

Document:

- H5 architecture
- normalization strategy
- why Jev is used
- exact Jev question types
- why no general LLM is used
- semantic contribution metrics
- financial-equivalence concept
- remaining uncertainty
- H5 cap interpretation
- H5 threshold-crossing interpretation
- confidence limitations
- reproduction commands

Preserve prompt provenance.

Store the exact Jev state/question templates used for the final solution under prompts/ or the project's equivalent provenance location.

Do not claim "all prompts" unless that is actually true.

==================================================
31. COMPETITOR REPOS
==================================================

Do not use other public solutions as labels.

Do not majority-vote against them.

Do not copy their mappings or totals into ours.

Only after our H5 implementation is complete, you may optionally compare high-level counts as a DIFFERENTIAL SANITY CHECK.

A competitor agreeing with us is not proof.
A competitor disagreeing with us is not proof.

The H5 contract and invoices are the source of truth.

==================================================
32. SUCCESS CRITERIA
==================================================

The implementation is complete only when:

1. Full existing test suite passes.
2. New H5 test suite passes.
3. H1/H2/H4 behavior is preserved.
4. H5 audit runs end-to-end.
5. Every required H5 invoice ID appears in H5 submission output.
6. Jev semantic review can be reproduced/direct-run or exported/imported.
7. No runtime general LLM is required.
8. No service identity uses billed price.
9. Ambiguous candidate sets are preserved.
10. Semantic ambiguity is separated from financial ambiguity.
11. Exact totals are filled whenever every viable interpretation leads to the same corrected amount.
12. Remaining blanks have explicit defensible reasons.
13. A before/after contribution report proves exactly what normalization, Jev, and financial-equivalence logic added.
14. outputs/submission.csv includes H2 + H4 + H5 correctly.
15. README and DECISION_LOG accurately describe what was implemented.

==================================================
33. FINAL REPORT TO ME
==================================================

When finished, do not just say "implemented".

Give me:

- files created/changed
- full test result
- H5 dataset counts
- number of raw descriptions and normalized clusters
- deterministic MATCHED / AMBIGUOUS / UNKNOWN counts before Jev
- number of vocabulary candidates sent to Jev
- Jev normalization result counts:
    safe_global_normalization
    context_required
    unsafe_normalization
- number of missing-word clusters reviewed by Jev
- number resolved to a service
- number kept ambiguous
- number classified unknown
- exact additional line/invoice coverage caused by Jev
- exact additional invoice totals recovered by financial equivalence
- final H5 flagged count
- final H5 pricing_complete count
- final H5 correction_reconstructable count
- final H5 blank expected_total count
- top reasons for remaining blanks
- finding counts by category
- whether any decisions remain questionable
- confirmation that H1/H2/H4 outputs remained unchanged
- exact commands to reproduce H5 and the combined submission

Most importantly:

Do not optimize for "filling cells".

Optimize for:
MAXIMUM PROVABLE INFORMATION.

A blank is acceptable when the contract/data genuinely cannot determine the result.

A blank is NOT acceptable merely because our matcher was too weak to extract information that is actually present.
