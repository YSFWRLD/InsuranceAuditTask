# Invoice audit — contract-driven, deterministic, per hospital

The exercise brief is in [`EXERCISE.md`](EXERCISE.md). This file describes the
submission.

## What is submitted

| hospital | status | in `outputs/submission.csv`? |
|---|---|---|
| Hospital 1 | implemented; the labelled **development** hospital, evaluated, **not scored** | no — it is development data |
| Hospital 2 | implemented; **submitted** (1,125 rows, 74 flagged) | yes |
| Hospital 4 | implemented; **submitted** (835 rows, 63 flagged); fully deterministic, no LLM | yes |
| Hospital 5 | implemented; **submitted** (1,050 rows, 76 flagged, 939 totals proven); deterministic audit over Jev-reviewed vocabulary and identity decisions | yes |
| Hospital 3 | **not implemented** — no code, no rows, no placeholder files; not used to produce any submitted prediction | no |

Deliverables, as the brief lists them:

1. **A runnable repository.** See [Install and test](#install-and-test) and
   [Reproduce](#reproduce). Dependencies are pinned in `requirements.txt`.
2. **`submission.csv`.** [`outputs/submission.csv`](outputs/submission.csv),
   in the template format. It holds scored hospitals only: 3,010 rows. The
   per-hospital files are
   [`outputs/hospital_2/submission.csv`](outputs/hospital_2/submission.csv),
   [`outputs/hospital_4/submission.csv`](outputs/hospital_4/submission.csv) and
   [`outputs/hospital_5/submission.csv`](outputs/hospital_5/submission.csv).
3. **Evaluation report.**
   [`outputs/hospital_1/evaluation_report.md`](outputs/hospital_1/evaluation_report.md)
   has per-category performance on Hospital 1 and failures grouped by type.
4. **Prompts, versioned.** [`prompts/`](prompts/), indexed in
   [`prompts/README.md`](prompts/README.md).
5. **One-page decision log.** [`DECISION_LOG.md`](DECISION_LOG.md). The
   detailed per-hospital logs are
   [`outputs/hospital_1/decision_log.md`](outputs/hospital_1/decision_log.md),
   [`outputs/hospital_2/decision_log.md`](outputs/hospital_2/decision_log.md),
   [`outputs/hospital_4/decision_log.md`](outputs/hospital_4/decision_log.md) and
   [`outputs/hospital_5/decision_log.md`](outputs/hospital_5/decision_log.md).

## Sequencing: Hospital 1, then 2, then 4, then 5

- **Hospital 1 came first because it is the only labelled hospital.** Every
  design choice (conservative matching, a declined total rather than a guessed
  one, evidence-strength confidence) was developed and checked there.
- **Hospital 2 was the first scored hospital attempted.** Its contract is
  unlike Hospital 1's: 76 services, each described in one prose clause, with
  no tables. That tested whether the approach transfers.
- **Hospital 4 was added next.** Its contract is table-driven, like Hospital
  1's, with the same rule families. It is built as a deterministic engine that
  combines Hospital 1's pricing with Hospital 2's conservative identity rules.
- **Hospital 5 was added last.** It was designed to attack the weakness of
  Hospitals 2 and 4: descriptions the text alone cannot identify, which left
  most totals blank. With 939 of 1,050 totals proven, against 307 of 835 for
  H4, it covers far more, but 111 totals remain blank. Jev reviews candidate vocabulary and missing-word
  identity as bounded choices. Every number is still Python.
- **Hospital 3 was not implemented** and was not used to produce any
  submitted prediction. It was looked at only during planning.
- **Hospital 2 was stopped at a defined point.** See the
  [Stopping decision](outputs/hospital_2/decision_log.md#stopping-decision).
  Following the brief's emphasis on depth and stated uncertainty, focused
  implementations of two scored hospitals, with explicit uncertainty, were
  preferred over shallow coverage of all four.

## How it works

```
contract text ──► contract.py ──► rules (every rate, cap, discount, bundle, exclusion, with its clause)
invoice text  ──► matcher.py  ──► service identity: MATCHED / AMBIGUOUS / UNKNOWN (never uses price)
                  semantic.py ──► H2: LLM classifier + Jev verifier for unclear descriptions
                                  H5: Jev reviews proposed vocabulary and missing-word identity
                  audit.py    ──► integer-cent pricing, findings, reconstructable total or blank, confidence
```

- **Python decides every number.** All money is integer cents through
  `Decimal` with `ROUND_HALF_UP` (`src/shared/money.py`). A model is used only
  for service *identity*: Hospital 2's classifier and Jev verifier, and
  Hospital 5's Jev reviews. It never sees a price, a total, an invoice or
  patient id, and it never calculates anything.
- **Identity comes from words, never from price.** The billed price is the
  thing under audit, so using it to identify a service would be circular.
  Tests pin the matcher signatures to prevent it.
- **Detection and reconstruction are separate questions.**
  `expected_total_cents` is filled only when the corrected total can be
  defended. Otherwise it is left blank, never the billed total, zero, or a
  cap-adjusted guess. For example, in Hospitals 1, 2 and 5 a daily-cap breach
  proves an invoice is wrong but does not reveal the delivered quantity, so
  its total is blank. Hospital 4's contract instead says the excess "is not
  payable", so there the limit is priced. That is the chosen H4 policy; the
  alternative remains documented as an acknowledged ambiguity (see its
  decision log).
- **Unresolved identity is carried, not guessed.** An unclear line is audited
  as the set of services it could be. It is flagged only if it is wrong under
  every reading.

### Uncertainty

- `confidence` is an **evidence-strength score, not a calibrated
  probability**:
  - Hospital 1 uses 0.92 / 0.70 / 0.40;
  - Hospitals 2, 4 and 5 use 0.85 / 0.65 / 0.40.

  It measures how the services were identified and whether the total can be
  reconstructed. Hospital 1 shows it is under-confident as a detection
  probability (the low band was right 19 of 19 times); see §4 of the
  evaluation report. Hospitals 2, 4 and 5 have no labels, so their calibration
  is unknown. Jev's probabilities are evidence about one description, not this
  score.
- `predictions.csv` (per hospital) adds the uncertainty columns:
  - `confidence_band`;
  - `pricing_complete` and `correction_reconstructable`;
  - `maximum_contractually_payable_total_cents`;
  - occurrence ids;
  - a plain-language `uncertainty_reasons`.

  Hospitals 2, 4 and 5 also have a diagnostic `provisional_expected_total_cents`
  that is never submitted. Hospital 5 adds per-invoice line counts by financial
  status and the reasons for any blank total.

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

### Hospital 4 (submitted, no labels)

No accuracy is claimed. No model or external API is used.

| | |
|---|---|
| invoice rows / flagged | 835 / 63 (840 physical records, 5 reused numbers) |
| `pricing_complete` / reconstructable | 308 / 307 |
| blank `expected_total_cents` | 528 |
| contract rules parsed | 98 services, 18 premiums, 18 daily limits, 7 bundles, 3 discount services (4 tiers), 15 exclusions, no weekend uplift |
| description clusters | 211: 157 MATCHED, 41 AMBIGUOUS, 13 UNKNOWN |
| lines | 10,560: 9,725 identified (147 of them via the unit-basis tie-break), 822 ambiguous, 13 unknown |

- **Why most totals are blank.** A description that omits its qualifier,
  specialty or concept stays AMBIGUOUS even when only one contracted service
  fits, because the text cannot rule out an uncontracted look-alike. 523
  invoices carry such a line.
- **Where to look.**
  [`unresolved.json`](artifacts/hospital_4/unresolved.json) ranks those
  clusters by impact.
  [`audit_report.md`](outputs/hospital_4/audit_report.md) explains every
  flagged invoice line by line.
  [`decision_log.md`](outputs/hospital_4/decision_log.md) separates contract
  rules, implementation policies and unresolved ambiguities.

### Hospital 5 (submitted, no labels)

No accuracy is claimed. The audit calls nothing; it reads committed Jev
reviews, checked against the requests the current code would send.

| | |
|---|---|
| invoice rows / flagged | 1,050 / 76 (1,057 physical records, 7 reused numbers) |
| `pricing_complete` / reconstructable | 945 / 939 |
| blank `expected_total_cents` | 111, by primary reason: 90 a line's service not settled, 4 a discount position that depends on such lines, 11 unknown services, 6 daily-cap breaches |
| contract rules parsed | 84 services, 252 facility and 252 plan-tier multipliers, 9 daily caps, 10 premiums, 9 weekend uplifts, 3 bundles, 9 discount services (15 tiers), 7 exclusions |
| descriptions | 474 raw, 180 identity clusters |
| lines | 13,221: 13,113 identified, 97 ambiguous, 11 unknown |
| lines identified by | text 3,817; text with context-resolved tokens 8,050; unit-basis tie-break 301 (+68 after review); Jev missing-word choice 877 |
| Jev normalisation review | 266 proposals, one question each → gated 62 global / 45 context-only / 159 rejected (2 of the decisions are recorded human-review overrides) |
| Jev missing-word review | 33 clusters (1,042 lines) → 28 services, 1 closed to its two candidates, 4 left unresolved |

How the pipeline gets there, stage by stage on the same data
([`semantic_contribution.md`](outputs/hospital_5/semantic_contribution.md)):

| stage | identified lines | totals proven |
|---|---|---|
| exact contract words only | 0 | 0 |
| + Jev-reviewed global vocabulary | 4,002 | 0 |
| + context-dependent vocabulary, resolved by structure | 12,168 | 385 |
| + Jev missing-word decisions | 13,113 | 936 |
| + financial equivalence (final) | 13,113 | 939 |

- **Financial equivalence** evaluates every still-possible reading of a line:
  candidate service, bundle partner, threshold side, discount tier, repeat,
  exclusion, and the days a malformed date can stand for. It blanks a total
  only when one of those readings changes the amount. On this contract it
  added 3 totals: surviving candidate services never priced identically.
- **A pre-commit review removed a single-candidate closure gate.** It had been
  added after the first Jev run and lifted proven totals from 706 to 1,032.
  In four clusters, Jev chose the only candidate at 0.80-0.89 and put the
  rest on "ambiguous". The gate counted that hedge as confirmation. The
  clusters are structurally the same as the 30 Jev resolved above the
  pre-declared 0.90 bar, so no principled rule separates them.
- **Of those four, only `SUPV ENT SPCM ANLY` carries its discriminator.**
  ENT is the standard abbreviation for otolaryngology, in the specialty slot.
  A human-reviewed, context-only reading `ent → otolaryngologic` resolves it
  deterministically. It is recorded with provenance in
  `artifacts/hospital_5/human_review_overrides.json` and recovers 233 totals
  (706 → 939). The other three genuinely omit a word and stay unresolved. See
  sections G and H of the [decision log](outputs/hospital_5/decision_log.md).
- **Readings chosen where the H5 wording is open.** Each rests on the H5
  text alone and has a switch:
  - a threshold-crossing line takes the discount tier already exceeded
    before it (3.1: one unit rate per line);
  - a bare "Daily cap" breach is flagged and its total left blank (the
    agreement does not say what a breach does);
  - "not billable within N days of" is a distance, so both sides of the
    trigger count, day N included. All 6 exclusion findings depend on this;
  - the facility is taken from the invoice header, because line items carry
    none (a schema interpretation).

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

There are 711 tests:

| area | tests |
|---|---|
| Hospital 1 | 154 |
| Hospital 2 | 209 |
| Hospital 4 | 106 |
| Hospital 5 | 190 |
| shared | 36 |
| setup and secrets | 8 |
| submission files | 8 |

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
python -m src.main audit h4
```

```bash
python -m src.main audit h5
```

```bash
python -m src.main submission
```

- `audit h2` reads the persisted identity decisions in
  `artifacts/hospital_2/service_mappings.json` and makes no API call.
- It writes `outputs/hospital_2/{predictions,findings,submission}.csv`,
  `audit_report.md` and `artifacts/hospital_2/pricing_traces.jsonl`.
- `audit h4` parses the contract and matches every description
  deterministically. It writes everything under `artifacts/hospital_4/`
  (contract rules, description clusters, service mappings, unresolved
  clusters, one pricing trace per line) and
  `outputs/hospital_4/{predictions,findings,submission}.csv` plus
  `audit_report.md`.
- `audit h5` rebuilds the lexicon and every Jev request from the contract,
  the corpus and the prompt templates. It checks each stored review in
  `artifacts/hospital_5/jev_*_reviews.jsonl` against the exact request body
  and input fingerprint, and **refuses to run if any is missing or stale**.
  It then writes everything under `artifacts/hospital_5/` and
  `outputs/hospital_5/`:
  - the contract rules and the proposals;
  - the gated vocabulary and the missing-word decisions;
  - the clusters, mappings and unresolved list;
  - a pricing trace for every line;
  - coverage and blank reasons;
  - `{predictions,findings,submission}.csv`, `audit_report.md` and
    `semantic_contribution.md`.
- `submission` rewrites the three hospital files and builds
  `outputs/submission.csv` by copying their rows unchanged: Hospital 2, then 4,
  then 5. A hospital file and its rows in the combined file cannot disagree,
  and a test checks this.

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
| `python -m src.main semantic h5 normalize-run` | Jev: one question per normalisation proposal (266) | `TYPESAFE_API_KEY` (or `JEV_API_KEY`) |
| `python -m src.main semantic h5 missing-word-run` | Jev: one question per unresolved cluster (35) | `TYPESAFE_API_KEY` (or `JEV_API_KEY`) |
| `python -m src.main semantic h5 probe` | Jev: the constructed stress test (1) | `TYPESAFE_API_KEY` (or `JEV_API_KEY`) |

- Rerunning them may change `service_mappings.json`, and so the Hospital 2
  outputs.
- A decision is kept only while its contract fingerprint, matcher version,
  prompt version (a hash of the prompt file) and candidate set are unchanged.
- The Jev Playground route needs no key. For Hospital 2 it is `jev-export`,
  then a manual Playground run, then `jev-import`. For Hospital 5 it is
  `normalize-export` / `normalize-import` and `missing-word-export` /
  `missing-word-import`.
- Hospital 5 reviews are stored per request with the request, state and
  question hashes. A run asks only the requests without a current review, so
  rerunning on unchanged inputs calls nothing. `semantic h5 status` shows
  coverage and staleness offline.
- Missing-word questions are built from the reviewed vocabulary, so they
  cannot be asked until every normalisation review is current.

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
| `JEV_THRESHOLD` | `0.90` (Hospital 2 only) |
| `H5_JEV_GLOBAL_MIN_PROBABILITY` / `_CONFIDENCE` | `0.90` / `0.85` |
| `H5_JEV_CONTEXT_MIN_PROBABILITY` | `0.80` |
| `H5_JEV_SERVICE_MIN_PROBABILITY` / `_CONFIDENCE` | `0.90` / `0.80` |
| `H5_JEV_CLOSURE_MIN_PROBABILITY` | `0.90` |
| `H5_JEV_UNKNOWN_MIN_PROBABILITY` | `0.90` |
| `H5_JEV_MAX_CANDIDATES` | `8` |
| `H5_JEV_WORKERS` / `H5_JEV_MAX_ATTEMPTS` / `H5_JEV_TIMEOUT` | `4` / `3` / `60` |

Changing an H5 gate changes which stored reviews are accepted, not the
reviews themselves. Changing `JEV_MODEL` or a Hospital 5 prompt template makes
every stored review stale, and the audit then refuses to run.

## AI use (disclosure)

- **Code and documents.** They were written with Claude Code (Claude Opus 5;
  Hospitals 4 and 5 with Claude Opus 5.5),
  driven by the prompts in [`prompts/`](prompts/). The human author set the
  scope, the constraints and the stopping points; the assistant wrote the code,
  the tests and the first drafts of the reports.
  - Development prompts are saved verbatim. Several were saved only
    afterwards, recovered from the session transcript, and each file says so.
  - The prompts index lists them in order.
- **Runtime models.** They are used for service identity only. Hospital 4
  uses none:
  - Hospital 2: the OpenRouter classifier
    (`prompts/hospital_2/001_service_classifier.md`) and the Jev verifier
    (`prompts/hospital_2/003_jev_verifier.md`);
  - Hospital 5: Jev only. It reviews normalisation proposals
    (`prompts/hospital_5/001_jev_normalization_review.md`) and missing-word
    identity (`prompts/hospital_5/002_jev_missing_word_resolution.md`). No
    general LLM is used.

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
src/hospital_4/             contract, matcher, audit (deterministic; no semantic stage)
src/hospital_5/             contract, normalization (proposals + lexicon), matcher, semantic
                            (Jev requests, gates, review store), workflow, audit, report
tests/                      shared/, hospital_1/, hospital_2/, hospital_4/, hospital_5/,
                            test_setup.py, test_submission.py
outputs/submission.csv      the combined, scored submission (hospital_2, hospital_4, hospital_5)
outputs/hospital_1/         predictions, findings, evaluation.md, evaluation_report.md,
                            generalization_report.md, decision_log.md
outputs/hospital_2/         submission, predictions, findings, audit_report.md, decision_log.md
outputs/hospital_4/         submission, predictions, findings, audit_report.md, decision_log.md
outputs/hospital_5/         submission, predictions, findings, audit_report.md,
                            semantic_contribution.md, decision_log.md
artifacts/hospital_1/       locked split + manifest, freeze record, match audit, research/
artifacts/hospital_2/       contract rules, clusters, service_mappings.json (every identity
                            decision with provenance), unresolved.json, Jev batch, pricing traces
artifacts/hospital_4/       contract rules, description clusters, service mappings,
                            unresolved.json (ranked by impact), pricing traces (every line)
artifacts/hospital_5/       contract rules; normalisation proposals; stored Jev reviews
                            (normalisation, missing-word, probe); gated vocabulary; missing-word
                            decisions; clusters, mappings, unresolved; pricing traces; coverage,
                            blank reasons, semantic contribution
prompts/                    versioned prompts; see prompts/README.md
DECISION_LOG.md             one-page decision log for the submission
EXERCISE.md                 the challenge brief (unchanged)
```

Hospital 1's prediction path (`src/shared/*`, `src/hospital_1/{contract,matcher,audit}.py`)
is hashed in `artifacts/hospital_1/freeze.json`. Editing any of those files
switches off the holdout score. Hospitals 2, 4 and 5 reuse `src/shared/`
without modifying it.

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
