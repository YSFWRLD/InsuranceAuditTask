# Prompt 000 — implement Hospital 2

- **Used with:** Claude Code (Claude Opus 5)
- **Produced:** `src/hospital_2/`, `tests/hospital_2/`, `artifacts/hospital_2/`,
  `outputs/hospital_2/`, and the two runtime prompts first written in this
  directory: [001](001_service_classifier.md) and [002](002_jev_verifier.md).
  002 was later superseded by [003](003_jev_verifier.md) and never sent to Jev;
  see [the index](../README.md)
- **Preceded by:** [`prompts/hospital_1/`](../hospital_1/)

Transcribed verbatim below.

---

Implement Hospital 2 completely in the existing insurance auditing project.

Hospital 1 is already implemented, tested, and should be treated as frozen.

Do not rewrite Hospital 1.
Do not refactor the project again.
Do not create Hospitals 3–5.

This task is to add a real Hospital 2 implementation that fits the current per-hospital architecture.

IMPORTANT EXECUTION RULE:

Do not stop after inspecting the repository or proposing a plan.
Do not ask me to approve intermediate steps.
Inspect the project, implement H2, test it, run it, produce the outputs, and finish the task in one continuous workflow.

If an external credential is required and unavailable, implement everything that can be implemented locally and create the required export/import workflow rather than inventing API responses.

==================================================
CURRENT ARCHITECTURE
==================================================

The project already uses:

src/
├── shared/
├── hospital_1/
└── main.py

Add:

src/
└── hospital_2/
    ├── contract.py
    ├── matcher.py
    ├── semantic.py
    └── audit.py

Also add appropriate:

tests/hospital_2/
artifacts/hospital_2/
outputs/hospital_2/
prompts/hospital_2/

Do not introduce a new architecture.

Reuse code from src/shared/ where it genuinely applies.

If H2 reveals something that is truly generic and belongs in shared/, make the smallest safe change necessary.

Do not move H1-specific behavior into shared/ merely to avoid duplication.

==================================================
SOURCE OF TRUTH
==================================================

Use only the challenge repository as task truth.

Relevant files:

README.md

contracts/hospital_2/master_services_agreement.md

invoices/hospital_2_invoices.jsonl
invoices/hospital_2_invoices.csv
invoices/hospital_2_line_items.csv

submission_template.csv

Hospital 1 may be used to understand project conventions and to prevent regressions, but:

- H1 labels are not H2 labels
- H1 service mappings are not H2 mappings
- H1 outcomes must not be transferred to H2

There are no provided H2 labels.

Do not copy mappings or predictions from public competitor repositories.

Do not optimize toward another solution's number of flagged invoices.

==================================================
FIRST: VERIFY THE DATA AND CONTRACT
==================================================

Inspect the actual files yourself.

The following facts were previously observed and should be verified:

- contract number: INS-H2-2024-1183
- provider: St. Auben Metropolitan Hospital Trust
- effective: 2024-01-01 through 2025-12-31
- currency: GBP
- rounding: half-up cent
- facility: F-MAIN
- plan tiers do not alter rates
- 76 contracted services
- approximately 1,132 invoice occurrences
- 1,125 unique invoice IDs
- approximately 14,360 line items
- approximately 506 distinct raw descriptions

There are repeated invoice IDs representing genuinely different invoice occurrences.

Therefore:

DO NOT use invoice_id as the internal primary key.

Use hospital_2_invoices.jsonl as the canonical invoice source because it preserves each occurrence and its correct nested line items.

Assign each occurrence a stable internal identifier.

Do not join headers and lines solely by invoice_id.

==================================================
H2 DESIGN
==================================================

H2 should use this pipeline:

raw H2 data
    ↓
contract extraction
    ↓
description normalization
    ↓
deterministic candidate generation
    ↓
clear match?
   / \
 yes  no
 ↓     ↓
use    semantic classifier
        ↓
       Jev verifier
        ↓
 accepted / rejected / unresolved
        ↓
persist service mappings
        ↓
build global billing context
        ↓
deterministic Python pricing
        ↓
validation/findings
        ↓
invoice results
        ↓
submission output

The LLM is allowed to solve semantic service identity only.

Python must make every financial and contractual decision.

==================================================
1. hospital_2/contract.py
==================================================

Implement deterministic extraction of the H2 contract.

Do not manually hardcode all 76 services if the Markdown can be reliably parsed.

Extract for each service:

- clause_id
- canonical service_name
- base_rate_cents
- contractual unit_basis
- bundle rules
- weekend/non-business-day uplifts
- daily aggregate quantity uplifts
- cumulative volume discounts
- daily caps
- exclusion windows
- source clause/provenance

