# Prompt 003 — Hospital 5 pre-commit validation

- **Used with:** Claude Code (Claude Opus 5.5)
- **Kind:** development prompt to the coding assistant (not a runtime prompt; the code does not load this file)
- **Produced:** removal of the single-candidate closure gate; the review-slot guard; the `bd → bedside` override and the 2-letter-token invariant; the missing-word fingerprint change and re-stamp; adversarial and boundary tests; text-only justifications in `outputs/hospital_5/decision_log.md` (section G); regenerated H5 outputs and combined submission
- **Preceded by:** [`000_implement_hospital_5.md`](000_implement_hospital_5.md)
- **Provenance:** saved when issued (2026-09-24), copied verbatim from the session.

Transcribed verbatim below.

---

Do a focused pre-commit validation of the current Hospital 5 implementation.

Do NOT broadly refactor H5 and do NOT touch H1/H2/H4 unless absolutely necessary.

The implementation is currently producing:

- 1,050 H5 invoice IDs
- 1,032 correction-reconstructable totals
- 18 blanks
- 76 flagged
- 13,209 / 13,221 lines identified
- 672 tests passing

Before we commit, I want you to challenge the few decisions that could materially inflate or weaken the result.

The goal is NOT to preserve the current 1,032 total count.

The goal is:

MAXIMUM DEFENSIBLE COVERAGE.

If coverage has to decrease because a rule is not sufficiently supported, decrease it.

==================================================
1. FIRST: DO NOT MODIFY ANYTHING
==================================================

Start by inspecting the current uncommitted Hospital 5 implementation, especially:

- src/hospital_5/*
- tests/hospital_5/*
- outputs/hospital_5/decision_log.md
- outputs/hospital_5/semantic_contribution.md
- artifacts/hospital_5/*
- prompts/hospital_5/*
- README.md
- DECISION_LOG.md
- .env.example

Also read the H5 source of truth again:

- contracts/hospital_5/network_reimbursement_agreement.md
- invoices/hospital_5_invoices.jsonl

Run the current full test suite and confirm the current baseline.

Do not make changes until the investigation below is complete.

==================================================
2. PRIORITY #1: AUDIT THE FOUR POST-HOC SINGLE-CANDIDATE CLOSURE CLUSTERS
==================================================

This is the most important review.

Current behavior:

There are four missing-word clusters where:

- only one contracted candidate remains
- Jev selected that candidate
- Jev candidate probability was approximately 0.80–0.89
- none_of_the_above probability was 0
- the normal 0.90 gate would reject them
- a post-hoc rule was added:
  H5_JEV_SINGLE_CANDIDATE_CLOSURE
- enabling this rule increases proven totals from about 706 to 1,032
- therefore these four clusters affect approximately 326 invoice totals

This is too material to accept without direct review.

For EACH of the four clusters, produce a review table containing:

- raw description examples
- normalized description
- occurrence count
- line count
- invoice count affected
- Jev selected candidate
- full Jev probability distribution
- Jev confidence
- exact missing words
- explicit words present
- unit basis
- all contract candidates BEFORE deterministic elimination
- all candidates AFTER deterministic elimination
- why each rejected contract candidate was eliminated
- whether elimination came from:
  - explicit textual contradiction
  - normalization
  - unit basis
  - absence of a qualifier
  - absence of a specialty
  - absence of a concept
  - another rule
- whether the one remaining candidate is truly identified by positive evidence
- whether it is merely the last surviving candidate because every other option was eliminated
- whether an unknown/uncontracted service could still plausibly explain the description
- how many expected totals depend on accepting this mapping

IMPORTANT DISTINCTION:

"Only one contracted candidate remains"

does NOT automatically mean:

"The service is known."

We need to distinguish:

A. positive identification
from
B. elimination-only uniqueness

If the description says enough that the remaining service is genuinely identifiable, accepting it can be defensible.

If the service is only unique because the matcher eliminated everything else but a discriminator is still absent, that should probably remain ambiguous.

Do not use:
- billed unit price
- line total
- invoice total
- matching contract rate
- competitor solutions

to justify identity.

==================================================
3. EVALUATE THE SINGLE-CANDIDATE CLOSURE RULE ITSELF
==================================================

After reviewing the four clusters, decide whether the rule:

H5_JEV_SINGLE_CANDIDATE_CLOSURE

is methodologically justified.

Do NOT justify it with:

"it improves coverage."

Do NOT justify it with:

"none_of_the_above was zero."

Do NOT justify it only because:

"Jev chose the candidate."

Instead ask:

Does the state contain sufficient non-price semantic evidence to identify the service?

Consider whether the rule should become one of these instead:

OPTION A:
Keep the current single-candidate closure rule.

OPTION B:
Require stronger positive evidence, such as:
- qualifier present
- concept present
- specialty inferred only when every competing contracted service is explicitly contradicted
- no unknown token that could be the missing discriminator

OPTION C:
Require a lower but explicit Jev probability threshold only for single-candidate cases, with a documented rationale.

OPTION D:
Reject closure entirely and keep the clusters ambiguous.

If you change the rule, make the rule general and principled.

Do NOT special-case the four descriptions individually merely to preserve results.

==================================================
4. TEST THE CLOSURE RULE ADVERSARIALLY
==================================================

Create synthetic tests designed to break the rule.

Examples:

CASE 1:
A description contains qualifier + concept but omits specialty.
Only one contract service happens to share that qualifier/concept.

Question:
Is that enough to identify it?

CASE 2:
A description contains only the concept.
Only one contracted service survives due to the current vocabulary.

Question:
Should this still be accepted?

CASE 3:
A description contains an unrecognized token that could plausibly be a missing specialty.

Question:
Should single-candidate closure be blocked?

CASE 4:
A description has one candidate after unit-basis filtering, but the unit basis itself might be wrong.

Question:
Should closure happen, and can wrong_unit_basis still be detected?

CASE 5:
The description could plausibly refer to an uncontracted service even though only one contracted candidate remains.

Expected:
do not force a contracted identity merely because the candidate set size is one.

The closure rule should survive adversarial tests, not just current data.

==================================================
5. PRIORITY #2: REMOVE OR QUARANTINE THE KNOWN BAD NORMALIZATION
==================================================

Current known issue:

Jev approved:

bd -> bedside
P ≈ 0.96

This mapping is known to be wrong.

It currently changes no final output because the relevant description already contradicts every contract service.

Do NOT leave a known-wrong approved global normalization silently in the final artifact.

Investigate why Jev accepted it.

Then do one of:

- remove it from safe global normalization
- move it to rejected/context-required
- add a deterministic safety rule that prevents this class of false normalization
- mark it explicitly overridden with provenance

Prefer a general fix if possible.

For example, ask whether candidate-generation/review should require stronger lexical evidence for two-character abbreviations.

But do not introduce a rule that breaks valid mappings without testing it.

Add a regression test for `bd`.

==================================================
6. PRIORITY #3: REVIEW EXCLUSION DIRECTIONALITY
==================================================

Current implementation reportedly applies H5 exclusions in both directions and inclusive of day N.

This is questionable.

Read the exact H5 exclusion clauses.

For every exclusion rule, determine:

- trigger service
- excluded service
- direction
- time window
- whether same-day counts
- whether the boundary is inclusive or exclusive
- whether reverse direction is stated anywhere

Do NOT infer symmetry merely because the dataset happens to contain violations in one direction.

If the contract expresses:

A after B is excluded

then do not automatically implement:

B after A is excluded

unless the text supports it.

Create tests for:

- stated direction
- reverse direction
- day 0
- exact boundary day N
- N+1
- ordering across invoices

If current behavior is wrong, fix it even if finding counts change.

Document the exact interpretation in the H5 decision log.

==================================================
7. PRIORITY #4: REVIEW CUMULATIVE DISCOUNT THRESHOLD CROSSING
==================================================

Current interpretation reportedly is:

- a line receives the discount tier already exceeded BEFORE that line
- a line crossing a threshold is not split
- billed data from five lines was noted as supporting this behavior

That billed-data evidence must NOT determine contract interpretation.

Read H5 Section 8 again.

Focus on wording such as:

"subsequent units"

Determine from contract language alone whether:

Example:

prior utilization = 79
current quantity = 5
threshold = more than 80 units

means:

A.
all 5 units use the pre-threshold rate

B.
first units up to threshold use old rate, later units use discounted rate

C.
all 5 units receive the new rate

or another interpretation.

Do this for every discount structure, including multiple tiers.

Add explicit tests for:

- prior = threshold - 1, qty = 1
- prior = threshold - 1, qty = 2
- prior = threshold, qty = 1
- prior = threshold + 1
- crossing first threshold
- crossing second threshold
- one line crossing two tiers if possible
- chronological ordering
- same-date ordering by line ID if specified

Do not use billed price agreement as proof.

If the contract is genuinely ambiguous:
- choose the most defensible interpretation
- explain alternatives
- document the ambiguity
- do not pretend certainty

==================================================
8. PRIORITY #5: RECHECK DAILY-CAP SEMANTICS
==================================================

Current behavior:

- daily-cap breach proves an error
- expected total remains blank because H5 gives only a bare "Daily cap" column
- six blank totals are caused by this

This is conservative and may be correct.

Re-read the exact H5 wording around Table 1 and any clauses referring to caps.

Determine whether the contract says:

- excess quantity is non-payable
- billed quantity must be capped
- cap only establishes a violation
- something else

Do NOT import H4 semantics automatically.

Do NOT use H1 labels as H5 ground truth.

If the contract does not tell us what the correct delivered/payable quantity should be, keeping the expected total blank is acceptable.

Only change this if H5 text itself supports reconstruction.

==================================================
9. FACILITY SOURCE REVIEW
==================================================

Current implementation reportedly uses the invoice-header facility because the dataset has no line-level facility, while clause 1.2 says:

"facility code recorded on a line item"

Verify the actual H5 JSONL schema.

If line items truly have no facility field:

- document the schema-contract mismatch
- determine whether invoice-level facility is clearly intended to apply to every line
- check whether any invoice could contain multiple facility contexts
- do not invent line-level values

If header facility is the only available facility evidence, keeping it may be reasonable, but document that this is a data-schema interpretation rather than exact textual compliance.

==================================================
10. UNKNOWN SERVICES
==================================================

Current behavior:

12 unknown services
-> expected totals blank

Keep the existing principle unless the contract independently proves a payable amount.

Do NOT copy H1's behavior of carrying billed amount.

Do NOT use:

expected = billed

as a fallback.

==================================================
11. MEASURE THE EFFECT OF EVERY REVIEW
==================================================

After each change, recompute:

- identified lines
- MATCHED / AMBIGUOUS / UNKNOWN clusters
- Jev-resolved clusters
- pricing_complete invoices
- correction_reconstructable invoices
- blank totals
- flagged invoices
- finding counts
- combined submission rows

Especially show:

A. current result with closure enabled
B. current result with closure disabled
C. final result after this review

For each of the four closure clusters, show how many totals depend on it.

==================================================
12. PRESERVE JEV CONTRIBUTION HONESTLY
==================================================

Even if the closure gate is removed, Jev still appears to add substantial coverage.

Recalculate exact contribution after finalizing the rules.

Do not report:

"Jev added 744 totals"

if that number includes totals that depend on a post-hoc rule that we later reject.

Produce final stage-by-stage contribution metrics based on the final methodology.

==================================================
13. DO NOT TUNE AGAINST FINAL COVERAGE
==================================================

This is important.

Do NOT make a rule because it raises:

706 -> 1,032

or because it keeps the result close to another solution.

The only acceptable reason for a rule is:

the contract + invoice semantics support it.

If final defensible coverage is:

1,032
good.

If it becomes:

900
also acceptable.

If it becomes:

706
also acceptable.

Correct methodology matters more than a high filled-total count.

==================================================
14. AFTER INVESTIGATION, IMPLEMENT ONLY NECESSARY FIXES
==================================================

Once the review is complete:

- make only justified changes
- add regression tests
- update H5 artifacts
- update H5 decision log
- update semantic contribution report
- update README/root DECISION_LOG only if final behavior changes materially
- regenerate outputs
- regenerate combined submission

Do not commit.

==================================================
15. REGRESSION REQUIREMENTS
==================================================

Run:

- all Hospital 5 tests
- full repository test suite

Existing H1/H2/H4 outputs and artifacts must remain byte-identical.

Verify explicitly.

Use the existing CRLF-safe diff check where appropriate:

git -c core.whitespace=cr-at-eol diff --check

==================================================
16. FINAL REPORT
==================================================

When finished, give me a concise but evidence-heavy report with these sections:

1. SINGLE-CANDIDATE CLOSURE REVIEW

For each of the four clusters:
- description
- missing discriminator
- Jev probability
- line count
- invoice count
- final decision
- why

2. CLOSURE RULE VERDICT

State whether:
- kept unchanged
- tightened
- replaced
- removed

and explain the general rule.

3. BD NORMALIZATION

Explain:
- why Jev got it wrong
- what changed
- whether any output changed

4. EXCLUSION RULES

State exact final direction/boundaries and whether previous behavior changed.

5. DISCOUNT THRESHOLD CROSSING

State exact final interpretation and contract basis.

6. DAILY CAP

State exact final interpretation and whether blanks remain.

7. FACILITY SOURCE

State whether invoice-level facility remains and why.

8. FINAL COVERAGE

Report:
- identified lines
- pricing_complete
- correction_reconstructable
- blank totals
- flagged
- finding counts

9. JEV VALUE

Report final measured incremental contribution from:
- safe normalization
- contextual normalization
- missing-word Jev
- single-candidate closure, if retained
- financial equivalence

10. REGRESSIONS

Confirm:
- full test count/pass
- H1/H2/H4 unchanged
- combined submission row count
- no commit made

Most important:

Challenge the current H5 result.

Do not defend it merely because we built it.

I want the highest-coverage result that survives an adversarial methodological review.
