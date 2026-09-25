# Prompt 002 — Hospital 3: finalization review

- **Used with:** Claude Code (Claude Opus 5.5)
- **Kind:** development prompt to the coding assistant (not a runtime prompt; the code does not load this file)
- **Produced:** the ENT review (accepted; recorded as a human-reviewed, context-only reading in `src/hospital_3/normalization.py::ACRONYM_REVIEWS`), independent verification of the Jev requests, the amendment, every proven total and every blank, a fix to the per-unit discount *alternative* (an arithmetic error no longer reads as a discount finding), the updated submission write-up and PDF, and documentation updates
- **Preceded by:** [`000_implement_hospital_3.md`](000_implement_hospital_3.md)
- **Provenance:** saved when issued (2026-09-25), copied verbatim from the session.

Transcribed verbatim below.

---

Do a focused finalization pass for Hospital 3 only.

Do NOT redesign the whole solution.
Do NOT change H1/H2/H4/H5 behavior.
Do NOT commit or push yet.

Current H3 state:

- 939 physical records
- 932 unique invoice IDs
- 11,655 lines
- 234 patients
- 544 raw descriptions
- 507 normalized descriptions
- 219 identity clusters

Final identity:
- 11,569 identified lines
- 72 ambiguous
- 14 unknown

Routes:
- text: 6,143
- text with context-resolved tokens: 4,523
- unit-basis tie-break: 85
- Jev: 818

Audit:
- 932 rows
- 70 flagged
- 852 pricing-complete
- 848 proven expected totals
- 84 blank

Blank reasons:
- 47 closed ties with different prices
- 20 unresolved because Jev P=0.89 < 0.90 gate
- 13 unknown services
- 4 daily-cap breaches

Tests:
- 159 H3 tests
- 871 total tests
- 0 failures

Submission:
- 932 H3 rows
- combined submission now 3,942 rows

Current git state:
- HEAD: aee672f
- nothing committed
- nothing pushed
- no PR

Modified existing files:
- outputs/submission.csv
- README.md
- DECISION_LOG.md
- prompts/README.md
- src/main.py
- src/submission.py
- tests/test_submission.py
- .env.example

New H3 directories:
- src/hospital_3/
- tests/hospital_3/
- prompts/hospital_3/
- outputs/hospital_3/
- artifacts/hospital_3/

==================================================
1. FIRST REVIEW THE H3 ENT DECISION
==================================================

The main decision I need checked before finalizing is:

ENT -> otolaryngologic

Current implementation:
- context-only, not global
- no price or total used
- only applies when a contracted Otolaryngologic service fits the rest of the words
- disabling it via build_lexicon(include_acronyms=False) drops proven totals from 848 to 529

Evidence from H3 itself:
- ENT appears in 15 descriptions
- none contain another specialty word
- all 15 structurally fit an Otolaryngologic contracted service
- 7 corresponding description patterns also appear with "otolaryngologic" spelled out

Your task:

A. Inspect all H3 occurrences of standalone ENT.
B. Inspect the related H3 contract wording.
C. Verify the 7 spelled-out counterpart patterns.
D. Verify there is no H3 evidence that ENT means something else in these contexts.
E. Verify the mapping is contextual rather than global.
F. Verify no billed price, totals, invoice IDs, or downstream financial result influenced the identity decision.

Then give one of:

ENT REVIEW ACCEPTED
or
ENT REVIEW NOT ACCEPTED

If accepted:
- keep the current contextual implementation
- document explicitly that this is a human-reviewed contextual interpretation
- do NOT make ENT a global alias

If not accepted:
- disable it cleanly
- regenerate outputs
- report the new metrics

Do not lower any Jev threshold to recover the lost coverage.

==================================================
2. VERIFY LIVE JEV USAGE
==================================================

38 live Jev requests were made using jev-1.13.0.

Verify:

- requests contained only semantic description evidence
- no prices
- no totals
- no dates
- no invoice IDs
- no patient IDs
- no contract rates
- only allowed metadata such as line count if documented
- stored responses are sufficient for offline reproduction
- stale review protection works
- production audit does not require live calls once artifacts exist

