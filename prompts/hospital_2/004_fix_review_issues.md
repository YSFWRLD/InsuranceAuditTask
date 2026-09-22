# Prompt 004 — fix the remaining Hospital 2 review issues (development)

- **Used with:** Claude Code (Claude Opus 5)
- **Kind:** development prompt to the coding assistant, not a runtime prompt
- **Preceded by:** [000](000_implement_hospital_2.md)
- **Produced:** the count terminology, the Playground `type: choice` Jev format (prompt [003](003_jev_verifier.md), which superseded [002](002_jev_verifier.md)), duplicate-invoice serialisation, and blank `expected_total_cents` when a total cannot be reconstructed
- **Provenance:** Recovered verbatim from the Claude Code session transcript on 2026-09-22 (it was not saved to the repository when it was issued).

Transcribed verbatim below.

---

Fix the remaining Hospital 2 issues identified during review.

Hospital 1 is frozen.
Hospital 2 deterministic auditing is already implemented and passing tests.

This is a targeted cleanup/fix task.

DO NOT:
- rewrite H2
- refactor the overall architecture
- change contract rules without evidence of a bug
- run or fake GLM results
- run or fake Jev results
- implement H3-H5
- change H1 behavior

I do not currently have the OpenRouter or Jev API keys configured.
I WILL add both later.

Therefore:
- prepare the integrations completely
- preserve offline/export workflows
- do not require either API to complete this task
- never fabricate API responses

Do not stop after proposing a plan.
Make the changes, run tests, rerun H1/H2, and finish the task.

==================================================
ISSUE 1 — FIX SEMANTIC COUNT TERMINOLOGY
==================================================

The current documentation/reporting mixes several different H2 counts.

These are different concepts and must be reported separately.

Observed current state:

- 441 normalized description clusters total
- 358 deterministically matched clusters
- 14 deterministic UNKNOWN clusters
- 69 clusters requiring semantic classification
- those 69 semantic clusters represent approximately 2,093 line occurrences
- approximately 384 of those lines currently have provisional unit-basis tie-break resolution
- approximately 1,709 lines remain actually AMBIGUOUS in the current audit
- unresolved.json currently contains 83 clusters because it includes:
    69 semantic-pending clusters
    +
    14 deterministic UNKNOWN clusters

VERIFY all counts from the generated artifacts/code rather than blindly
hardcoding these numbers.

Then fix documentation, audit reports, CLI summaries, README text, and any
generated reports so the terminology is precise.

Use distinct names such as:

total_normalized_clusters
deterministic_matched_clusters
deterministic_unknown_clusters
semantic_pending_clusters
semantic_pending_line_occurrences
currently_ambiguous_line_occurrences
unit_basis_provisional_line_occurrences
total_unresolved_or_unknown_clusters

Do not say:

"69 clusters / 1,709 lines need semantic classification"

if the classifier actually processes 2,093 line occurrences represented by
those clusters.

Make the CLI/reporting self-explanatory.

==================================================
ISSUE 2 — IMPROVE JEV INTEGRATION
==================================================

The current Jev handoff should support TWO modes:

1. direct API mode for later, when I provide a Jev API key
2. manual Playground/batch mode as a fallback

Both modes must operate on the same verification cases and produce the same
normalized internal result format.

--------------------------------------------------
A. DIRECT JEV API MODE
--------------------------------------------------

Prepare direct Jev execution cleanly.

The intended future flow should be approximately:

OPENROUTER_API_KEY=...
JEV_API_KEY=...

python -m src.main semantic h2 classify
python -m src.main semantic h2 jev
python -m src.main audit h2
python -m src.main submission

Use environment/configuration for:

- Jev API key
- Jev model/version
- endpoint/base URL if necessary

Target model/version currently:

jev-1.13.0

IMPORTANT:

Do not invent undocumented Jev API behavior.

If the exact Jev HTTP/SDK contract is already known from existing code or
documentation available in the project, implement it correctly.

If it is not known, isolate the uncertain transport-specific code behind a
small adapter so the rest of semantic.py does not depend on guessed request
formats.

The business logic must remain independent of the transport.

Normalize the Jev result internally to something like:

{
  "choice": "ACCEPT | REJECT | UNCERTAIN",
  "probabilities": {
    "ACCEPT": 0.0,
    "REJECT": 0.0,
    "UNCERTAIN": 0.0
  },
  "confidence": 0.0,
  "model": "jev-1.13.0"
}

Then apply:

P(ACCEPT) >= 0.90
    -> accept classifier decision

P(REJECT) >= 0.90
    -> reject classifier proposal

otherwise
    -> unresolved

Remember:

If the classifier decision itself is AMBIGUOUS or UNKNOWN and Jev ACCEPTS it,
the final result remains AMBIGUOUS or UNKNOWN.

--------------------------------------------------
B. PLAYGROUND / MANUAL MODE
--------------------------------------------------

Improve the exported files so they are practical to use with Jev Playground.

Generate a proper State JSON and Questions JSON rather than a loose collection
of rendered prose prompts.

The exported format should resemble:

STATE

{
  "cases": {
    "h2_case_001": {
      "description": "...",
      "normalized_description": "...",
      "candidates": [...],
      "classifier": {
        "status": "...",
        "selected_service": "...",
        "confidence": ...,
        "reason": "..."
      }
    }
  }
}

QUESTIONS

{
  "verify_h2_case_001": {
    "type": "choice",
    "instructions": "Verify whether the classifier decision is justified by the description and supplied contract candidates.",
    "criteria": {
      "ACCEPT": "...",
      "REJECT": "...",
      "UNCERTAIN": "..."
    }
  },

  "verify_h2_case_002": {
    ...
  }
}

Use the actual Jev Playground syntax already used by this project:

{
  "verify_service_match": {
    "type": "choice",
    "instructions": "...",
    "criteria": {
      "ACCEPT": "...",
      "REJECT": "...",
      "UNCERTAIN": "..."
    }
  }
}

Do NOT use an invented:

{
  "question": "...",
  "options": [...]
}

format.

The Playground export should allow the whole semantic verification batch to be
run with minimal manual translation.

Files should be something like:

artifacts/hospital_2/jev_state.json
artifacts/hospital_2/jev_questions.json
artifacts/hospital_2/jev_results.json

--------------------------------------------------
C. ONE NORMALIZED IMPORT PATH
--------------------------------------------------

Whether Jev results come from:

- direct API
or
- Playground result import

they should ultimately pass through the same validation and decision-gating
code.

Do not maintain two different interpretations of Jev output.

Validate:

- case ID exists
- ACCEPT/REJECT/UNCERTAIN only
- probabilities are present
- probabilities are valid numbers from 0 to 1
- probabilities are internally sensible
- no result is silently attached to the wrong description cluster

Reject malformed imports visibly.

==================================================
ISSUE 3 — DUPLICATE INVOICE SERIALIZATION
==================================================

H2 contains seven reused invoice IDs representing different physical invoice
occurrences.

The internal audit is occurrence-safe and must remain that way.

The current final combination logic can produce a misleading result by:

- taking billed/expected money from the later occurrence
- while combining error findings from BOTH occurrences

Example problem:

occurrence 1:
    unit_price_mismatch

occurrence 2:
    duplicate_invoice_id

final row currently risks becoming:

    unit_price_mismatch|duplicate_invoice_id

while the billed_total_cents belongs only to occurrence 2.

Fix this.

For submission serialization of a duplicated invoice ID:

- select the later occurrence as the represented occurrence
- use the later occurrence's billed_total_cents
- use the later occurrence's expected_total_cents
- use the later occurrence's pricing/reconstructability state
- use findings belonging to that later occurrence
- ensure duplicate_invoice_id is included

DO NOT copy unrelated findings from the earlier occurrence into the submitted
row.

Earlier occurrences and all of their findings must still remain available in:

- detailed audit report
- findings.csv
- internal occurrence-level results

Do not discard evidence.

Add explicit tests for a duplicate invoice ID where:

occurrence A has one independent pricing error
occurrence B has only duplicate_invoice_id

and verify the final submission row does NOT inherit occurrence A's unrelated
pricing category.

Document this policy in the H2 decision log.

==================================================
ISSUE 4 — EXPECTED TOTALS VS RECONSTRUCTABILITY
==================================================

The current final submission may contain:

correction_reconstructable = false

while still emitting a numeric:

expected_total_cents

This is misleading.

The project principle is:

A numeric expected_total_cents is a claim that we know what the invoice should
have totalled.

Fix the submission behavior.

If:

correction_reconstructable == false

then final scored submission should leave:

expected_total_cents

blank/null rather than emitting a best-effort number.

Internally, the audit may still retain diagnostic calculations such as:

provisional_expected_total_cents

or other best-effort intermediate totals if useful.

But distinguish clearly between:

- diagnostically computed amount
- contractually defensible corrected total

The scored submission must use only the defensible corrected total.

Therefore:

if correction_reconstructable:
    expected_total_cents = exact corrected total
else:
    expected_total_cents = blank

Do not substitute:

- billed total
- zero
- partial calculated total
- guessed cap-adjusted amount

just to fill the CSV.

Check that the resulting CSV writer produces a clean blank field rather than
the literal strings:

None
null
NaN

