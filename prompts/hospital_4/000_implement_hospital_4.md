# Prompt 000 — implement Hospital 4

- **Used with:** Claude Code (Claude Opus 5.5)
- **Kind:** development prompt to the coding assistant. Hospital 4 has no runtime (LLM) prompt: its pipeline is fully deterministic
- **Produced:** `src/hospital_4/`, `tests/hospital_4/`, `artifacts/hospital_4/`, `outputs/hospital_4/`, and the addition of Hospital 4 to the combined submission
- **Preceded by:** [`prompts/project/006_professional_cleanup.md`](../project/006_professional_cleanup.md)
- **Provenance:** saved when issued (2026-09-23), copied verbatim from the session.

Transcribed verbatim below.

---

Implement Hospital 4 end-to-end in the existing InsuranceAuditTask repository.

Before changing anything, study the current codebase carefully, especially:

- src/shared/
- src/hospital_1/
- src/hospital_2/
- src/main.py
- src/submission.py
- tests/hospital_1/
- tests/hospital_2/
- outputs/hospital_1/
- outputs/hospital_2/
- artifacts/hospital_2/
- prompts/
- contracts/hospital_4/conditional_reimbursement_agreement.md
- invoices/hospital_4_invoices.jsonl
- invoices/hospital_4_invoices.csv
- invoices/hospital_4_line_items.csv
- submission_template.csv
- README.md
- DECISION_LOG.md

The goal is NOT to refactor the repository into a generic framework.

Follow the existing per-hospital architecture.

H1 and H2 are already completed and must remain behaviorally unchanged.

==================================================
1. NON-NEGOTIABLE SAFETY RULES
==================================================

Do NOT:

- modify H1 predictions
- modify H2 predictions
- change H1/H2 matching behavior
- change H1/H2 pricing behavior
- change existing persisted H2 semantic decisions
- rerun OpenRouter
- rerun Jev
- call any external API
- introduce runtime LLM dependency for H4
- use billed unit price to identify a service
- hard-code invoice IDs as known errors
- copy another participant's predictions
- use competitor outputs as labels
- invent contract rules
- force ambiguous service matches
- silently choose one interpretation when contract evidence is genuinely insufficient

After the H4 implementation, H1 and H2 regression tests must still pass.

Where possible, confirm H1/H2 generated scored outputs remain byte-identical.

==================================================
2. REQUIRED CODEBASE ARCHITECTURE
==================================================

Create a new Hospital 4 module following the same architectural style as H1/H2.

Create:

src/hospital_4/
├── __init__.py
├── contract.py
├── matcher.py
└── audit.py

tests/hospital_4/
├── __init__.py
├── conftest.py
├── test_contract.py
├── test_matching.py
└── test_audit.py

artifacts/hospital_4/
├── contract_rules.json
├── description_clusters.json
├── service_mappings.json
├── unresolved.json
└── pricing_traces.jsonl

outputs/hospital_4/
├── submission.csv
├── predictions.csv
├── findings.csv
├── audit_report.md
└── decision_log.md

prompts/hospital_4/
└── 000_implement_hospital_4.md

Save this implementation prompt itself as:

prompts/hospital_4/000_implement_hospital_4.md

Do not reorganize H1 or H2 into H4's folder.

Reuse src/shared/ only where the existing abstraction is already appropriate.

If something is truly Hospital-4-specific, keep it inside src/hospital_4/.

==================================================
3. HIGH-LEVEL H4 DESIGN
==================================================

Hospital 4 should combine the strongest parts of the existing design:

H1:
- deterministic contract parsing
- deterministic pricing
- explicit contract-rule attribution
- global hospital-wide state
- duplicate handling
- reconstruction separate from detection
- detailed pricing traces

H2:
- conservative service identity
- MATCHED / AMBIGUOUS / UNKNOWN
- candidate sets
- billed unit basis allowed only as a genuine textual tie-break
- never use billed price for service identity
- propagate ambiguity instead of collapsing it
- interval/range reasoning for uncertain global quantities
- pricing_complete
- correction_reconstructable
- uncertainty reasons
- confidence as evidence strength, not a claimed calibrated probability