Check whether the statement:
"no digits apart from each cluster's line count"
is actually true from stored request bodies.

Report any violation.

Do not make additional live Jev calls unless a stored artifact is genuinely invalid.

==================================================
3. VERIFY H3 AMENDMENT LOGIC
==================================================

H3 uses three-document precedence:

Amendment
> Appendix B
> Base contract

Pricing must be selected by SERVICE DATE, never invoice date.

Verify:

- 2024 service dates use old rates
- 2025 service dates use amended rates where applicable
- added services are only valid from their effective date
- service_not_contracted_on_date logic is correct
- amended_rate_applied_before_effective_date logic is correct
- amended_rate_not_applied logic is correct
- invoice date never selects the rate version

Use direct examples from the H3 data.

Expected observations:
- 282 amended-service lines priced under 2024 rules
- 280 under 2025 rules
- 28 old-rate service lines appear on invoices issued in 2025
- 81 lines use services added by amendment
- none of those added-service lines occur before 2025-01-01

Verify these numbers independently.

==================================================
4. VERIFY ALL 848 PROVEN TOTALS
==================================================

For every nonblank expected_total_cents:

confirm it is reconstructable from accepted service identity and contract logic only.

No proven total may depend on:

- unresolved service identity
- unknown service
- price-based identity
- invoice-total compatibility
- stale Jev review
- rejected ENT behavior
- a closed tie with different prices
- undocumented assumption

Spot-check each route:

- exact/normalized text
- contextual token resolution
- ENT contextual resolution
- unit-basis tie-break
- Jev missing-word resolution
- amended-rate pricing
- bundle
- premium
- discount
- exclusion
- repeat billing
- financial equivalence if used

==================================================
5. VERIFY THE 84 BLANKS
==================================================

Confirm final blank breakdown exactly:

- 47 closed ties where candidate services price differently
- 20 unresolved because review remains below threshold
- 13 unknown services
- 4 daily-cap breaches

For every blank:
- ensure the reason is documented
- ensure no downstream stage accidentally prices it
- ensure no avoidable blank remains where all viable interpretations give the same total

==================================================
6. VERIFY JEV THRESHOLD DISCIPLINE
==================================================

Current gate:
0.90

One unresolved cluster is P=0.89.

Confirm it stays unresolved.

Do NOT:
- rerun it hoping for a different answer
- lower the gate
- add a one-off exception
- use unit price to resolve it

This is intentionally uncertain.

==================================================
7. VERIFY H3 FINDING COUNTS
==================================================

Recompute and verify these counts:

- wrong_unit_basis 14
- unknown_service 13
- unit_price_mismatch 13
- bundle_not_applied 7
- daily_cap_exceeded 7
- premium_omitted 7
- invoice_total_mismatch 7
- line_total_arithmetic 7
- malformed_service_date 7
- duplicate_invoice_id 7
- service_date_out_of_window 7
- service_date_after_invoice_date 7
- contract_number_mismatch 6
- premium_incorrectly_applied 5
- volume_discount_incorrectly_applied 5
- volume_discount_omitted 4
- cross_invoice_duplicate 4
- exclusion_window_violation 3
- amended_rate_applied_before_effective_date 3
- amended_rate_not_applied 1

Also verify:

service_not_contracted_on_date

is implemented and tested but has 0 occurrences in the dataset.

==================================================
8. REVIEW THE FOUR IMPORTANT INTERPRETATIONS
==================================================

Keep each behind its current switch.

A. Exclusions
Current reading:
"within N days of" means both directions, inclusive of day N.

All 3 findings depend on the excluded service appearing before the trigger.

Verify:
- all 3
- INV-H3-000052 is flagged only for this reading
- confidence is appropriately lower there

Do NOT hide that this is ambiguous.

B. Volume discount threshold crossing
Current reading:
a line takes the tier already exceeded before that line.

Verify the 15 rows that would differ under a per-unit interpretation.

Billed prices may be mentioned only as consistency evidence, never as the reason for choosing the interpretation.

C. Daily cap
Current reading:
breach is detectable, but exact corrected total is blank because the contract does not prove that excess quantity should simply be removed.

