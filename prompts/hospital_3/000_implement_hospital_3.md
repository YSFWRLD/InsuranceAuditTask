# Prompt 000 — implement Hospital 3

- **Used with:** Claude Code (Claude Opus 5.5)
- **Kind:** development prompt to the coding assistant. Hospital 3's runtime prompt is the Jev question template [`001_jev_missing_word_resolution.md`](001_jev_missing_word_resolution.md)
- **Produced:** `src/hospital_3/`, `tests/hospital_3/`, `artifacts/hospital_3/`, `outputs/hospital_3/`, and the addition of Hospital 3 to the combined submission
- **Preceded by:** [`prompts/hospital_5/004_ent_contextual_normalization.md`](../hospital_5/004_ent_contextual_normalization.md) and the submission-packaging requests after it (see the prompt index)
- **Provenance:** saved when issued (2026-09-25), copied verbatim from the session.

Transcribed verbatim below.

---

Implement Hospital 3 end-to-end in the existing InsuranceAuditTask repository.

Repository:
https://github.com/YSFWRLD/InsuranceAuditTask

Original task repository:
https://github.com/majedzahrani3/insurance_auditing

IMPORTANT SAFETY / INTEGRITY RULES

- Work ONLY from:
  - this repository
  - the original task repository/data
  - the Hospital 3 contract documents
- DO NOT inspect GitHub forks.
- DO NOT inspect other candidates' repositories.
- DO NOT search for other people's solutions.
- DO NOT use another submission as evidence.
- DO NOT use billed price or billed totals to infer service identity.
- DO NOT optimize against hidden labels because H3 has none.
- DO NOT modify H1/H2/H4/H5 behavior.
- DO NOT commit or push at the end.
- Preserve the existing conservative principle:
  a confidently wrong answer is worse than an explicit uncertainty.

The implementation must be auditable, deterministic where possible, and consistent with the architecture already used by this repository.

==================================================
0. FIRST: UNDERSTAND THE EXISTING REPO
==================================================

Before writing code, inspect:

- README.md
- DECISION_LOG.md
- src/shared/
- src/hospital_1/
- src/hospital_2/
- src/hospital_4/
- src/hospital_5/
- src/main.py
- src/submission.py
- tests/
- prompts/
- outputs/
- artifacts/

Understand the existing conventions for:

- contract parsing
- service matching
- integer-cent money
- ROUND_HALF_UP
- uncertainty
- findings
- expected_total_cents
- confidence
- duplicate handling
- output artifacts
- submission integration
- tests
- decision logs

Reuse good existing components where appropriate.

Do NOT copy another hospital's contract interpretation blindly.

Hospital 3 must be implemented from the H3 wording itself.

==================================================
1. READ ALL THREE H3 CONTRACT DOCUMENTS
==================================================

Hospital 3 is split across three documents:

contracts/hospital_3/base_agreement.md

contracts/hospital_3/appendix_b_rate_schedule.md

contracts/hospital_3/amendment_no_1.md

Treat them as one contract package.

Contract:

INS-H3-2024-0562

Provider:

Rivermead General Hospital

Term:

2024-01-01 through 2025-12-31

Currency:

GBP

Rounding:

ROUND_HALF_UP after each monetary adjustment step.

Precedence is explicitly:

Amendment
>
Appendix B
>
Base Agreement

Build this precedence into the implementation explicitly.

==================================================
2. THE MOST IMPORTANT H3 FEATURE: AMENDMENT DATE
==================================================

Amendment No. 1 takes effect:

2025-01-01

CRITICAL:

The amendment applies BY SERVICE DATE.

NOT invoice date.

For each line:

service_date < 2025-01-01
    -> original Appendix B rate/rules

service_date >= 2025-01-01
    -> Amendment No. 1 applies

Do not infer the rate period from invoice_date.

Add strong boundary tests for:

2024-12-31
2025-01-01
2025-01-02

including invoices issued later than the service date.

==================================================
3. AMENDED SERVICES
==================================================

The amendment changes the rates of these existing services from 2025-01-01:

- Ambulatory Otolaryngologic Imaging Interpretation
- Assisted Urologic Endoscopic Procedure
- Bedside Neurological Radiotherapy Fraction
- Intensive Infectious Anaesthesia Administration
- Intensive Ophthalmic Laboratory Panel
- Intensive Otolaryngologic Anaesthesia Administration
- Specialist Psychiatric Discharge Planning