For H4 initially:

NO LLM.
NO semantic.py.
NO OpenRouter.
NO Jev.

Build a strong deterministic solution first.

==================================================
4. CONTRACT PARSER
==================================================

Implement:

src/hospital_4/contract.py

Parse H4 directly from:

contracts/hospital_4/conditional_reimbursement_agreement.md

Do not create the rules manually from another participant's JSON.

The parser should extract and validate:

- contract identity
- hospital
- contract number
- effective dates
- currency
- rounding convention
- pricing order
- service catalogue
- base rates
- unit bases
- threshold premiums
- daily caps
- bundles
- cumulative volume discounts
- exclusions
- facility rules
- plan-tier rules
- duplicate/repeat billing clauses
- invoice-level validity clauses

Expected values discovered during review may be used ONLY as cross-checks after parsing:

- 98 contracted services
- 18 threshold-premium rules
- 18 daily caps
- 7 bundle pairs
- 3 services with cumulative-volume-discount schedules
- 4 cumulative discount thresholds total
- 15 exclusion rules
- no non-business-day/weekend uplifts

Verify these against the contract rather than assuming them.

Expected contract identity should also be independently parsed and tested:

INS-H4-2024-2049

Expected term:

2024-01-01 through 2025-12-31

Money must remain integer cents.

Use Decimal for percentage calculations and ROUND_HALF_UP.

Rounding must occur after every individual pricing step where the contract requires it.

Fail loudly if:

- a rule references an unknown service
- service names are duplicated unexpectedly
- a percentage cannot be parsed
- unit basis is missing
- unsupported rounding is encountered
- required tables/sections are incomplete
- contract invariants disagree

Preserve source provenance where practical:
- section/clause
- source wording/table
- contract SHA/fingerprint

Write a deterministic structured representation to:

artifacts/hospital_4/contract_rules.json

==================================================
5. DATA LOADING
==================================================

Use the existing occurrence-preserving shared loading architecture.

Prefer:

invoices/hospital_4_invoices.jsonl

as the canonical source of physical invoice occurrences.

The CSV files should be used as a cross-check, not as the sole source for joining lines to invoice records.

Do NOT associate line items with physical invoice occurrences by invoice_id alone because invoice IDs can be reused.

Verify the actual dataset statistics yourself.

Known expected cross-checks from review are approximately:

- 840 physical invoice occurrences
- 835 unique invoice IDs
- 10,560 physical line items

Do not hard-code these into runtime logic.

If the actual repository differs, trust the supplied files and report the difference.

Preserve earlier physical occurrences for hospital-wide historical calculations even when a later occurrence becomes the canonical submission row.

==================================================
6. H4 SERVICE MATCHER
==================================================

Implement:

src/hospital_4/matcher.py

This should follow the philosophy of src/hospital_2/matcher.py, adapted to H4.

The billed description is free text.

The matcher must NEVER consume:

- billed unit price
- billed line total
- invoice total
- patient ID
- invoice ID

Service identity must come from description text, plus a narrow unit-basis tie-break when permitted.

--------------------------------------------------
6.1 Normalization
--------------------------------------------------

Normalize:

- lowercase
- punctuation
- word ordering
- H4 billing reference suffixes such as /CW-####
- common harmless formatting differences

Build a transparent abbreviation vocabulary from billing shorthand actually present in H4.

Examples may include:

adv -> advanced
amb -> ambulatory
asst -> assisted
compr -> comprehensive
cont -> continuous
elect -> elective
emer -> emergency
ext -> extended
foc -> focused
inpt -> inpatient
intens -> intensive
interm -> intermittent
outpt -> outpatient
postop -> postoperative
preop -> preoperative
rtn -> routine
spclst -> specialist
std -> standard
supv -> supervised