Verify the 4 affected blank totals.

D. Repeat billings
Current reading:
repeat billings contribute zero to the day's aggregate.

Verify that alternate treatment changes 0 rows.

==================================================
9. SETTLED-INVOICE CLAUSE
==================================================

The contract has a settled-invoice clause, but source data contains no settlement field.

Verify the implementation does NOT pretend settlement can be evaluated.

Document clearly:

- clause exists
- data lacks settlement information
- therefore it is not operationalized
- no line is affected

==================================================
10. CHECK REGRESSION
==================================================

Regenerate H3.

Then verify all existing H1/H2/H4/H5 outputs and artifacts remain byte-identical.

Only allowed changes outside H3 are the already expected integration/documentation files.

Run full test suite.

Expected:
871 tests pass

The Windows access-violation notices are acceptable only if:
- tests still pass
- exit code is success
- behavior matches the pre-existing H5 condition
- no output corruption occurs

==================================================
11. UPDATE THE SUBMISSION WRITE-UP
==================================================

Now update:

SUBMISSION_WRITEUP.md

and regenerate its PDF.

This is required.

The write-up must remain NO MORE THAN TWO PAGES.

Update it to reflect H3.

Required changes:

SECTION 1
- remove any statement that H3 was deliberately not implemented
- say scored coverage now includes H2, H3, H4 and H5
- combined submission rows: 3,942
- H3: 848 of 932 proven totals
- tests: 871

SECTION 2
Add concise H3 uncertainty/methodology notes:

- ENT contextual reading materially affects H3:
  319 of 848 proven totals depend on it
- explain why it was accepted or rejected after this review
- Jev adds 818 identified lines and 495 proven totals
- amendment pricing is based on service date
- exclusion ambiguity applies to H3 too

Do not make the write-up overly technical.

SECTION 3
Remove:
"Implement Hospital 3"

Replace it with better next-week work, such as:
- obtain clarification on ambiguous exclusion direction
- calibrate confidence on held-out or newly labeled data
- review remaining ambiguous identity clusters
- improve amendment/temporal-contract tests
- reduce dependence on manual contextual readings

AI DISCLOSURE
Update to say:
- H3 and H5 use Jev for bounded semantic review
- AI-assisted coding/review tools were used
- prompts and review artifacts are versioned in repo

Keep it understandable to an interviewer.

Do not exceed two pages after PDF regeneration.

==================================================
12. UPDATE DOCUMENTATION
==================================================

Update only where necessary:

- README.md
- DECISION_LOG.md
- prompts/README.md
- H3 decision log
- H3 semantic contribution report
- submission write-up

Ensure there are no stale statements like:

- H3 not implemented
- three scored hospitals
- 3,010 combined rows
- 711 tests
- H5-only Jev
- old H3 metrics

The root decision log is currently around 904 words.
Do not bloat it unnecessarily.

==================================================
13. CHECK SUBMISSION
==================================================

Verify outputs/submission.csv:

- 3,942 rows
- H2: 1,125
- H3: 932
- H4: 835
- H5: 1,050
- no H1 rows
- exact template columns/order
- unique invoice IDs across scored hospitals
- deterministic ordering
- valid confidence range
- correct blank serialization

Regenerate twice and verify byte-identical output.

==================================================
14. FINAL READINESS REPORT
==================================================

At the end report:

### H3
- ENT review accepted/rejected
- proven totals
- blank totals
- flagged
- identified / ambiguous / unknown lines
- amendment findings
- Jev contribution
- ENT dependency
- final finding counts

### Submission
- total rows
- H2/H3/H4/H5 row counts
- tests passed
- regression status
- write-up page count
- PDF regenerated yes/no

### Git
- modified files
- untracked/new files
- current HEAD
- confirmation nothing committed
- confirmation nothing pushed

### Verdict
Use exactly one:

READY TO COMMIT

READY AFTER SMALL FIXES

NOT READY

IMPORTANT:

Do not optimize for more coverage.

Do not lower confidence gates.

Do not add another inference rule just to raise totals.

Correctness, uncertainty discipline, amendment handling, and submit-readiness matter more than coverage.

Do not commit or push.