Retain enough source text to explain each rule.

Generate:

artifacts/hospital_2/contract_rules.json

Include a fingerprint such as SHA256 of the source contract so semantic mappings can be invalidated if the contract changes.

Fail visibly if a service clause cannot be parsed.

Do not silently default missing values.

Expected sanity checks observed from the contract:

- 76 services
- 8 non-business-day uplift services
- 9 daily aggregate uplift services
- 8 cumulative utilisation discount services
- 8 hard daily caps
- 6 exclusion-window rules
- 3 bundle pairs

Verify these counts from the actual contract.

Pay special attention to clause 4.2:

Assisted Infectious Telemetry Monitoring

The phrase is:

"per hour, per item"

Represent its unit basis correctly rather than silently collapsing it to a normal per_hour service.

==================================================
GLOBAL H2 CONTRACT RULES
==================================================

Article III defines pricing behavior.

Use exact integer-cent/Decimal arithmetic.

Never use binary floating point for money.

Apply contractual adjustments in this order:

1. bundled rate substitution
2. facility multiplier
3. plan-tier multiplier
4. premium/uplift
5. cumulative volume discount
6. effective unit rate × quantity

For H2, facility and plan multipliers are effectively 1, but preserve the explicit pricing order.

Round HALF_UP after each adjustment step when required by the contract.

CUMULATIVE UTILISATION

This is global across the whole agreement and all patients.

Process lines by:

service_date ascending
then line_id ascending when dates tie

The contract defines cumulative utilisation as usage PRIOR to the current line.

Interpret threshold wording literally.

Do not split a line across a threshold unless the contract explicitly requires it.

DAILY AGGREGATES

Rules based on patient + service + Service Day must aggregate across invoice boundaries.

Do not evaluate these per line or per invoice in isolation.

BUNDLES

Bundle detection may require lines from different invoices belonging to the same patient on the same Service Day.

Do not restrict bundle detection to one invoice.

EXCLUSION WINDOWS

Article 3.6 states that exclusion windows are measured in either direction.

Use patient history across invoices.

Document how "within N days" is interpreted.

CAPS

A cap violation can be confidently detected when the billed aggregate quantity exceeds the contractual maximum.

However, do not automatically assume:

correct quantity = cap

The true delivered quantity may be unknowable.

Separate violation detection from monetary reconstructability.

==================================================
ARTICLE XIII
==================================================

Implement invoice-level contractual checks including:

- invoice submitted no later than 60 days after discharge
- invoice number unique across the whole agreement
- correct contract number

Treat invoice_date as the submission date and record this assumption.

Hospital 2 contains repeated invoice IDs.

Preserve every occurrence internally.

Use the H1 development behavior as a useful serialization convention:

- first occurrence establishes the ID
- later occurrence is the duplicate occurrence

Do not merge the two invoices.

==================================================
STRICT DATE HANDLING
==================================================

Dates must be parsed strictly.

Invalid dates such as:

2024-13-05
31/02/2024
2025-06-31
not-a-date

must remain invalid.

Do not use permissive parsing that silently changes them into another date.

Validate:

- malformed service date
- service date outside contract term
- service date after invoice date where defensible
- invoice/discharge chronology
- 60-day submission rule

==================================================
2. hospital_2/matcher.py
==================================================

This module handles deterministic service identification and candidate generation.

The invoice description is free text.

The README explicitly states it is not a contract term and not a service code.

Descriptions contain abbreviations and variants such as:

ADV
AMB
ASST
CARD
CONT
ELECT
EMER
ENT
FOC
GI
HAEM
IMG
INF
INTENS
INTERM
MSK
NEURO
OBST
OCC
OPHTH
ORTHO
PAED
PALL
PHARM
PHYSIO
PLNG
PROC
PULM
RECOV
RM
RTN
SPCM
SUPP
SVC
THTR
TM
TRANSP
VST

Build normalization systematically from observed H2 descriptions and contract vocabulary.

Do not blindly rely on this list.

Descriptions frequently include suffixes such as:

/SA-1234

Treat these as non-semantic billing noise.

DO NOT treat SA numbers as hidden service codes.

Remove/ignore them for service identity.

==================================================
MATCH STATES
==================================================

Use explicit states:

MATCHED
AMBIGUOUS
UNKNOWN

Never force an ambiguous description into a service.

==================================================
DETERMINISTIC MATCHING
==================================================

Use signals such as:

- normalized tokens
- abbreviation expansion
- modifier words
- specialty
- procedure/service type
- token overlap
- order-insensitive similarity
- character similarity when helpful