card -> cardiac
derm -> dermatologic
endo -> endocrine
ent -> otolaryngologic
ger -> geriatric
gi -> gastrointestinal
haem -> haematology
hep -> hepatic
immun -> immunologic
infect -> infectious
metab -> metabolic
msk -> musculoskeletal
neuro -> neurological
obst -> obstetric
onc -> oncology
ophth -> ophthalmic
ortho -> orthopaedic
paed -> paediatric
pall -> palliative
psych -> psychiatric
pulm -> pulmonary
ren -> renal
rheum -> rheumatologic
urol -> urologic
vasc -> vascular

admin -> administration
anaes -> anaesthesia
anly -> analysis
bd -> bed
biop -> biopsy
conf -> conference
consult -> consultation
cr -> care
crit -> critical
cs -> case
diag -> diagnostic
dial -> dialysis
disch -> discharge
disp -> dispensing
endosc -> endoscopic
fract -> fraction
hm -> home
img -> imaging
inf -> infusion
interp -> interpretation
isol -> isolation
lab -> laboratory
monit -> monitoring
nurs -> nursing
nutr -> nutritional
obs -> observation
occ -> occupancy
physio -> physiotherapy
plng -> planning
pnl -> panel
proc -> procedure
prog -> programme
radiother -> radiotherapy
recov -> recovery
rehab -> rehabilitation
rm -> room
sess -> session
spcm -> specimen
steril -> sterilisation
supp -> support
svc -> service
telem -> telemetry
ther -> therapy
thtr -> theatre
tm -> time
transf -> transfusion
transp -> transport
vent -> ventilation
vst -> visit
wd -> ward
wnd -> wound

Do not blindly add aliases.

Every abbreviation should be defensible from H4 terminology/data.

--------------------------------------------------
6.2 Structured service identity
--------------------------------------------------

Do not rely only on fuzzy similarity.

Treat H4 service names structurally where possible:

QUALIFIER
+
SPECIALTY
+
SERVICE CONCEPT

For example:

Preoperative Haematology Imaging Interpretation

A description such as:

PREOP haem IMG interpretation

contains:

qualifier = preoperative
specialty = haematology
concept = imaging interpretation

and can be a strong deterministic match.

However:

ASSISTED spcm ANALYSIS

may still be ambiguous if the specialty is missing and several contracted services fit.

Do not choose the highest scoring service just because it is first.

Return one of:

MATCHED
AMBIGUOUS
UNKNOWN

MATCHED:
text contains enough discriminating evidence to identify exactly one contract service.

AMBIGUOUS:
description is consistent with multiple contracted services and lacks enough information to choose.

UNKNOWN:
the text contradicts all plausible contracted services or describes something outside the contract.

Preserve candidate services and matching evidence.

==================================================
7. UNIT-BASIS TIE BREAK
==================================================

Use the same anti-circular principle as H2.

The billed unit basis may be used ONLY when:

- the text already leaves a genuine tie between multiple plausible services
- exactly one tied service is compatible with the billed unit basis

If unit basis resolves identity, record:

used_unit_basis_for_identity = true

Then DO NOT use that same billed unit basis to produce a wrong_unit_basis finding on that line.

One piece of evidence cannot both establish identity and incriminate itself.

Support compound bases correctly, including forms such as:

per_hour_per_item

Do not collapse them into another basis.

==================================================
8. NEVER USE BILLED PRICE FOR SERVICE IDENTITY
==================================================

This is a strict rule.

Do NOT implement:

MATCHED_BY_PRICE

Do NOT use:

unit_price_cents

to decide between candidate services.

Do NOT use expected price similarity.

Do NOT use invoice totals.

The price is what is being audited.

Using it to determine service identity creates circular reasoning.

Tests should explicitly protect the matcher API from accidentally gaining a price parameter.

==================================================
9. DESCRIPTION CLUSTER ARTIFACTS
==================================================

Before pricing, create deterministic description analysis.