Add tests.

==================================================
SEMANTIC STAGE AND RECONSTRUCTABILITY
==================================================

Once GLM and Jev are run later, more invoices may become reconstructable.

Therefore make sure this state updates automatically.

Example:

before semantic resolution:

AMBIGUOUS line
    -> pricing_complete = false
    -> correction_reconstructable = false
    -> expected_total_cents blank

after accepted semantic mapping:

MATCHED line
    -> deterministic pricing can proceed
    -> if no other blocker exists:
       correction_reconstructable = true
       expected_total_cents populated

No special manual patch should be needed after semantic resolution.

==================================================
OPENROUTER PREPARATION
==================================================

Keep the default classifier:

z-ai/glm-5.3-flash

but make it configurable.

When I later set:

OPENROUTER_API_KEY

I should be able to run the classifier without editing source code.

Use strict structured output.

Classifier schema remains:

{
  "selected_service": "canonical service name or null",
  "status": "MATCHED | AMBIGUOUS | UNKNOWN",
  "confidence": 0.0,
  "evidence_clause_ids": ["..."],
  "reason": "..."
}

Do not expose:

- billed unit price
- line total
- invoice total
- patient ID
- invoice ID

as service identity evidence.

Do not run OpenRouter during this task unless credentials already genuinely
exist.

==================================================
PROMPT FILES
==================================================

If the semantic prompt format changes because of the Jev improvements, update:

prompts/hospital_2/

Keep prompt versions clear.

Do not overwrite historical prompts in a misleading way.

If the semantic prompt meaning changes materially, create a new version such as:

003_jev_verifier.md

rather than pretending the earlier prompt was always written that way.

==================================================
TESTING
==================================================

Add/adjust tests for all four fixes.

At minimum test:

COUNT REPORTING
- semantic pending clusters vs line occurrences are distinguished
- deterministic UNKNOWN is not counted as semantic pending
- unit-basis provisional lines are reported separately

JEV
- Playground State export valid
- Playground Questions export valid
- question IDs map deterministically to cluster IDs
- ACCEPT >= .90 works
- REJECT >= .90 works
- weak ACCEPT remains unresolved
- weak REJECT remains unresolved
- ACCEPT of AMBIGUOUS preserves AMBIGUOUS
- ACCEPT of UNKNOWN preserves UNKNOWN
- malformed imported result fails safely
- API and manual-import results use the same gate

DUPLICATES
- occurrences remain separate internally
- later occurrence chosen for submission
- earlier occurrence findings do not contaminate later occurrence row
- duplicate_invoice_id remains present

RECONSTRUCTABILITY
- false -> blank expected_total_cents
- true -> exact expected_total_cents
- CSV contains blank, not None/null/NaN
- semantic resolution can transition an invoice from unreconstructable to reconstructable

==================================================
REGRESSION
==================================================

Before changes, run the current tests.

Current expected state is approximately:

325 tests passing

H1:
913 invoices
58 flagged
4 declined

H2:
1,132 occurrences
1,125 unique IDs
74 currently flagged
1,103 pricing_complete

Verify rather than blindly trusting these numbers.

After the fixes:

run the entire test suite

run:
    python -m src.main audit h1
    python -m src.main evaluate h1
    python -m src.main audit h2
    python -m src.main submission

H1 must remain unchanged.

H2 flagged counts may change ONLY if the duplicate-submission policy changes
how final invoice-ID rows are represented.

The underlying occurrence-level findings should not change merely because of
these cleanup fixes.

==================================================
DOCUMENTATION
==================================================

Update README and H2 decision log to explain:

- difference between cluster and line counts
- semantic-pending vs deterministic UNKNOWN
- direct Jev API mode
- Playground fallback
- duplicate invoice serialization policy
- blank expected totals when correction is not reconstructable
- semantic mappings can later restore reconstructability

Do not claim GLM or Jev has been run yet.

Do not claim H2 accuracy.

==================================================
GIT
==================================================

Do NOT commit these changes automatically.

Leave the changes in the working tree for review.

==================================================
FINAL RESPONSE
==================================================

When finished, report:

1. test count and result
2. H1 regression result
3. corrected H2 semantic counts:
   - total clusters
   - deterministic matched
   - deterministic UNKNOWN
   - semantic pending
   - semantic-pending line occurrences
   - currently ambiguous line occurrences
   - unit-basis provisional line occurrences
4. Jev direct API readiness
5. Jev Playground export format
6. duplicate serialization behavior
7. how many current submission rows now have blank expected_total_cents
8. H2 flagged count after the fix
9. files changed
10. confirmation that no external semantic results were fabricated
11. confirmation that nothing was committed

Perform the fixes completely.
