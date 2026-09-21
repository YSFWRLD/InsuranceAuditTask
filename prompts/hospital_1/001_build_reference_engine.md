# Prompt 001 — build the Hospital 1 reference engine

- **Used with:** Claude Code (Claude Opus 5)
- **Produced:** the original Hospital 1 implementation, frozen at
  `ab7bc8040ab8fe43` (see `artifacts/hospital_1/freeze.json`)
- **Followed by:** [002](002_refactor_by_hospital.md), a structural refactor

Transcribed verbatim below.

---

You are working inside the `insurance_auditing` repository.
Your task is to build a new Hospital 1 auditing solution from scratch.
This is not a competition to obtain 100% on Hospital 1 by any means necessary.
The goal is to build a clean, deterministic, explainable reference auditing engine that should later transfer to Hospitals 2–5.
Only work on Hospital 1.
Do NOT implement Hospital 2, 3, 4, or 5.
Core philosophy
Follow these principles throughout the implementation:
Contract text determines rules.
Invoice text determines service identity.
Python determines financial calculations.
Labels only measure performance.
Uncertainty must remain uncertainty.
The system must not learn or memorize the Hospital 1 answer key.
A 97–99% clean unseen result is preferable to a suspicious 100% result achieved through label-specific tuning.
Files in scope
Use:

* `contracts/hospital_1/provider_services_agreement.md`
* `contracts/hospital_1/provider_services_agreement.txt`
* `invoices/hospital_1_invoices.csv`
* `invoices/hospital_1_line_items.csv`
* `invoices/hospital_1_invoices.jsonl`
* `labels/hospital_1_labels.csv`
* `submission_template.csv`

Do not inspect or implement other hospitals.
Phase 1 — Protect against overfitting BEFORE development
Hospital 1 labels must be separated into:

* development labels
* locked holdout labels

Use a deterministic split such as:

* 70% development
* 30% holdout
* fixed seed

IMPORTANT:
Create the split programmatically.
Do NOT print or manually inspect the holdout label contents.
After the split is created:

* development code may use only the development labels
* holdout labels must remain untouched
* do not debug against holdout failures
* do not repeatedly evaluate on holdout

The holdout should be evaluated only after the implementation is frozen.
Also create a temporal/prospective evaluation later because some rules depend on historical utilization and cross-invoice context.
Create something like:

```text
outputs/
    hospital_1_split_manifest.json

development/
    hospital_1_dev_labels.csv

holdout/
    hospital_1_holdout_labels.csv

```

The exact structure may differ, but preserve the isolation.
Phase 2 — Data model
Use the JSONL file as the canonical invoice-occurrence source where possible.
Reason:
Invoice IDs may be duplicated, and a duplicate invoice ID does not mean the physical invoice records are identical.
Do not collapse physical invoice occurrences too early.
Represent something similar to:

```python
InvoiceOccurrence(
    occurrence_id=...,
    invoice_id=...,
    invoice_date=...,
    patient_id=...,
    facility_code=...,
    plan_tier=...,
    line_items=[...],
)

```

CSV data may be used as a validation/cross-check source.
Do NOT rely on assumptions such as numeric positions embedded in line IDs to associate lines with duplicate invoice records unless absolutely necessary.
If an assumption is unavoidable, document it.
Phase 3 — Parse the contract into structured rules
Do not scatter contract-specific constants throughout the pricing engine.
Create a structured `ContractRules` representation from the Hospital 1 contract.
For example:

```python
ServiceRule(
    name=...,
    unit_basis=...,
    base_rate_cents=...,
)

```

and explicit structures for:

```text
threshold premiums
weekend/non-business-day uplifts
daily caps
bundles
cumulative volume discounts
exclusion windows
contract metadata

```

Each parsed rule should preserve provenance where practical, such as:

```python
source_section="Section 7"

```

The parser should fail visibly.
If the contract contains a section that looks like a pricing rule but cannot be parsed safely, do NOT silently interpret that as "no rule."
Raise a validation warning/error.
Validate expected rule-family counts where possible.
Phase 4 — Conservative service matcher
The invoice `description` is free text.
Create a deterministic matcher that does NOT depend on labels.
Normalize:

* casing
* punctuation
* whitespace
* `/NG-####` suffixes
* known abbreviations

Use a controlled abbreviation dictionary for things such as:

```text
adv → advanced
rtn → routine
msk → musculoskeletal
ophth → ophthalmic
biop → biopsy
proc → procedure
etc.

```

The abbreviation dictionary must represent general textual abbreviations, NOT invoice-specific answers.
Do not create:

```python
"description X" -> "service Y"

```

just because a labeled invoice required it.
Matching states
The matcher must distinguish:

```text
MATCHED
AMBIGUOUS
UNKNOWN

```

Meaning:
MATCHED
There is enough textual evidence to identify a contracted service.
AMBIGUOUS
Multiple contracted services remain plausible.
Do not force a choice.
UNKNOWN
The description does not plausibly identify any contracted service.
This distinction must survive into the audit results.
Matching evidence
Use textual evidence first.
Recommended order:

```text
normalized description
        ↓
expanded abbreviation tokens
        ↓
candidate service ranking
        ↓
margin / ambiguity check

```

A service may only be selected when the match exceeds a conservative threshold and the best candidate has sufficient separation from alternatives.
Preserve:

```python
match_score
runner_up_score
candidate_services
match_method

```

Unit basis tie-breaking
The billed unit basis may be used ONLY when:

1. text leaves a genuine tie between plausible services
2. exactly one tied candidate supports the billed unit basis

If unit basis determines the service identity, record:

```python
used_unit_basis_for_matching = True

```

Then do NOT independently claim `wrong_unit_basis` from that same evidence.
Avoid double-counting evidence.
Do NOT use billed price to identify the service
The billed price is itself being audited.
Therefore:
DO NOT select a service because its contract rate happens to match the billed price.
Forbidden logic:

```python
if billed_price == service.rate:
    matched_service = service

```

and do not use closest-price matching.
If text and unit basis cannot resolve the service:

```text
AMBIGUOUS

```

is the correct result.
Phase 5 — Build hospital-wide context
Do not price invoices independently when the contract contains stateful rules.
Before pricing, construct global indexes for:
Daily aggregates

```text
(patient, service, service_date)
→ aggregate quantity

```

Used for:

* threshold premiums
* daily caps

Bundles

```text
(patient, service_date)
→ services present

```

Cumulative utilization
Track utilization across the whole contract term.
Respect the contract's ordering rules:

```text
service date
then line identifier where dates tie

```

Cumulative quantity must exclude the current line before determining its discount.
Patient/service/date history
Required for:

* exclusion windows
* duplicate services

Duplicate invoice occurrences
Preserve physical occurrences while recognizing duplicated invoice identifiers.
Cross-invoice duplicate services
Identify later repeat billing of:

```text
same patient
same service
same service date

```

according to the contract.
Phase 6 — Deterministic pricing engine
All financial calculations must be deterministic.
Use:

```python
Decimal
ROUND_HALF_UP

```

Money must ultimately remain integer cents.
No binary floating-point calculations for money.
Apply adjustments in the exact contractual order:

```text
1. bundled-rate substitution
2. facility multiplier
3. plan-tier multiplier
4. premium / uplift
5. cumulative volume discount
6. multiply by quantity

```

Even if Hospital 1 facility and plan-tier multipliers are effectively 1.0, keep these stages in the architecture so the engine can later transfer to other hospitals.
Round after every required step.
Pricing trace
Every priced line should produce an explainable trace.
Example:

```text
Base rate:                  10000
Weekend uplift +20%:        12000
Volume discount -10%:       10800
Quantity × 3:               32400

```

The trace should make it possible to explain where every expected total came from.
Phase 7 — Error detection
Implement all Hospital 1 contract rules, including:

* contract number mismatch
* duplicate invoice ID
* malformed service date
* service date outside contract term
* service date after invoice date
* unknown service
* ambiguous service where appropriate
* wrong unit basis
* unit-price mismatch
* line-total arithmetic error
* invoice-total mismatch
* daily cap exceeded
* premium omitted
* premium incorrectly applied
* non-business-day uplift issues
* volume discount omitted
* volume discount incorrectly applied
* bundle not applied
* exclusion-window violation
* duplicate same-service billing
* cross-invoice duplicate billing

Do not create invoice-specific conditions.
Forbidden:

```python
if invoice_id == "INV-H1-000015":

```

or:

```python
if total == SOME_KNOWN_LABEL_TOTAL:

```

when introduced only to reproduce a label.
Phase 8 — Separate detection from monetary reconstruction
This is extremely important.
These are different questions:

```text
Is this invoice wrong?

```

and:

```text
Can I reconstruct exactly what it should have been?

```

Model them separately.
Example:

```python
AuditResult(
    flagged=True,
    error_categories=["daily_cap_exceeded"],
    pricing_complete=False,
    expected_total_cents=None,
)

```

A daily-cap violation may prove that the billed quantity is impossible without revealing the true underlying delivered quantity.
Do NOT reverse-engineer the hidden synthetic corruption from the labels.
Use fields internally such as:

```text
flagged
pricing_complete
correction_reconstructable
expected_total_cents
uncertainty_reasons

```

If useful, also calculate:

```text
maximum_contractually_payable_total_cents

```

but do not pretend it is necessarily the original true invoice value.
Phase 9 — Confidence
Do not treat confidence as a magically calibrated probability.
Internally treat it as:

```text
evidence confidence / review priority

```

Base it on observable evidence quality.
Example hierarchy:

```text
high:
clear deterministic rule
strong service match
complete pricing history

medium:
abbreviated but strong service match
secondary unit-basis tie-break
stateful calculation with minor uncertainty

low:
unknown service
ambiguous service
malformed stateful input
unreconstructable correction

```

Do not assign 0.99 simply because the label matched during development.
Document clearly that confidence is not a probability unless independently calibrated.
Phase 10 — Development using ONLY dev labels
Now evaluate against:

```text
development/hospital_1_dev_labels.csv

```

Use dev-label failures to find GENERAL problems.
Allowed:
Several failures reveal cumulative utilization is calculated after the current line instead of before it.
Fix that.
Not allowed:
Invoice X failed, so add a special case for Invoice X.
For each change ask:
Does this represent a genuine contract/data interpretation rule that would also apply to another invoice?
If not, do not implement it.
Phase 11 — Tests before holdout evaluation
Build strong synthetic tests that do NOT copy Hospital 1 labeled invoices.
At minimum test:
Rounding
Cases at half-cent boundaries.
Base pricing
Correct and incorrect unit prices.
Daily caps

```text
cap - 1
cap
cap + 1

```

Premium thresholds

```text
threshold - 1
threshold
threshold + 1

```

Weekend uplifts

```text
weekday
Saturday
Sunday

```

Volume discounts

```text
before threshold
crossing threshold
after threshold
second threshold
same-date tie ordering

```

Bundles

```text
A only
B only
A+B same patient/day
A+B different patient
A+B different day

```

Exclusion windows

```text
outside
boundary
inside
both directions

```

Duplicate services

```text
same invoice
cross invoice
different patient
different service day

```

Duplicate invoice IDs
Use two physical invoice occurrences.
Unit basis
Correct, incorrect and ambiguous-service scenarios.
Unknown services
Clearly non-contract service descriptions.
Ambiguous descriptions
Descriptions that support more than one contracted service.
Tests should validate both:

```text
error detection
pricing behavior

```

Phase 12 — Ablation tests
Verify that important system components actually matter.
Run versions with these disabled one at a time:

```text
description normalization
bundles
premiums/uplifts
volume discounts
duplicate detection
date validation

```

Measure the performance change on DEV only.
If disabling an important contract rule changes nothing, investigate why.
Possible explanations:

* rule never triggers
* implementation is broken
* another heuristic masks it
* evaluation is wrong

Phase 13 — Freeze implementation
Once development performance and synthetic tests are stable:

1. run all tests
2. record code hash / commit hash
3. freeze matcher thresholds
4. freeze aliases
5. freeze contract parsing
6. freeze pricing behavior

Do not modify the implementation after seeing holdout failures.
Phase 14 — Evaluate locked holdout ONCE
Run the frozen system against the locked random holdout.
Report:

```text
accuracy
precision
recall
F1
TP
FP
TN
FN
category precision/recall/F1
expected-total exact match
expected-total MAE
pricing completeness

```

Clearly distinguish:

```text
invoice-error detection
category attribution
exact monetary reconstruction

```

Do not combine them into one misleading percentage.
Phase 15 — Temporal prospective evaluation
Also perform a chronological evaluation.
Use earlier invoices/history as available information and evaluate later invoices prospectively.
Do not let future invoice rows affect earlier stateful calculations.
This specifically tests:

* cumulative discounts
* cross-invoice duplicates
* exclusion history
* temporal state handling

Report this separately from the random holdout.
Required outputs
Create:

```text
outputs/hospital_1_predictions.csv
outputs/hospital_1_dev_evaluation.md
outputs/hospital_1_holdout_evaluation.md
outputs/hospital_1_generalization_report.md
outputs/hospital_1_service_match_audit.csv
outputs/hospital_1_split_manifest.json

```

Also create structured machine-readable audit output if useful.
Prediction output
`hospital_1_predictions.csv` should contain at least:

```text
invoice_id
flagged
error_category
expected_total_cents
billed_total_cents
confidence

```

Internally maintain richer fields even if they are not part of the CSV:

```text
pricing_complete
correction_reconstructable
uncertainty_reasons
physical_occurrence_id

```

Service-match audit
Generate a file showing each unique description and:

```text
description
normalized_description
matched_service
status
match_score
runner_up
runner_up_score
match_method
used_unit_basis_for_matching

```

This should allow manual inspection of matcher behavior.
Generalization report
The final report should answer:
Did the predictor ever read labels?
Prediction modules must have zero dependency on label files.
Is there hardcoding?
Search for:

```text
invoice IDs
patient IDs
line IDs
known erroneous totals
label-derived mappings

```

How different is dev vs holdout performance?
Report generalization gaps.
How does performance change on the temporal test?
Report separately.
Are rare categories reliable?
Mention small support explicitly.
Does service matching generalize?
Analyze:

```text
strong textual matches
abbreviation matches
unit-basis tie-breaks
ambiguous
unknown

```

Do the rule components matter?
Include ablation results.
Are synthetic contract tests passing?
Report all.
What cannot be reconstructed?
Be explicit.
Architecture
Prefer something approximately like:

```text
src/
    models.py
    data.py
    contract_parser.py
    matcher.py
    context.py
    pricing.py
    validators.py
    auditor.py
    confidence.py
    evaluate.py
    main.py

tests/
    test_data.py
    test_contract_parser.py
    test_matching.py
    test_rounding.py
    test_caps.py
    test_premiums.py
    test_discounts.py
    test_bundles.py
    test_exclusions.py
    test_duplicates.py
    test_synthetic_contract.py

```

You may improve the structure if there is a cleaner design.
Keep the code readable and well commented around non-obvious contract logic.
Do not overengineer.
Critical restrictions
Never use Hospital 2–5.
Never hardcode labeled invoice outcomes.
Never use invoice ID as prediction evidence.
Never use labels inside the prediction pipeline.
Never use billed price to determine service identity.
Never silently force an ambiguous service match.
Never pretend an unknowable corrected amount is known.
Never tune against the locked holdout.
Never modify logic after seeing holdout results.
Never optimize solely for the headline H1 percentage.
Final objective
The final result should not merely say:
"Hospital 1 = 100%."
It should allow us to say:
"This is a deterministic contract-auditing engine. Its pricing rules are independently tested, its matcher exposes uncertainty, its prediction path cannot access labels, its unseen holdout performance is measured separately, and the architecture is suitable for transferring to another hospital."
Start by creating the locked development/holdout split without exposing the holdout answers, then inspect the Hospital 1 contract and build the system from the contract outward.