Write:

artifacts/hospital_4/description_clusters.json
artifacts/hospital_4/service_mappings.json
artifacts/hospital_4/unresolved.json

For each normalized description cluster record:

- raw examples
- normalized description
- occurrence count
- invoice count
- deterministic status
- selected service, if matched
- candidates
- evidence
- whether unit basis broke a tie
- ambiguity reason

Generate summary metrics:

- distinct normalized clusters
- MATCHED clusters
- AMBIGUOUS clusters
- UNKNOWN clusters
- matched line occurrences
- ambiguous line occurrences
- unknown line occurrences
- unit-basis-tiebreak occurrences
- affected invoice counts

Also rank unresolved descriptions by impact:

- number of line occurrences
- number of affected invoices
- whether candidate services participate in:
  - bundles
  - threshold premiums
  - caps
  - cumulative discounts
  - exclusion rules

Do not add an LLM stage automatically.

==================================================
10. GLOBAL AUDIT CONTEXT
==================================================

Implement Hospital-4-wide state in:

src/hospital_4/audit.py

Do not audit invoices independently when a contract rule depends on the entire hospital dataset.

Use the existing H1/H2 design concepts.

Track:

- patient/service/day aggregate quantities
- certain services on each patient/day
- possible services on each patient/day
- service dates per patient
- repeat/duplicate service occurrences
- prior cumulative utilization
- bundle partner presence
- exclusion relationships
- duplicate invoice occurrences

For ambiguity-sensitive quantities, preserve ranges:

Interval(low, high)

Example:

certain prior utilization = 76
possible prior utilization = 84
threshold = 80

Do NOT choose whether the volume discount applies.

The correct state is uncertain.

If:

low > threshold

the discount certainly applies.

If:

high <= threshold

it certainly does not.

If the range crosses the threshold:

pricing is uncertain.

Use the same principle for:

- threshold premiums
- bundles
- cumulative discounts
- exclusions
- duplicate-sensitive logic where appropriate

==================================================
11. H4 PRICING ORDER
==================================================

Follow the contract exactly.

For a resolved service, use the contract-defined order.

Expected structure:

base rate

then:
1. bundle substitution
2. facility multiplier
3. plan-tier multiplier
4. threshold premium/uplift
5. cumulative volume discount

then:
effective unit rate × payable quantity

Round HALF_UP after every contractually required adjustment.

Do not round only at the end.

Generate detailed stage traces.

Example trace shape:

base = ...
bundle = ...
facility = ...
plan = ...
premium = ...
discount = ...
expected_unit_rate = ...
payable_quantity = ...
expected_line_total = ...

==================================================
12. H4 DAILY CAP SEMANTICS
==================================================

Do NOT copy H1's daily-cap reconstruction policy blindly.

H1 deliberately left cap-breach corrected totals blank because H1 evidence did not reveal the true delivered quantity.

H4 contract wording is different.

H4 states that quantities above the maximum billable amount are not payable.

Therefore, where the relevant service/day quantity is deterministically known:

payable_quantity = min(contractual cap, payable nonduplicate quantity)

A single line with:

quantity = 13
cap = 8

can therefore have:

payable_quantity = 8

and a reconstructable expected total.

However, do NOT invent arbitrary allocation across multiple competing lines/invoices.

==================================================
13. DUPLICATE / REPEATED SERVICE POLICY
==================================================

H4 prohibits repeated billing of the same contracted service for the same patient and service date, including across invoices.

Follow the existing H1-informed deterministic convention unless H4 explicitly requires something else:

- first billing stands
- later repeats are non-payable

Use deterministic ordering:

invoice date
then invoice identifier
then line identifier

Document clearly that the ordering convention is an implementation policy where the contract prohibits repetition but does not specify how competing physical records should be allocated.

For later duplicate/repeated lines:

payable quantity = 0

Possible categories may include:

duplicate_service
cross_invoice_duplicate

Use existing repository category conventions where possible.

