# Prompt 002 — reorganise by hospital (structural refactor)

- **Used with:** Claude Code (Claude Opus 5)
- **Preceded by:** [001](001_build_reference_engine.md)
- **Produced:** the `src/shared/` + `src/hospital_1/` layout, frozen at the
  refactor entry in `artifacts/hospital_1/freeze.json`. The refactor is
  behaviour-preserving: `predictions.csv` is byte-identical to the one
  produced by the original frozen implementation (same `predictions_sha256`).

Transcribed verbatim below.

---

I want you to refactor the architecture of this project.

IMPORTANT:
Hospital 1 is currently the ONLY implemented hospital.

Hospital 2 has NOT been implemented yet.
Do not create Hospital 2 code during this refactor.
Do not create fake placeholders, empty modules, speculative abstractions, or APIs for H2.

The purpose of this refactor is to clean Hospital 1 now so that adding H2 afterward is easy.

This is a REFACTOR, not a rewrite.

Do not change working Hospital 1 audit logic, predictions, pricing rules, service matching behavior, or evaluation results unless you discover an actual bug and explain it before changing behavior.

==================================================
GOAL
==================================================

The current Hospital 1 implementation is spread across too many small files.

I want the repository organized primarily BY HOSPITAL.

A reviewer should be able to open:

    src/hospital_1/

and understand almost the entire Hospital 1 solution.

At the same time, truly reusable infrastructure should live in:

    src/shared/

Do not build a miniature enterprise framework.

Aim for approximately 3–5 meaningful files for Hospital 1.

==================================================
TARGET ARCHITECTURE FOR NOW
==================================================

Refactor toward:

src/
├── shared/
│   ├── models.py
│   ├── data.py
│   ├── money.py
│   └── submission.py
│
├── hospital_1/
│   ├── contract.py
│   ├── matcher.py
│   ├── audit.py
│   └── evaluation.py
│
└── main.py

tests/
├── shared/
│   ├── test_data.py
│   └── test_money.py
│
└── hospital_1/
    ├── test_contract.py
    ├── test_matching.py
    ├── test_audit.py
    └── test_evaluation.py

artifacts/
└── hospital_1/

outputs/
└── hospital_1/

prompts/
└── hospital_1/

DO NOT create:

src/hospital_2/
src/hospital_3/
src/hospital_4/
src/hospital_5/

yet unless one of those hospitals already contains real implementation code.

We will add each hospital when we actually implement it.

==================================================
WHAT BELONGS IN shared/
==================================================

Only move code into shared/ if it is genuinely hospital-independent.

shared/models.py

Generic structures such as:

- Invoice
- InvoiceOccurrence
- LineItem
- ServiceRule
- AuditFinding
- AuditResult
- MatchResult
- MatchStatus

Do not put H1-specific rules here.

shared/data.py

Generic functionality such as:

- JSONL loading
- CSV loading
- strict date parsing
- occurrence-safe invoice loading
- generic source validation

shared/money.py

Only reusable financial primitives:

- integer-cent handling
- Decimal
- ROUND_HALF_UP
- percentage calculation
- exact multiplication
- reusable rounding helpers

Do not put H1 premiums, bundle rates, caps, discounts, or thresholds here.

shared/submission.py

Generic conversion of audit results to:

invoice_id
flagged
error_category
expected_total_cents
billed_total_cents
confidence

==================================================
HOSPITAL 1
==================================================

Hospital 1 should mainly live inside:

    src/hospital_1/

Use approximately:

contract.py
matcher.py
audit.py
evaluation.py

-------------------------
contract.py
-------------------------

Move Hospital 1 contract parsing/extraction here.

This includes H1-specific:

- service definitions
- base rates
- unit bases
- premiums/uplifts
- volume discounts
- bundles
- caps
- exclusions
- contract validation
- source clause provenance

-------------------------
matcher.py
-------------------------

Move Hospital 1 description matching here.

Include:

- normalization
- aliases
- token matching
- fuzzy/semantic heuristics currently used
- ambiguity handling
- match evidence

Do not use labels or expected totals here.

-------------------------
audit.py
-------------------------

This should become the main Hospital 1 implementation.

Merge closely related logic currently spread across files such as:

- context.py
- validators.py
- auditor.py
- confidence.py
- pipeline.py
- H1-specific pricing code

audit.py may contain logically separated sections/classes/functions for:

- global context
- duplicate detection
- date validation
- arithmetic checks
- contract-number validation
- service-date validation
- pricing orchestration
- premiums
- bundles
- cumulative discounts
- caps
- exclusions
- expected totals
- findings
- evidence/confidence

Do not reduce code quality.

We are reducing FILE fragmentation, not combining everything into one giant function.

-------------------------
evaluation.py
-------------------------

Merge H1 development/evaluation functionality here.

This may contain:

- loading H1 labels
- metrics
- per-category evaluation
- error analysis
- calibration analysis
- development/holdout utilities if still needed
- generalization checks if they remain valuable

A separate split.py should not exist unless there is a genuinely strong reason.

==================================================
FILES TO REVIEW FOR MERGING
==================================================

Inspect the actual project first.

I expect files conceptually like:

pipeline.py
context.py
validators.py
confidence.py
split.py

to likely disappear after their useful code is merged.

Do NOT blindly delete them.

For every old file:

1. determine its responsibility
2. determine whether the responsibility is still necessary
3. move useful code
4. update imports
5. only then delete the old file

==================================================
DO NOT PREMATURELY GENERALIZE FOR H2
==================================================

This is important.

Hospital 2 will be different from Hospital 1.

Do NOT attempt to predict what H2 will need.

Do not create things like:

BaseHospitalAuditor
HospitalStrategy
HospitalFactory
AbstractContractParser
SemanticProviderInterface
GenericHospitalPipeline

unless the CURRENT H1 implementation genuinely requires them.

When H2 is implemented later, we will extract additional shared functionality only when we have two real implementations proving that it is shared.

Follow this principle:

    duplication once is sometimes better than the wrong abstraction

For now:

    H1-specific logic → hospital_1/
    obviously generic logic → shared/

Nothing more.

==================================================
TEST CONSOLIDATION
==================================================

The current test suite has too many small files.

Keep all meaningful coverage but consolidate related tests.

Instead of:

test_caps.py
test_bundles.py
test_discounts.py
test_exclusions.py
test_premiums.py

prefer:

tests/hospital_1/test_audit.py

containing:

def test_daily_cap(...):
def test_bundle(...):
def test_volume_discount(...):
def test_exclusion_window(...):
def test_weekend_premium(...):

Suggested final layout:

tests/hospital_1/
    test_contract.py
    test_matching.py
    test_audit.py
    test_evaluation.py

tests/shared/
    test_data.py
    test_money.py

Do not remove important assertions just to reduce file count.

==================================================
OUTPUTS AND ARTIFACTS
==================================================

Clean generated files too.

Use:

artifacts/
└── hospital_1/
    ├── service_matches.csv
    ├── split_manifest.json
    └── research/
        └── development-only experiment artifacts if still useful

outputs/
└── hospital_1/
    ├── predictions.csv
    ├── findings.csv
    ├── evaluation.md
    └── generalization_report.md if still valuable

prompts/
└── hospital_1/
    └── versioned prompts used during development

Do not leave large numbers of generated H1 files scattered around the root.

==================================================
GENERALIZATION / OVERFITTING EXPERIMENTS
==================================================

Hospital 1 accumulated extra machinery for investigating whether its strong score came from overfitting.

This included things such as:

- development split
- holdout split
- temporal evaluation
- ablations
- freeze manifests
- service-match audits
- generalization reports

These experiments were useful.

But they should not define the runtime architecture.

For each artifact/tool decide:

A. needed by the challenge
B. useful final evidence
C. development-only research

Keep A and useful B.

Move C to:

    artifacts/hospital_1/research/

or remove it if it is reproducible and adds no value.

Do not remove the final evidence showing:

- no label leakage
- deterministic pricing
- error analysis
- known limitations
- honest confidence handling

==================================================
MAIN ENTRY POINT
==================================================

Have one simple entry point.

For example:

    python -m src.main audit h1
    python -m src.main evaluate h1
    python -m src.main submission

Exact CLI syntax is flexible.

main.py should contain orchestration only.

Do not put H1 business logic in main.py.

==================================================
ASSESSMENT QUALITY MUST BE PRESERVED
==================================================

The purpose is NOT to make the solution simpler by removing quality.

Keep:

- deterministic financial calculations
- exact ROUND_HALF_UP behavior
- cross-invoice context
- service ambiguity handling
- no label leakage
- H1 evaluation
- per-category metrics
- systematic error analysis
- meaningful tests
- decision log
- prompt versioning
- reproducibility
- confidence/uncertainty handling
- runnable submission generation

The goal is:

    same strong solution
    cleaner architecture
    fewer unnecessary files

==================================================
REFACTOR PROCESS
==================================================

STEP 1

Inspect the full repository before editing.

Produce a concise map:

current file
→ responsibility
→ KEEP / MOVE / MERGE / DELETE
→ destination

STEP 2

Run the current tests and evaluation.

Record a baseline:

- test count
- pass/fail count
- H1 accuracy
- precision
- recall
- F1
- expected-total metrics
- number of generated predictions
- any other important current metrics

STEP 3

Create:

src/shared/
src/hospital_1/

STEP 4

Move generic code into shared/.

STEP 5

Move H1-specific code into hospital_1/.

STEP 6

Merge unnecessary small modules.

STEP 7

Consolidate tests.

STEP 8

Organize outputs/artifacts/prompts.

STEP 9

Update imports and CLI.

STEP 10

Run the complete test suite again.

STEP 11

Run H1 evaluation again.

The results must remain equivalent to baseline.

If results change, stop and investigate rather than assuming the refactor is correct.

STEP 12

Update README with:

- simple architecture explanation
- project tree
- commands
- where H1 lives
- where future hospitals will be added

==================================================
CLEAN CODE RULES
==================================================

Use:

- type hints
- dataclasses where useful
- descriptive functions
- clear contract-rule comments
- simple modules
- explicit code

Avoid:

- unnecessary base classes
- service/repository/controller architecture
- dependency injection frameworks
- factory classes
- interfaces with one implementation
- speculative abstractions for future hospitals
- files containing only a tiny wrapper around another function

==================================================
FINAL ARCHITECTURE PHILOSOPHY
==================================================

TODAY:

                    shared
                      |
                 hospital_1


LATER, once implemented:

                    shared
                      |
          +-----------+-----------+
          |           |           |
     hospital_1  hospital_2  hospital_3

Do not build the future hospitals today.

==================================================
FINAL REPORT
==================================================

After completing the refactor, show me:

1. old project tree
2. new project tree
3. KEEP / MOVE / MERGE / DELETE summary
4. files deleted
5. files merged
6. files moved
7. why every remaining source file exists
8. test results before vs after
9. H1 evaluation before vs after
10. any behavior changes, ideally none
11. anything you intentionally did not simplify and why

Most importantly:

A reviewer should be able to open:

    src/hospital_1/

and understand the Hospital 1 solution without jumping through ten unrelated modules.

Actually perform the refactor.
Do not only propose it.