Implement the original and amended rates explicitly.

For each service test:

- 2024 service date -> Appendix B rate
- 2025 service date -> amended rate

Do not rely on invoice date.

==================================================
4. SERVICES ADDED BY THE AMENDMENT
==================================================

Two services become contracted only from 2025-01-01:

- Advanced Dermatologic Nutritional Support
- Elective Pulmonary Imaging Interpretation

They are:

NOT CONTRACTED before 2025-01-01.

Therefore a line that genuinely maps to one of these services before that date
must not simply receive the 2025 rate.

Model the service's effective period explicitly.

Test:

2024-12-31 -> not contracted
2025-01-01 -> contracted

Be careful to distinguish:

UNKNOWN SERVICE IDENTITY

from:

IDENTIFIED SERVICE BUT NOT CONTRACTED ON THAT DATE

Those are different audit findings.

==================================================
5. PARSE THE FULL H3 RULE SET
==================================================

Extract the H3 contract into structured rules.

At minimum include:

- base services/rates/unit basis
- amendment effective periods
- amended rates
- newly added services
- threshold premiums
- non-business-day uplifts
- cumulative volume discounts
- daily quantity caps
- bundled-service pairs
- exclusion windows
- contract number
- contract term
- service-date validation
- invoice-date validation
- duplicate/repeat rules

Keep provenance to the contract section/clause where practical.

Do not hardcode financial answers directly into invoice logic.

==================================================
6. PRICING ORDER
==================================================

H3 explicitly states this pricing order:

1. bundled-rate substitution
2. facility multiplier
3. plan-tier multiplier
4. premium / uplift
5. cumulative volume discount
6. multiply effective unit rate by quantity

ROUND_HALF_UP after EACH individual monetary adjustment.

Use Decimal / existing shared money utilities.

Never use binary float for money.

Although H3 says there is no facility differential and all plan tiers reimburse identically, preserve the pricing architecture rather than deleting those conceptual stages.

==================================================
7. FACILITY AND PLAN TIER
==================================================

H3 says services are delivered from:

F-MAIN

No facility differential applies.

All plan tiers are reimbursed identically.

Validate the invoice data against this contract wording.

Do not invent multipliers where none exist.

If unexpected facility values exist in the data, inspect them and document the chosen handling rather than silently ignoring them.

==================================================
8. SERVICE IDENTITY / DESCRIPTION MATCHING
==================================================

Profile every H3 description before deciding how aggressive matching should be.

Produce statistics such as:

- invoice count
- line count
- unique raw descriptions
- normalized descriptions
- description clusters
- exact matches
- structurally resolved matches
- ambiguous matches
- unknown matches

Start with deterministic text normalization and structural matching.

Reuse the strongest ideas from H4/H5 where appropriate:

qualifier
+
specialty
+
concept

But derive the H3 vocabulary from H3's own contracted service list.

IMPORTANT:

Do NOT use:

- billed unit price
- line total
- invoice total

to choose a contracted service.

Financial fields may be audited AFTER identity is established.

They must never establish identity.

==================================================
9. NORMALIZATION
==================================================

Build normalization conservatively.

Separate:

GLOBAL SAFE NORMALIZATION

from:

CONTEXT-DEPENDENT NORMALIZATION.

Examples of safe mechanics may include:

- case
- punctuation
- spacing
- obvious morphological forms
- unambiguous abbreviations

But do not assume an abbreviation's meaning just because another hospital used it.

Hospital-specific vocabulary needs hospital-specific evidence.

Keep raw description alongside normalized representation.

==================================================
10. DECIDE WHETHER JEV IS NECESSARY
==================================================

Do NOT automatically add Jev just because H5 used it.

First measure deterministic H3 coverage.

If deterministic matching leaves substantial high-impact ambiguity that can be framed as bounded semantic choices, then the existing Jev approach may be reused.

If Jev is used:

- use `jev-1.13.0`
- no general LLM
- one narrow question per review
- provide only semantic text context
- NEVER provide prices/totals/patient financial information
- save requests/responses as versioned artifacts
- make the audit reproducible offline from committed artifacts
- use a predeclared acceptance threshold
- do not invent a post-hoc closure rule after seeing results