Do not invent category names unnecessarily.

==================================================
14. BUNDLES
==================================================

Bundle logic must be patient/day aware.

Apply a bundled rate only when both contracted services are certainly present for the same patient and service day.

If the bundle partner is only possibly present due to service ambiguity:

do not silently assume the bundle.

Carry both contractual possibilities.

If both possibilities result in the same final payable amount, reconstruction can still be certain.

Otherwise mark the affected pricing state incomplete/uncertain.

==================================================
15. THRESHOLD PREMIUMS
==================================================

Premium thresholds use aggregate quantity for:

(patient, service, service_date)

not only the current line.

Use interval reasoning if ambiguous lines might contribute to the same service.

Example:

low = 5
high = 8
premium threshold = 6

premium state is uncertain.

Do not force the premium on or off.

==================================================
16. CUMULATIVE VOLUME DISCOUNTS
==================================================

Cumulative utilization must follow the contract.

Use all relevant historical physical line occurrences.

Count across the whole contract term and all patients.

Use service-date ordering.

Use line-id ordering where the contract requires a same-day tie-break.

The current line must NOT count toward its own prior utilization.

Use prior-exclusive cumulative quantities.

For ambiguous historical lines, use low/high ranges.

Do not mark an entire discounted service unusable merely because one unresolved candidate exists.

Use interval logic to determine whether the threshold result is still contractually certain.

==================================================
17. EXCLUSION WINDOWS
==================================================

Apply exclusion windows hospital-wide.

Use the contract's exact directionality and boundary language.

If H4 says "within N days", document whether boundary day N is included and test it.

Do not infer cross-patient exclusions unless contract language clearly requires it.

Default clinical reading should remain patient-specific unless contract wording says otherwise.

A possible ambiguous trigger must not by itself prove an exclusion violation.

A certain trigger can.

==================================================
18. STRUCTURAL / STATELESS CHECKS
==================================================

Retain independent checks that do not require service identity.

Examples:

- contract_number_mismatch
- duplicate_invoice_id
- malformed_service_date
- service_date_out_of_window
- service_date_after_invoice_date
- line_total_arithmetic
- invoice_total_mismatch

These should still work even if some line identities are unresolved.

Detection must remain separate from monetary reconstruction.

==================================================
19. DETECTION VS RECONSTRUCTION
==================================================

Preserve the existing distinction between:

flagged
pricing_complete
correction_reconstructable
expected_total_cents

An invoice can be definitely erroneous even when the correct total cannot be reconstructed.

Never fill expected_total_cents with:

- billed total merely as a placeholder
- zero without contractual basis
- a guessed total
- a cap-adjusted amount when allocation is ambiguous
- an amount calculated from a forced service match

Leave expected_total_cents blank when a defensible corrected amount cannot be derived.

A provisional diagnostic amount may exist internally, but it must not be confused with submitted expected_total_cents.

==================================================
20. CONFIDENCE
==================================================

Confidence is evidence strength.

Do NOT describe it as a calibrated probability for H4 because H4 has no labels.

Build simple explainable bands based on:

- deterministic text match
- unit-basis tie-break
- unresolved ambiguity
- unknown service
- reconstructability
- global pricing uncertainty
- structural-only findings

Keep confidence conservative.

Document the policy in:

outputs/hospital_4/decision_log.md

Do not tune confidence based on competitor outputs.

==================================================
21. DETAILED AUDIT REPORT
==================================================

Generate:

outputs/hospital_4/audit_report.md

For flagged invoices, include enough information to manually verify why they were flagged.

For each relevant line include:

- line_id
- raw description
- normalized description
- match status
- matched service
- candidate services
- identity evidence
- whether unit basis was consumed for matching
- billed quantity
- billed basis
- billed unit price
- billed line total
- contract base rate
- bundle state
- facility multiplier
- plan multiplier
- patient/day aggregate quantity
- premium state
- prior cumulative utilization
- discount state
- cap
- payable quantity
- exclusion relationships
- duplicate relationships
- expected unit rate
- expected line total
- finding categories
- relevant contract clause
- uncertainty reasons

