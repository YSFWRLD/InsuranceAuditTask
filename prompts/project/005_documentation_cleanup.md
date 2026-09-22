# Prompt P005 — documentation-only cleanup

- **Used with:** Claude Code (Claude Opus 5)
- **Preceded by:** [004](004_h1_h2_submission_readiness.md)
- **Produced:** wording fixes for the H3–H5 scope, the H1 holdout and the H2 classifier attempt counts, and a shorter root `DECISION_LOG.md`. No prediction, metric or semantic decision changed
- **Provenance:** saved when issued (2026-09-22), copied verbatim from the session.

Transcribed verbatim below.

---

Make the following documentation-only cleanup changes.

DO NOT change prediction logic.
DO NOT change H1/H2 audit behavior.
DO NOT rerun OpenRouter or Jev.
DO NOT start H3/H4/H5.
DO NOT alter current metrics or semantic decisions.
After edits, run the full test suite and report whether anything changed.

1. FIX H3–H5 SCOPE WORDING

Search README.md and root DECISION_LOG.md for wording equivalent to:

“H3–H5 were not attempted, and their contracts were never opened.”

That is too strong because H3–H5 were inspected during planning/comparison.

Replace it with wording equivalent to:

“H3–H5 were not implemented and were not used to produce submitted predictions.”

Keep the wording factual and concise.

Also search for any other statements elsewhere in the submission-facing documentation that incorrectly imply H3–H5 were never inspected/opened. Fix those too, but do not rewrite unrelated documentation.

2. FIX H1 HOLDOUT WORDING

In:

outputs/hospital_1/evaluation.md

find the sentence:

“The 30% holdout was not read while the system was being built.”

Replace it with wording equivalent to:

“The split was created before the final engine implementation, but because the full H1 labels had been visible during development, the holdout is reported only as a post-hoc check.”

Make sure this is consistent with the H1 evaluation report and other disclosure text.

Do not imply the holdout is an untouched validation set.

3. CORRECT H2 GLM CALL WORDING

Search the documentation for claims such as:

“69 clusters were sent once each”
“69 calls”
“69 requests total”

These are inaccurate.

The correct factual summary is:

- 69 semantic clusters required classification
- classification was attempted for all 69 clusters
- 65 clusters completed in 1 HTTP attempt
- 2 clusters required 2 HTTP attempts
- 2 clusters required 3 HTTP attempts
- 75 OpenRouter HTTP attempts total
- 67 clusters ended with valid classifier results
- 2 clusters ultimately failed

Use wording like:

“Classification was attempted for 69 semantic clusters. Because bounded retries were used, this resulted in 75 OpenRouter HTTP attempts in total. Sixty-seven clusters produced valid classifier results and two ultimately failed.”

Do not say:
- “69 calls”
- “sent once each”
- anything implying one API request per cluster

Verify these counts against service_mappings.json before editing.

4. SHORTEN ROOT DECISION_LOG.md

The challenge asks for a one-page decision log.

The current root DECISION_LOG.md is about 1,019 words.

Reduce ONLY the root:

DECISION_LOG.md

to roughly 500–700 words.

Keep the detailed hospital-specific logs unchanged:
- outputs/hospital_1/decision_log.md
- outputs/hospital_2/decision_log.md

The root decision log should remain a concise challenge-facing summary.

Preserve the important decisions, especially:

H1:
- H1 is development/evaluation only
- labels were visible during development
- holdout is post-hoc, not untouched
- prediction path is label-independent
- daily-cap quantity cases can be detectable without reconstructable corrected totals

H2:
- H2 was selected as a scored hospital
- JSONL/occurrence-safe handling for duplicate invoice IDs
- /SA-#### stripped as nonsemantic noise
- billed price is not used for service identity
- unit basis may break a genuine textual tie, with anti-circularity safeguards
- unresolved descriptions remain unresolved
- GLM is used only for semantic identity, not financial decisions
- Jev verifies semantic decisions with the 0.90 gate
- accepted AMBIGUOUS stays AMBIGUOUS
- 69 clusters required semantic classification
- 67 valid classifier results, 2 provider/credit failures
- 75 OpenRouter HTTP attempts due to retries
- Jev accepted 64 AMBIGUOUS decisions and no MATCHED decisions
- semantic stage added 0 verified service mappings
- expected totals are blank where correction is not reconstructable
- H2 work was intentionally stopped rather than adding credits or introducing an unvalidated replacement model
- H3–H5 were not implemented and were not used to produce submitted predictions

Do not turn the root decision log into a technical report.
Keep it genuinely one-page in spirit.

5. VERIFY CONSISTENCY

After edits, search the repo for contradictory wording related to:

- H3–H5 never being opened/inspected
- H1 holdout being untouched/unseen
- 69 GLM calls / once-each wording
- H2 provider failures
- semantic stage adding verified matches

Fix only genuine inconsistencies.

6. TEST

Run:

python -m pytest -q

Do not run:
- semantic h2 classify
- semantic h2 jev

Do not make external API calls.

7. FINAL REPORT

After completing the edits, report:

- files modified
- exact final word count of root DECISION_LOG.md
- whether the H3–H5 wording is now consistent
- whether H1 holdout wording is now consistently post-hoc
- whether the H2 classifier wording now correctly states 69 clusters / 75 HTTP attempts / 67 success / 2 failure
- test result
- whether any prediction file changed
- whether any metric changed
- git diff --stat
- git status

If any prediction, metric, or semantic artifact changes unexpectedly, stop and explain why instead of treating it as documentation cleanup.