If deterministic matching is sufficient, do not add model complexity unnecessarily.

Explain the decision either way.

==================================================
11. UNIT BASIS
==================================================

Validate each matched service's contractual unit basis against:

unit_basis_as_billed

Use the same conservative principle used elsewhere:

unit basis may help resolve ambiguity ONLY where the description has already constrained identity to a genuine finite candidate set and the contract basis distinguishes those candidates.

Do not use unit basis to turn an UNKNOWN description into a confident identity.

Wrong unit basis should be independently flaggable.

==================================================
12. THRESHOLD PREMIUMS
==================================================

H3 threshold premiums are based on:

aggregate quantity
of the Service
for the Patient
on the Service Day.

Implement this literally.

If total daily quantity exceeds the stated threshold, apply the relevant uplift according to the contract wording.

Test:

threshold - 1
threshold
threshold + 1

Also test aggregation across multiple lines and multiple invoices for the same patient/service/day.

Do not calculate threshold independently per line if the contract says aggregate daily quantity.

==================================================
13. NON-BUSINESS-DAY UPLIFTS
==================================================

Business Day means:

any Service Day other than Saturday or Sunday.

Therefore non-business-day uplift applies to the listed services on:

Saturday
Sunday

Do not invent public-holiday logic.

Test weekday / Saturday / Sunday.

==================================================
14. CUMULATIVE VOLUME DISCOUNTS
==================================================

This clause is unusually explicit.

Cumulative utilisation is:

- across the whole contract term
- across all patients
- ordered by Service Date
- up to but EXCLUDING the line currently being priced
- where service dates tie, order by ascending line identifier
- if multiple thresholds have been crossed, use the deeper discount

Implement exactly that.

This is important.

Create tests for:

- immediately before threshold
- exactly reaching threshold
- first line after threshold
- two thresholds
- same service date with line-id ordering
- quantities that cross a threshold on one line

Do not select the interpretation by comparing to billed prices.

Document precisely how a threshold-crossing quantity is handled.

If the wording creates any residual ambiguity, implement a switch/test for the alternative and document it.

==================================================
15. BUNDLES
==================================================

H3 contains bundled service pairs.

A bundle applies when both services are delivered:

- to the same patient
- on the same service day

The specified bundled rates replace Appendix B rates for BOTH services.

Bundle substitution happens before premium/discount.

Apply the amendment rate logic correctly around bundles:

A bundle replaces the underlying normal rate when the pair qualifies.

Do not accidentally apply amended base rate first and then bundle incorrectly.

Test:

- both services same patient/day
- different patient
- different day
- repeated lines
- 2024
- 2025

==================================================
16. DAILY CAPS
==================================================

H3 lists maximum units per Patient per Service Day.

Do not automatically copy H4's "excess not payable" behavior.

Read H3's wording.

If the contract proves that the quantity breaches a maximum but does NOT clearly establish how to reconstruct the corrected financial quantity, follow the conservative policy:

- flag the cap breach
- do not invent delivered quantity
- leave corrected total blank where necessary

Document the interpretation.

If H3 wording actually establishes a payable cap clearly, demonstrate that from H3 itself before pricing it.

Test aggregate same-patient/service/day cap behavior.

==================================================
17. EXCLUSION WINDOWS
==================================================

Parse all H3 exclusion rules.

Do not silently inherit H5's interpretation.

Read the exact H3 phrase:

"Service A not billable within N days of Service B"

Determine and document:

- whether "within" is two-sided or directional
- whether same day counts
- whether day N is inclusive

If ambiguous, choose a defensible reading and preserve an alternative switch/test.

Do not choose based on which interpretation produces more findings.

Test:

0
N
N+1
-N
-(N+1)

where relevant.

==================================================
18. DUPLICATES / REPEATS
==================================================

H3 says:

the same Service may not be billed twice
for the same Patient
and Service Date
whether on one invoice or across several.

Implement:

- within-invoice repeats
- cross-invoice repeats

Distinguish:

duplicate invoice number

from:

duplicate service billing.

Use identity only when service identity is sufficiently established.

==================================================
19. CONTRACT / DATE VALIDATION
==================================================