Return a ranked bounded candidate set, preferably around 3–5 services.

Most obvious descriptions should be solved deterministically.

Do not send all 14,000+ lines to an LLM.

Cluster normalized descriptions so one semantic decision can resolve repeated descriptions.

==================================================
CRITICAL ANTI-CIRCULARITY RULE
==================================================

DO NOT use:

- unit_price_cents
- line_total_cents
- invoice_total_cents
- invoice_id
- patient_id

to identify a service.

Most importantly:

NEVER use billed price to determine service identity.

Text is the primary evidence.

The billed unit basis may only be used when two or more candidates remain genuinely textually plausible.

If billed unit basis is used to break that tie, record:

used_unit_basis_for_identity = true

If that occurs, the same unit basis cannot later be used as evidence for:

wrong_unit_basis

Do not use the same evidence both to identify the service and accuse the invoice of having the wrong unit basis.

==================================================
3. hospital_2/semantic.py
==================================================

This module contains semantic AI functionality only.

No pricing logic belongs here.

Use a configurable semantic classifier.

Default model:

z-ai/glm-5.3-flash

via OpenRouter.

Do not hardcode credentials.

Use environment variables/configuration.

Only unresolved deterministic description clusters should be sent to the classifier.

==================================================
LLM INPUT
==================================================

Send only information needed for semantic identity:

- raw description
- normalized description
- candidate canonical service names
- candidate clause IDs
- relevant short contract text
- candidate contractual unit basis

Prefer an initial semantic decision without exposing billed unit basis.

If necessary, a second controlled tie-break pass may use billed unit basis, subject to the consumed-evidence rule.

Never send billed price or totals as identity evidence.

==================================================
LLM OUTPUT
==================================================

Require strict structured output:

{
  "selected_service": "canonical service name or null",
  "status": "MATCHED | AMBIGUOUS | UNKNOWN",
  "confidence": 0.0,
  "evidence_clause_ids": ["..."],
  "reason": "short explanation"
}

Use OpenRouter structured JSON/schema support where available.

Do not scrape JSON out of arbitrary prose unless absolutely necessary.

If the provider returns invalid output:

- retry only a small bounded number of times
- then leave the case unresolved
- record the failure

Never fabricate an answer.

==================================================
JEV VERIFIER
==================================================

After the classifier proposes a decision, verify it with Jev.

Target version:

jev-1.13.0

or make the version configurable.

Jev is a judge, not the primary classifier.

Give Jev:

- the raw description
- candidate services
- relevant contract evidence
- classifier status
- classifier selected service
- classifier reasoning
- permitted unit-basis evidence

Jev returns:

ACCEPT
REJECT
UNCERTAIN

Use the probability distribution, not just the top categorical choice.

Default gate:

if P(ACCEPT) >= 0.90:
    accept the classifier decision

elif P(REJECT) >= 0.90:
    reject the classifier proposal

else:
    unresolved

IMPORTANT:

If the classifier says AMBIGUOUS or UNKNOWN and Jev ACCEPTS that decision,
the final result remains AMBIGUOUS or UNKNOWN.

Do not interpret Jev ACCEPT as permission to force a service.

==================================================
JEV ACCESS
==================================================

If direct Jev API access is available, support it.

Also support manual Playground usage through batch files because Jev may not be programmatically accessible.

Create something like:

artifacts/hospital_2/jev_state.json
artifacts/hospital_2/jev_questions.json
artifacts/hospital_2/jev_results.json

Provide commands for:

- exporting unresolved decisions for Jev
- importing Jev results
- rebuilding final service mappings

If Jev credentials/access are not available during this coding task:

DO NOT fake verification results.

Implement the export/import workflow completely and leave pending cases pending.

==================================================
PERSISTED SERVICE MAPPINGS
==================================================

Persist semantic decisions in:

artifacts/hospital_2/service_mappings.json

Each cluster record should include useful provenance:

- contract hash
- normalized description
- representative raw descriptions
- deterministic candidate list
- deterministic evidence
- classifier model
- classifier prompt version
- classifier result
- Jev version
- Jev probabilities
- final status
- selected service or null
- used_unit_basis_for_identity
- unresolved reason where applicable

Once accepted mappings exist, normal H2 auditing should be deterministic and offline.

Do not call the LLM every time the audit runs.

If the contract hash changes, invalidate stale mappings.

Provide a deliberate refresh command for semantic mappings.

==================================================
PROMPTS
==================================================

AI assistance must be documented.

Create versioned prompt files such as:

prompts/hospital_2/001_service_classifier.md
prompts/hospital_2/002_jev_verifier.md

These files must contain the actual substantive prompts used.

Do not claim reconstructed prompts are verbatim if they are not.

==================================================
4. hospital_2/audit.py
==================================================

This is the deterministic H2 audit engine.

Build global indexes before auditing:

- by patient
- by service
- by patient + service + Service Day
- by patient + Service Day
- service lines sorted chronologically
- invoice occurrence order
- duplicate invoice ID occurrences

These indexes support:

- daily aggregate rules
- caps
- bundles
- exclusion windows
- cumulative utilisation

==================================================
PRICING TRACE
==================================================

For resolved lines, calculate the contractual rate in Python.

Keep a useful explainable trace, for example:

{
  "base_rate_cents": ...,
  "bundle_rate_cents": ...,
  "facility_multiplier": ...,
  "plan_multiplier": ...,
  "uplift": ...,
  "volume_discount": ...,
  "effective_unit_rate_cents": ...,
  "quantity": ...,
  "expected_line_total_cents": ...,
  "source_clause_ids": [...]
}

Do not let the LLM calculate any field in this structure.

==================================================
VALIDATIONS
==================================================

Implement evidence-supported findings including where applicable:

- contract_number_mismatch
- duplicate_invoice_id
- late_invoice_submission
- malformed_service_date
- service_date_out_of_contract
- service_date_after_invoice_date
- facility_mismatch
- unknown_service
- ambiguous_service_description
- wrong_unit_basis
- unit_price_mismatch
- line_total_arithmetic
- invoice_total_mismatch
- bundle_not_applied
- bundle_incorrectly_applied
- premium_omitted
- premium_incorrectly_applied
- volume_discount_omitted
- volume_discount_incorrectly_applied
- daily_cap_exceeded
- exclusion_window_violation

Do not mechanically copy H1 categories unless H2 contract/data actually support them.

For every finding, distinguish:

- contract-supported
- arithmetic/data-supported
- inferred/assumption-based

==================================================
UNRESOLVED SEMANTIC DEPENDENCIES
==================================================

Do not treat UNKNOWN or AMBIGUOUS services as if they did not exist.

An unresolved service may affect:

- cumulative utilisation
- daily aggregates
- bundles
- exclusions
- invoice expected total

If unresolved identity prevents reliable downstream pricing, propagate the uncertainty.

Do not silently calculate downstream values under an assumption of absence.

==================================================
DETECTION VS RECONSTRUCTION
==================================================

Track separately:

flagged

pricing_complete

correction_reconstructable

These are not the same thing.

Examples:

A duplicate invoice number can be confidently flagged even if monetary pricing is otherwise valid.

A cap violation can be identified even when the actual delivered quantity is unknowable.

An UNKNOWN description can be a contractual description problem while making exact pricing unreconstructable.

Do not invent expected totals for unreconstructable cases.

==================================================
CONFIDENCE
==================================================

Confidence should reflect evidence quality.

Do not pretend a heuristic score is a calibrated probability.

H2 has no labels, so do not claim calibrated H2 accuracy.

Use factors such as:

- deterministic structural evidence
- contract extraction certainty
- service-match evidence
- Jev verification
- pricing completeness
- unresolved dependencies

Be conservative.

The README explicitly says confidently wrong extraction is worse than uncertainty.

==================================================
OUTPUTS
==================================================

Produce at minimum:

artifacts/hospital_2/contract_rules.json

artifacts/hospital_2/description_clusters.json

artifacts/hospital_2/service_mappings.json

artifacts/hospital_2/unresolved.json

outputs/hospital_2/predictions.csv

outputs/hospital_2/findings.csv

outputs/hospital_2/audit_report.md

outputs/hospital_2/decision_log.md

The internal result format should preserve invoice occurrences even though final submission format is invoice_id based.

==================================================
SUBMISSION
==================================================

Update the existing submission infrastructure so H2 predictions can be emitted in the challenge format:

invoice_id
flagged
error_category
expected_total_cents
billed_total_cents
confidence

Do not add H1 rows to the final scored submission merely because H1 is the development hospital.

Do not solve H3–H5 yet.

If H2 duplicate invoice IDs create a serialization ambiguity, document the policy explicitly rather than silently collapsing them.

Use the H1 development convention as supporting evidence where appropriate.

==================================================
TESTS
==================================================

Create:

tests/hospital_2/test_contract.py
tests/hospital_2/test_matching.py
tests/hospital_2/test_semantic.py
tests/hospital_2/test_audit.py

Do not create dozens of tiny test files.

Contract tests should include:

- exactly 76 services
- rates parsed to cents
- correct unit bases
- special-rule counts
- bundle reciprocity
- clause provenance
- clause 4.2 special unit basis
- contract hash/fingerprint behavior

Matching tests:

- SA suffix does not affect identity
- billed-price perturbation does not change identity
- obvious abbreviations resolve
- genuine close descriptions remain ambiguous
- contradictory descriptions can become unknown
- unit basis is only used after textual ambiguity
- consumed unit-basis evidence cannot create wrong_unit_basis

Semantic tests:

- strict structured output validation
- malformed provider response fails safely
- API outage does not create fake mappings
- repeated descriptions are classified once
- persisted mappings support offline audit
- Jev low-margin result remains unresolved
- Jev acceptance of AMBIGUOUS preserves AMBIGUOUS
- Jev acceptance of UNKNOWN preserves UNKNOWN

Audit tests:

- exact HALF_UP rounding
- pricing order
- cumulative discounts span all patients
- same-date cumulative ordering uses line_id
- daily aggregates span invoices
- bundles span invoices
- exclusions span invoices
- exclusion windows operate in both date directions
- duplicate invoice occurrences remain distinct
- malformed dates stay malformed
- no floating-point money
- unresolved mappings propagate uncertainty

==================================================
H1 REGRESSION SAFETY
==================================================

Before H2 changes:

run the existing H1 tests and record the baseline.

After H2 implementation:

run the complete test suite again.

Hospital 1 should remain unchanged.

Expected current baseline is approximately:

190 passing tests
913 H1 invoices
58 flagged
4 corrected totals declined

Do not rely blindly on these numbers.
Confirm them from the current repository.

If H1 behavior changes because of shared-code modifications, investigate and fix the regression.

==================================================
CLI
==================================================

Extend the existing CLI naturally.

Commands should support the equivalent of:

audit h2

prepare H2 mappings

export Jev batch

import Jev batch

generate H2 predictions

The exact command syntax should match the style of the existing src/main.py.

Do not replace the CLI architecture.

==================================================
README
==================================================

Update the README to document:

- H2 architecture
- deterministic vs semantic responsibilities
- OpenRouter environment variable/configuration
- how to generate semantic mappings
- how Jev verification works
- how to use manual Jev export/import
- how to run H2 fully offline after mappings exist
- how to run tests
- how to generate H2 predictions

Do not claim H2 accuracy because there are no labels.

==================================================
DECISION LOG
==================================================

Document important assumptions and unresolved issues, including:

- invoice_date interpreted as submission date
- duplicate invoice occurrence serialization
- exclusion-window boundary interpretation
- unresolved semantic descriptions
- cases where unit basis was consumed for identity
- cases where monetary reconstruction is impossible
- any H2 contract wording that remains genuinely ambiguous

==================================================
DO NOT
==================================================

Do not:

- change H1 predictions
- rewrite the architecture
- implement H3–H5
- copy competitor mappings
- use hidden/imagined H2 labels
- hardcode known bad H2 invoice IDs
- treat SA numbers as service codes
- use billed prices to identify services
- force ambiguous descriptions
- ask the LLM to calculate prices
- use floating point for money
- collapse duplicate invoice occurrences internally
- silently skip unresolved lines
- ignore unresolved lines when they may alter cumulative/global rules
- fabricate API/Jev responses
- tune toward a target flagged count
- claim H2 accuracy without labels
- stop after producing a plan

==================================================
EXECUTION
==================================================

Perform the work autonomously.

Internally:

1. inspect the current project
2. run H1 baseline tests
3. inspect H2 contract/data
4. implement contract extraction
5. implement deterministic matcher
6. implement semantic workflow
7. implement deterministic H2 audit
8. add tests
9. generate available artifacts
10. run H2 audit
11. run full regression suite
12. update documentation

These are NOT approval checkpoints.

Continue through them automatically.

If external GLM or Jev access is unavailable, finish the code and export workflow and clearly identify only the external semantic decisions that remain pending.

==================================================
FINAL RESPONSE
==================================================

When finished, report concisely:

- files created/changed
- H1 regression result
- H2 contract extraction results
- number of invoice occurrences
- number of raw/normalized description clusters
- number resolved deterministically
- number requiring semantic classification
- GLM results if actually executed
- Jev results if actually executed
- unresolved mappings
- number of H2 invoices/occurrences audited
- findings by category
- number with pricing_complete=true
- number with correction_reconstructable=true
- generated output paths
- assumptions/limitations
- any external step still required

Do not report fake semantic results if APIs were unavailable.

Actually implement the H2 solution.