The report should be useful for a human reviewer.

==================================================
22. PRICING TRACES
==================================================

Write machine-readable detailed traces to:

artifacts/hospital_4/pricing_traces.jsonl

Every expected amount should be explainable stage by stage.

Do not create traces only for successful cases.

Include enough information to understand why pricing became incomplete.

==================================================
23. TESTS
==================================================

Create substantial Hospital 4 tests.

At minimum test:

CONTRACT:
- identity
- effective dates
- service count
- premium count
- cap count
- bundle count
- cumulative-discount counts
- exclusion count
- no weekend uplift
- compound unit basis parsing
- contract references only known services
- unsupported rounding fails
- malformed rules fail

MATCHING:
- exact canonical description
- reordered words
- abbreviations
- /CW-#### removal
- strong unique match
- missing specialty -> ambiguous
- contradictory specialty -> unknown
- genuine text tie
- unit-basis tie-break
- basis not reused for wrong_unit_basis
- matcher accepts no price input
- repeated calls deterministic

AUDIT:
- bundle applies to both services
- threshold premium aggregates across multiple lines
- threshold premium aggregates across invoices
- premium ambiguity propagates
- cumulative discount is prior-exclusive
- threshold crossing behavior
- cumulative ambiguity interval crossing
- daily cap on single line
- daily cap across lines
- repeat service within invoice
- repeat service across invoices
- later duplicate becomes non-payable
- exclusion trigger earlier
- exclusion trigger later if contract is symmetric
- exclusion boundary day
- line arithmetic
- invoice arithmetic
- contract mismatch
- malformed date
- date outside contract
- reused invoice IDs
- HALF_UP rounding after each stage
- overlapping synthetic adjustments
- unresolved service does not produce guessed expected total
- definite structural finding survives unresolved service identity

REGRESSION:
- all H1 tests pass
- all H2 tests pass
- existing H1/H2 scored outputs unchanged unless formatting infrastructure intentionally requires rebuilding an identical file

==================================================
24. H4 OUTPUTS
==================================================

Generate:

outputs/hospital_4/predictions.csv
outputs/hospital_4/findings.csv
outputs/hospital_4/submission.csv
outputs/hospital_4/audit_report.md
outputs/hospital_4/decision_log.md

submission.csv columns must exactly match the challenge template:

invoice_id
flagged
error_category
expected_total_cents
billed_total_cents
confidence

Aim to produce one H4 submission row per unique invoice ID when we have a defensible opinion.

The dataset is expected to have 835 unique H4 invoice IDs, but verify this yourself.

Correct invoices are useful and scored, so do not submit only flagged invoices.

==================================================
25. COMBINED SUBMISSION
==================================================

Update the existing submission-building logic carefully.

Current scored submission contains Hospital 2.

After H4 is complete, the scored hospitals should become:

Hospital 2
Hospital 4

Hospital 1 remains development-only and must never enter the scored submission.

Hospital 3 and Hospital 5 remain unimplemented unless explicitly requested later.

Combined submission should therefore contain:

H2 rows
+
H4 rows

Do not create fake H3/H5 rows.

Do not change H2 rows when adding H4.

Update tests accordingly.

==================================================
26. CLI / src.main
==================================================

Extend the existing CLI using the same style already used for H1/H2.

Desired commands should fit the current command structure, for example:

python -m src.main audit h4

and the existing:

python -m src.main submission

Do not redesign the entire CLI.

The normal H4 audit/reproduction path must be offline.

==================================================
27. DECISION LOG
==================================================

Write:

outputs/hospital_4/decision_log.md

Document at minimum:

- JSONL as canonical physical occurrence representation
- reused invoice ID policy
- duplicate/repeat service ordering policy
- unit basis used only as a text-tie-break
- billed price never used for service identity
- H4 cap semantics
- ambiguous matching policy
- unknown-service policy
- interval uncertainty policy
- bundle ambiguity policy
- threshold ambiguity policy
- cumulative discount ambiguity policy
- exclusion-window interpretation
- confidence policy
- why no H4 LLM stage was introduced
- any genuinely unresolved contract wording

Do not pretend assumptions are explicit contract rules.

Separate:

CONTRACT RULE
from
IMPLEMENTATION POLICY
from
UNRESOLVED AMBIGUITY

==================================================
28. DOCUMENTATION
==================================================

Update README.md only after H4 is actually complete.

Clearly state:

Hospital 1:
development/evaluation only

Hospital 2:
implemented and submitted

Hospital 4:
implemented and submitted

Hospitals 3 and 5:
not implemented and not used to produce submitted predictions

Update the root DECISION_LOG.md carefully so it remains roughly one page and reflects the new scope.

Do not rewrite H1/H2 history inaccurately.

==================================================
29. DIFFERENTIAL REVIEW AGAINST PUBLIC FORKS
==================================================

Only AFTER our H4 implementation is independently complete, you may create a local diagnostic comparison against public participant H4 outputs if those repositories are already available to you.

This is for debugging only.

Never use their outputs as ground truth.

Never majority-vote predictions.

Never change our prediction solely because another participant disagrees.

If used, produce a diagnostic such as:

invoice_id
ours
participant_A
participant_B
participant_C
our_reason

Then manually inspect disagreements against the actual H4 contract/data.

Do not commit competitor predictions into the production pipeline.

==================================================
30. IMPLEMENTATION ORDER
==================================================

Follow this order:

PHASE 1
Inspect current architecture and H4 contract/data.

PHASE 2
Implement H4 contract parser and contract tests.

PHASE 3
Profile H4 descriptions and implement deterministic matcher.

PHASE 4
Generate description_clusters.json, service_mappings.json and unresolved.json.

STOP and report matching coverage before introducing any semantic/AI stage.

PHASE 5
Implement global H4 AuditContext and interval uncertainty.

PHASE 6
Implement deterministic pricing and contractual findings.

PHASE 7
Implement reconstruction logic, confidence and detailed traces.

PHASE 8
Generate H4 outputs and audit report.

PHASE 9
Run all H4 tests and all existing repository tests.

PHASE 10
Integrate H4 into combined scored submission without altering H2 rows.

PHASE 11
Update README / decision logs / prompt index.

==================================================
31. FINAL VALIDATION
==================================================

Before finishing, verify:

- all tests pass
- H1 behavior unchanged
- H2 behavior unchanged
- no external API was called
- no API key is required to reproduce H4
- no billed price was used for service identity
- H4 parser derives rules from contract
- H4 descriptions preserve ambiguity where necessary
- pricing uses integer cents / Decimal only
- ROUND_HALF_UP occurs at correct stages
- global rules use all physical historical occurrences where required
- H4 submission contains unique invoice IDs
- H1 is absent from scored submission
- H3/H5 are absent
- H2 rows are unchanged
- no secrets are committed
- .env is untouched
- generated output schema exactly matches submission_template.csv

==================================================
32. FINAL RESPONSE TO ME
==================================================

When complete, do not just say "done."

Give me a compact engineering report containing:

1. Files created
2. Files modified
3. H4 contract-rule counts actually parsed
4. H4 dataset counts actually observed
5. Description-matching results:
   - clusters
   - matched
   - ambiguous
   - unknown
   - unit-basis tie-breaks
6. H4 submission:
   - rows
   - flagged
   - pricing_complete
   - correction_reconstructable
   - blank expected totals
7. Breakdown of finding categories
8. Important unresolved ambiguities
9. Tests passed
10. Confirmation that H1/H2 outputs did not change
11. Confirmation that no API calls were made
12. git diff --stat
13. git status --short

If you encounter a genuine contract ambiguity, do not silently decide it.

Record it and explain what you chose and why.