Check:

- contract_number
- service date within 2024-01-01 to 2025-12-31
- service date not after invoice date
- malformed dates
- duplicate invoice numbers

Amendment eligibility must still use SERVICE DATE.

==================================================
20. LINE ARITHMETIC
==================================================

Independently validate:

line_total_cents
against
unit_price_cents × quantity

where quantity representation permits exact evaluation.

Keep this separate from contractual pricing.

There are two different questions:

1. Did the hospital calculate its billed line correctly?
2. Was the billed rate contractually correct?

Do not collapse them.

==================================================
21. INVOICE TOTAL
==================================================

Check billed invoice total against the sum of billed line totals.

This is separate from the contractually expected total.

Support finding categories consistently with the rest of the repository.

==================================================
22. EXPECTED TOTAL POLICY
==================================================

Only emit expected_total_cents when the corrected total is genuinely reconstructable.

If any unresolved component could materially change the corrected total:

leave expected_total_cents blank.

Never fill it using:

- billed invoice total
- zero
- guessed service
- guessed cap quantity
- most likely interpretation

However, preserve the existing financial-equivalence principle:

if every viable interpretation produces exactly the same corrected invoice total, the total may be considered reconstructable.

If you implement this for H3, test it carefully.

==================================================
23. UNCERTAINTY
==================================================

Use the repository's existing confidence style.

Do not claim calibrated probabilities because H3 has no labels.

Confidence should reflect evidence strength.

Maintain machine-readable uncertainty diagnostics similar to H4/H5:

- pricing_complete
- correction_reconstructable
- ambiguity reasons
- unresolved line counts
- provisional total if existing conventions require it

But the final submission must stay in the official six-column schema.

==================================================
24. FINDING CATEGORIES
==================================================

Prefer consistent existing names where applicable, such as:

unit_price_mismatch
wrong_unit_basis
premium_omitted
premium_incorrectly_applied
volume_discount_omitted
volume_discount_incorrectly_applied
daily_cap_exceeded
bundle_not_applied
exclusion_window_violation
duplicate_invoice_id
cross_invoice_duplicate
line_total_arithmetic
invoice_total_mismatch
contract_number_mismatch
service_date_out_of_window
service_date_after_invoice_date
malformed_service_date
unknown_service

Add an H3-specific category if needed for:

service identified but not yet contracted under the amendment.

Choose a clean name and document it.

Do not label an uncontracted-on-date service as simply UNKNOWN if its identity is actually known.

==================================================
25. OUTPUTS
==================================================

Create the same style of H3 deliverables as the other hospitals.

At minimum:

outputs/hospital_3/submission.csv
outputs/hospital_3/predictions.csv
outputs/hospital_3/audit_report.md
outputs/hospital_3/decision_log.md

and useful artifacts such as:

artifacts/hospital_3/unresolved.json

plus semantic artifacts only if a semantic stage is actually used.

Integrate H3 into:

outputs/submission.csv

After implementation the combined submission should contain:

H2
H3
H4
H5

Hospital 1 remains excluded because it is development data.

==================================================
26. CLI INTEGRATION
==================================================

Extend the current CLI consistently.

Expected behavior should include something like:

python -m src.main audit h3

and:

python -m src.main submission

Do not break existing commands.

==================================================
27. README / DOCUMENTATION
==================================================

Update README.md so it no longer says:

Hospital 3 not implemented.

Add a concise H3 section reporting:

- invoice count
- flagged count
- pricing_complete
- correction_reconstructable
- blank totals
- description/identity coverage
- major H3-specific logic
- amendment handling
- uncertainty
- model use or no-model decision

Update root DECISION_LOG.md only as needed.

Update prompts/README.md.

Save THIS implementation prompt as a versioned H3 prompt, following the existing naming convention.

Do not rewrite unrelated documentation.

==================================================
28. SUBMISSION WRITE-UP
==================================================

Inspect:

SUBMISSION_WRITEUP.md

and the PDF workflow if present.

Do NOT automatically regenerate the final write-up yet unless the existing repo workflow explicitly expects it during implementation.

Instead report exactly what needs updating because H3 is now included.

The final write-up must remain within the original two-page limit.

==================================================
29. TESTS
==================================================

