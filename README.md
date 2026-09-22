# Invoice audit — contract-driven, deterministic, per hospital

The exercise brief is in [`EXERCISE.md`](EXERCISE.md). This file describes the
submission.

## What is submitted

| hospital | status | in `outputs/submission.csv`? |
|---|---|---|
| Hospital 1 | implemented; the labelled **development** hospital, evaluated, **not scored** | no — it is development data |
| Hospital 2 | implemented; **submitted** (1,125 rows, 74 flagged) | yes — the only rows in the file |
| Hospitals 3–5 | **not implemented** — no code, no rows, no placeholder files; not used to produce any submitted prediction | no |

Deliverables, as the brief lists them:

1. **A runnable repository.** See [Install and test](#install-and-test) and
   [Reproduce](#reproduce). Dependencies are pinned in `requirements.txt`.
2. **`submission.csv`.** [`outputs/submission.csv`](outputs/submission.csv),
   in the template format. It holds scored hospitals only. The per-hospital
   file is [`outputs/hospital_2/submission.csv`](outputs/hospital_2/submission.csv).
3. **Evaluation report.**
   [`outputs/hospital_1/evaluation_report.md`](outputs/hospital_1/evaluation_report.md)
   has per-category performance on Hospital 1 and failures grouped by type.
4. **Prompts, versioned.** [`prompts/`](prompts/), indexed in
   [`prompts/README.md`](prompts/README.md).
5. **One-page decision log.** [`DECISION_LOG.md`](DECISION_LOG.md). The
   detailed per-hospital logs are
   [`outputs/hospital_1/decision_log.md`](outputs/hospital_1/decision_log.md) and
   [`outputs/hospital_2/decision_log.md`](outputs/hospital_2/decision_log.md).

## Why Hospital 1, then Hospital 2, then stop

- **Hospital 1 came first because it is the only labelled hospital.** Every
  design choice (conservative matching, a declined total rather than a guessed
  one, evidence-strength confidence) was developed and checked there.
- **Hospital 2 was the first scored hospital attempted.** Its contract is
  unlike Hospital 1's: 76 services, each described in one prose clause, with
  no tables. That tested whether the approach transfers.
- **Hospitals 3–5 were not implemented** and were not used to produce any
  submitted prediction. They were looked at only during planning.
- **Hospital 2 was stopped at a defined point.** See the
  [Stopping decision](outputs/hospital_2/decision_log.md#stopping-decision).
  Following the brief's emphasis on depth and stated uncertainty, a focused
  implementation of one scored hospital, with explicit uncertainty, was
  preferred over shallow coverage of all four scored hospitals.

## How it works

```
contract text ──► contract.py ──► rules (every rate, cap, discount, bundle, exclusion, with its clause)
invoice text  ──► matcher.py  ──► service identity: MATCHED / AMBIGUOUS / UNKNOWN (never uses price)
                  semantic.py ──► (H2 only) LLM classifier + Jev verifier for unclear descriptions
                  audit.py    ──► integer-cent pricing, findings, reconstructable total or blank, confidence
```

- **Python decides every number.** All money is integer cents through
  `Decimal` with `ROUND_HALF_UP` (`src/shared/money.py`). An LLM is used only
  for Hospital 2 service *identity*. It never sees a price, a total, an
  invoice or patient id, and it never calculates anything.
- **Identity comes from words, never from price.** The billed price is the
  thing under audit, so using it to identify a service would be circular.
  Tests pin the matcher signatures to prevent it.
- **Detection and reconstruction are separate questions.**
  `expected_total_cents` is filled only when the corrected total can be
  defended. Otherwise it is left blank, never the billed total, zero, or a
  cap-adjusted guess. For example, a daily-cap breach proves an invoice is
  wrong but does not reveal the delivered quantity, so its total is blank.
- **Unresolved identity is carried, not guessed.** An unclear line is audited
  as the set of services it could be. It is flagged only if it is wrong under
  every reading.

### Uncertainty

- `confidence` is an **evidence-strength score, not a calibrated
  probability**:
  - Hospital 1 uses 0.92 / 0.70 / 0.40;
  - Hospital 2 uses 0.85 / 0.65 / 0.40.

  It measures how the services were identified and whether the total can be
  reconstructed. Hospital 1 shows it is under-confident as a detection
  probability (the low band was right 19 of 19 times); see §4 of the
  evaluation report. Hospital 2 has no labels, so its calibration is unknown.
- `predictions.csv` (per hospital) adds the uncertainty columns:
  - `confidence_band`;
  - `pricing_complete` and `correction_reconstructable`;
  - `maximum_contractually_payable_total_cents`;
  - occurrence ids;
  - a plain-language `uncertainty_reasons`.

  Hospital 2 also has a diagnostic `provisional_expected_total_cents` that is
  never submitted.

## Results

### Hospital 1 (development, not scored)

Checked against labels, all 913 invoices:

- **Detection:** TP 58 / FP 0 / TN 855 / FN 0.
- **Corrected totals:**
  - 909 offered, 908 exact; the one miss is 43,650 cents off;
  - 4 declined as unknowable (daily-cap breaches).
- **Categories:** exact except for one invoice, which has a matcher defect.

**These are post-hoc numbers, not untouched validation.** The project owner
reports that the full labels were visible during development. Also, the
locked holdout (274 invoices) has been printed on every evaluation run since
the freeze. Details, the split and temporal results, and why earlier quoted
figures (99.56% / 333.5 cents MAE) differ are in the
[evaluation report](outputs/hospital_1/evaluation_report.md).

### Hospital 2 (submitted, no labels)

No accuracy is claimed, because none can be measured.

| | |
|---|---|
| invoice rows / flagged | 1,125 / 74 |
| `pricing_complete` / reconstructable | 238 / 237 |
| blank `expected_total_cents` | 888 |
| description clusters | 441: 358 matched from text, 14 UNKNOWN, 69 sent to the semantic stage |
| semantic stage | classification attempted for 69 clusters (75 OpenRouter HTTP attempts, with bounded retries): 67 valid (64 AMBIGUOUS, 3 MATCHED), 2 failed (HTTP 402). Jev accepted the 64 AMBIGUOUS, and none of the 3 MATCHED passed the gate |
| verified matches added by the semantic stage | **0** |
| lines audited as ambiguous / unknown | 1,709 / 14 (of 14,360) |

**The semantic stage did not increase verified service-mapping coverage.** The
submitted rows are byte-identical before and after it ran. Most totals are
blank because the invoice contains at least one line whose service the text
does not identify, and the audit declines to guess. That covers 866 of the
888 blank totals. See
[`outputs/hospital_2/audit_report.md`](outputs/hospital_2/audit_report.md) and
the [decision log](outputs/hospital_2/decision_log.md).

## Install and test

Python 3.11+ (developed on 3.13.2). The engine uses only the standard library.
`python-dotenv` loads an optional `.env`, and `pytest` runs the tests. Both
are pinned.

```bash
pip install -r requirements.txt
```

```bash
python -m pytest -q
```

There are 414 tests:

| area | tests |
|---|---|
| Hospital 1 | 154 |
| Hospital 2 | 209 |
| shared | 36 |
| setup and secrets | 8 |
| submission files | 7 |

After dependencies are installed, the tests and normal reproduction workflow
require no API keys or network access.

## Reproduce

Every command in this section is **offline**: no API key is needed and no
network call is made.

Reproduce the submission from the artifacts in the repository:

```bash
python -m src.main audit h2
```

```bash
python -m src.main submission
```

- `audit h2` reads the persisted identity decisions in
  `artifacts/hospital_2/service_mappings.json` and makes no API call.
- It writes `outputs/hospital_2/{predictions,findings,submission}.csv`,
  `audit_report.md` and `artifacts/hospital_2/pricing_traces.jsonl`.
- `submission` rewrites `outputs/hospital_2/submission.csv` and builds
  `outputs/submission.csv` by copying that file's rows unchanged. The two
  cannot disagree, and a test checks this.

Reproduce the Hospital 1 predictions and evaluation:

```bash
python -m src.main audit h1
```

```bash
python -m src.main evaluate h1
```

- `audit h1` writes `outputs/hospital_1/{predictions,findings}.csv`.
- `evaluate h1` reads labels (after prediction) and writes
  `outputs/hospital_1/evaluation.md`. It scores the holdout only while the
  prediction sources match `artifacts/hospital_1/freeze.json`.
- There is no Hospital 1 submission-format file, because Hospital 1 is not
  scored.

Inspect the Hospital 2 semantic state without calling anything:

```bash
python -m src.main semantic h2 status
```

Further offline commands:

- `semantic h2 prepare`, `rebuild`, `jev-export` and `jev-import`;
- `research h1 temporal|ablation` (reads labels; development evidence).

### Commands that call paid external APIs

These are **not** needed to reproduce the submission, and they were not rerun
for it:

| command | calls | key |
|---|---|---|
| `python -m src.main semantic h2 classify [--retry-failed]` | OpenRouter, `z-ai/glm-5.3-flash` | `OPENROUTER_API_KEY` |
| `python -m src.main semantic h2 jev` | TypeSafe SystemOne (Jev `jev-1.13.0`) | `TYPESAFE_API_KEY` (or `JEV_API_KEY`) |

- Rerunning them may change `service_mappings.json`, and so the Hospital 2
  outputs.
- A decision is kept only while its contract fingerprint, matcher version,
  prompt version (a hash of the prompt file) and candidate set are unchanged.
- The Jev Playground route (`jev-export`, then a manual Playground run, then
  `jev-import`) needs no key.

### Keys and `.env`

1. Copy the template:

   ```bash
   cp .env.example .env
   ```

2. Fill in the keys you need in `.env`.

How the file is handled:

- `src/main.py` loads the project-root `.env` once at startup, with
  python-dotenv, `override=False`. Real environment variables win.
- `.env` is git-ignored.
- `.env.example` holds only empty placeholders.
- A test fails if any committable file contains something shaped like a
  credential.

Optional settings (an empty value means the default):

| variable | default |
|---|---|
| `H2_CLASSIFIER_MODEL` | `z-ai/glm-5.3-flash` |
| `H2_CLASSIFIER_MAX_ATTEMPTS` | `3` |
| `H2_CLASSIFIER_MAX_TOKENS` | `4096` |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` |
| `JEV_API_URL` | `https://api.typesafe.ai/v1/systemone` |
| `JEV_MODEL` | `jev-1.13.0` |
| `JEV_THRESHOLD` | `0.90` |

## AI use (disclosure)

- **Code and documents.** They were written with Claude Code (Claude Opus 5),
  driven by the prompts in [`prompts/`](prompts/). The human author set the
  scope, the constraints and the stopping points; the assistant wrote the code,
  the tests and the first drafts of the reports.
  - Development prompts are saved verbatim. Several were saved only
    afterwards, recovered from the session transcript, and each file says so.
  - The prompts index lists them in order.
- **Runtime models.** Two are used, both for Hospital 2 service identity only:
  - the OpenRouter classifier (`prompts/hospital_2/001_service_classifier.md`);
  - the Jev verifier (`prompts/hospital_2/003_jev_verifier.md`).

  The code loads those prompt files directly, so the documented prompt is the
  executed prompt.
- **No LLM prices, totals or flags anything.**

## Where things live

```
src/main.py                 CLI (orchestration only)
src/submission.py           per-hospital submission files -> combined outputs/submission.csv
src/shared/                 data loading, money, result types, CSV writers (frozen with H1)
src/hospital_1/             contract, matcher, audit, evaluation (the only label reader)
src/hospital_2/             contract, matcher, semantic (identity only), audit
tests/                      shared/, hospital_1/, hospital_2/, test_setup.py, test_submission.py
outputs/submission.csv      the combined, scored submission (hospital_2 only)
outputs/hospital_1/         predictions, findings, evaluation.md, evaluation_report.md,
                            generalization_report.md, decision_log.md
outputs/hospital_2/         submission, predictions, findings, audit_report.md, decision_log.md
artifacts/hospital_1/       locked split + manifest, freeze record, match audit, research/
artifacts/hospital_2/       contract rules, clusters, service_mappings.json (every identity
                            decision with provenance), unresolved.json, Jev batch, pricing traces
prompts/                    versioned prompts; see prompts/README.md
DECISION_LOG.md             one-page decision log for the submission
EXERCISE.md                 the challenge brief (unchanged)
```

Hospital 1's prediction path (`src/shared/*`, `src/hospital_1/{contract,matcher,audit}.py`)
is hashed in `artifacts/hospital_1/freeze.json`. Editing any of those files
switches off the holdout score. Hospital 2 reuses `src/shared/` without
modifying it.

## Adding a hospital

- Each new hospital gets its own `src/hospital_N/` and `tests/hospital_N/`,
  written for *its* contract.
- To submit it:
  1. write `outputs/hospital_N/submission.csv` with
     `write_hospital_submission`;
  2. add `"hospital_N"` to `SCORED_HOSPITALS` in `src/submission.py`;
  3. pass its file to `combine_submissions` in `cmd_submission`.
- `combine_submissions` refuses unscored hospitals, missing files and
  duplicate invoice ids.