Add comprehensive H3 tests.

Test at minimum:

CONTRACT
- precedence
- all original services
- amendment rates
- amendment effective boundary
- newly added services

MATCHER
- exact
- normalized
- ambiguous
- unknown
- no price leakage

PRICING
- HALF_UP
- pricing order
- premiums
- weekends
- discounts
- bundles
- caps
- amendment date

GLOBAL
- cumulative discounts across invoices
- duplicates across invoices
- daily patient aggregation
- exclusions

OUTPUT
- required columns
- confidence range
- blank total handling
- deterministic ordering
- combined submission includes H3 exactly once

REGRESSION
- all existing H1 tests
- all existing H2 tests
- all existing H4 tests
- all existing H5 tests
- shared tests

Existing hospital outputs must not change unexpectedly.

==================================================
30. REGRESSION PROTECTION
==================================================

Before implementation, hash or otherwise record all current H1/H2/H4/H5 outputs and artifacts.

After H3 implementation, confirm they remain byte-identical except for files that MUST change globally, such as:

outputs/submission.csv
README.md
DECISION_LOG.md
prompts/README.md
src/main.py
src/submission.py
shared integration tests

Do not alter per-hospital H1/H2/H4/H5 predictions just to make H3 fit.

==================================================
31. DO NOT CHASE COVERAGE
==================================================

This is important.

Do not optimize for:

"How many H3 totals can I fill?"

Optimize for:

"How many H3 totals can I defend?"

If coverage is low because service descriptions genuinely do not settle identity, preserve that uncertainty.

Do not create a post-hoc closure trick.

==================================================
32. IMPLEMENTATION PROCESS
==================================================

Work in this order:

PHASE A
Inspect and profile H3 data.

PHASE B
Parse all three contract documents into structured rules.

PHASE C
Build deterministic matcher and report coverage BEFORE semantic assistance.

PHASE D
Implement pricing and audit engine.

PHASE E
Evaluate unresolved clusters by impact.

PHASE F
Only if justified, decide whether bounded Jev review would materially improve defensible coverage.

PHASE G
Generate H3 outputs.

PHASE H
Integrate into combined submission.

PHASE I
Run adversarial review.

PHASE J
Run complete regression suite.

Do not jump directly to a semantic model.

==================================================
33. ADVERSARIAL REVIEW
==================================================

Before declaring H3 complete, actively try to break it.

Review:

- every amendment-boundary line
- every new-service pre-2025 occurrence
- all rate changes
- threshold boundaries
- discount boundaries
- bundle interactions
- exclusion boundaries
- cap breaches
- ambiguous descriptions
- unknown descriptions
- duplicate invoice IDs
- cross-invoice duplicate services
- malformed dates

Ask for each proven total:

"Could a different viable service reading change this amount?"

If yes:

blank it.

==================================================
34. FINAL REPORT
==================================================

At the end, DO NOT COMMIT.

Report:

DATA
- physical invoice records
- unique invoice IDs
- reused invoice IDs
- line count
- patient count
- raw descriptions
- normalized clusters

IDENTITY
- identified lines
- ambiguous lines
- unknown lines
- identity route breakdown

AUDIT
- flagged invoices
- pricing_complete
- correction_reconstructable
- blank expected totals

BLANK BREAKDOWN
- count by primary reason

FINDINGS
- count per error category

AMENDMENT
- number of lines using 2024 rates
- number using 2025 amended rates
- occurrences of the two newly added services
- any pre-effective-date occurrences of those services

SEMANTIC
- whether Jev was used
- if yes, exact incremental contribution
- if no, why not

TESTS
- H3 tests
- total test suite count
- all failures if any

REGRESSION
- H1 unchanged?
- H2 unchanged?
- H4 unchanged?
- H5 unchanged?
- exact global files that changed

SUBMISSION
- H3 row count
- new combined submission row count

CAVEATS
- unresolved contract ambiguities
- unresolved descriptions
- any interpretation that materially affects findings

GIT
- git status
- confirmation nothing was committed
- confirmation nothing was pushed

==================================================
35. STOP CONDITION
==================================================

Do not commit.

Do not push.

Do not open a PR.

Stop after implementation, tests, regenerated outputs, documentation updates and the final report.

I want to review the result before anything is committed.
