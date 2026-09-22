# Invoice audit — contract-driven, deterministic, per hospital

The exercise brief is in [`EXERCISE.md`](EXERCISE.md). This file describes the
solution.

**Status:** Hospitals 1 and 2 are implemented. Hospitals 3–5 are not yet
implemented, and no production audit code exists for them. The Hospital 1
prediction path does not read or depend on data from any other hospital.

- **Hospital 1** is the labelled development hospital, evaluated on a locked
  holdout.
- **Hospital 2** has no labels. Its semantic stage (an LLM classifier plus the
  Jev verifier) has **not yet been run**: no API credentials are configured.
  Of 441 description clusters, 69 therefore await semantic review. Those 69
  cover 2,093 of the 14,360 invoice lines, and 1,709 of those lines are
  currently audited as unresolved (see [Hospital 2](#hospital-2)).

## Where things live

The repository is organised **by hospital**. Everything that makes the
Hospital 1 audit what it is lives in `src/hospital_1/`. `src/shared/` holds
only what does not depend on any contract.

```
src/
├── main.py                  CLI. Orchestration only -- no audit logic.
├── shared/                  hospital-independent
│   ├── models.py            invoice schema, match outcome, audit result
│   ├── data.py              JSONL -> physical invoice occurrences; CSV cross-check
│   ├── money.py             integer cents, Decimal, ROUND_HALF_UP
│   └── submission.py        audit results -> submission / predictions / findings CSV
├── hospital_1/              the Hospital 1 solution
│   ├── contract.py          the Agreement -> ContractRules; fails loudly
│   ├── matcher.py           description -> MATCHED / AMBIGUOUS / UNKNOWN
│   ├── audit.py             context, pricing, checks, reconstruction, confidence
│   └── evaluation.py        labels, locked split, metrics, freeze, research
│                            (the ONLY module that reads labels)
└── hospital_2/              the Hospital 2 solution (no labels exist)
    ├── contract.py          76 prose service clauses -> rules; fails loudly
    ├── matcher.py           deterministic identity + bounded candidates
    ├── semantic.py          classifier + Jev verifier + persisted mappings
    │                        (identity only -- never prices anything)
    └── audit.py             global context, pricing trace, findings, confidence

tests/
├── shared/                  test_data.py, test_money.py
├── hospital_1/              conftest.py (a synthetic contract), test_contract.py,
│                            test_matching.py, test_audit.py, test_evaluation.py
└── hospital_2/              conftest.py (synthetic invoices, real contract),
                             test_contract.py, test_matching.py,
                             test_semantic.py, test_audit.py

outputs/                     deliverables
├── submission.csv           template format -- scored hospitals only (hospital_2)
├── hospital_1/
│   ├── predictions.csv      submission columns + uncertainty columns
│   ├── findings.csv         one row per finding, with its line
│   ├── evaluation.md        dev + locked-holdout scores
│   └── generalization_report.md   the honest write-up
└── hospital_2/
    ├── predictions.csv      one row per invoice number; occurrence ids kept
    ├── findings.csv         per occurrence and line, with evidence basis + clause
    ├── audit_report.md      what was found -- no accuracy claimed
    └── decision_log.md      every Hospital 2 reading and open question

artifacts/hospital_1/        evidence behind the outputs
├── split/                   dev_labels.csv, holdout_labels.csv (70/30, fixed seed)
├── split_manifest.json      how the split was drawn, and when
├── freeze.json              prediction-source hashes + predictions fingerprint
├── service_matches.csv      every distinct description and how it was matched
└── research/                temporal_evaluation.md, ablation.md (development only)

artifacts/hospital_2/
├── contract_rules.json      every parsed rule + source text + contract fingerprint
├── description_clusters.json  506 raw descriptions -> 441 normalised clusters
├── service_mappings.json    one identity decision per cluster, with provenance
├── unresolved.json          clusters without a verified single identity
├── jev_state.json, jev_questions.json   Jev Playground batch (+ jev_results.json)
└── pricing_traces.jsonl     stage-by-stage price of every line on a flagged invoice

prompts/hospital_1/          the prompts that produced Hospital 1, versioned
prompts/hospital_2/          the H2 task prompt, and the classifier and Jev
                             prompts the code sends (loaded from these files)
DECISION_LOG.md              Hospital 1 readings of the contract, and why
```

### Reading `src/hospital_1/` in order

1. **`contract.py`** — every rate, cap, threshold, uplift, discount, bundle
   and exclusion window, parsed out of the Agreement with the clause it came
   from. Nothing in the audit hardcodes a contract term.
2. **`matcher.py`** — turns free text like `Procedure Immun Endosc /NG-7220`
   into a contracted service, or says it cannot. Never looks at price.
3. **`audit.py`** — the audit, in seven numbered sections: inputs, global
   context, pricing (clause 3.2 in order), stateless checks, the auditor,
   confidence, and the pipeline that wires them. Detection ("is it wrong?")
   and reconstruction ("what should it cost?") are answered separately.
4. **`evaluation.py`** — how the audit is measured, and the one place labels
   are read.

The dependency runs one way: `evaluation.py` imports the prediction path;
nothing on the prediction path imports `evaluation.py`.
`tests/hospital_1/test_evaluation.py` checks that mechanically, including by
hiding every label file and asserting that not one prediction changes.

## Running it

Python 3.11+. The audit engine uses only the standard library. `python-dotenv`
loads an optional `.env` at startup, and pytest is for tests.

```bash
pip install -r requirements.txt
```

```bash
python -m pytest tests -q
```

The suite has 325 tests: 190 for Hospital 1 and shared code, 135 for Hospital 2.

```bash
python -m src.main audit h1
```

```bash
python -m src.main evaluate h1
```

```bash
python -m src.main audit h2
```

```bash
python -m src.main submission
```

`audit h1` writes `outputs/hospital_1/{predictions,findings}.csv` and
`artifacts/hospital_1/service_matches.csv`. `evaluate` writes
`outputs/hospital_1/evaluation.md`. `audit h2` writes `outputs/hospital_2/` (see
[Hospital 2](#hospital-2)). `submission` writes `outputs/submission.csv` in the
template's format. It contains **only scored hospitals**, so today that is
Hospital 2's 1,125 rows. Hospital 1 is the labelled development hospital, is
not scored, and is left out.

Development evidence. These commands read labels and are not needed to
produce predictions:

```bash
python -m src.main research h1 temporal
```

```bash
python -m src.main research h1 ablation
```

```bash
python -m src.main freeze h1 --reason "why the prediction path changed"
```

`split h1` exists but refuses to run: the split is locked, and its manifest's
creation time is evidence that it was drawn before development began.

### The holdout gate

`evaluate h1` scores the 30% holdout **only while the prediction sources are
byte-identical to `artifacts/hospital_1/freeze.json`**. Edit any file on the
prediction path and the report says the holdout is not evaluated, instead of
printing a number that has stopped being an unseen measurement. Re-freezing
requires a stated reason and keeps the earlier freeze in the record's history.

## Hospital 1 in one table

| | development (639) | locked holdout (274) |
|---|---|---|
| detection precision / recall | 1.000 / 1.000 | 1.000 / 1.000 |
| TP / FP / TN / FN | 46 / 0 / 593 / 0 | 12 / 0 / 262 / 0 |
| exact corrected total, where offered | 636 / 636 | 272 / 273 |
| corrected total declined as unknowable | 3 | 1 |

That holdout row contains one real failure: a matcher defect, analysed in §7
of the generalization report and deliberately left unfixed. Fixing it after
reading the holdout would spend the only unseen measurement. The per-category
tables are in `outputs/hospital_1/evaluation.md`; most categories have
single-digit support and are marked as such.

## Hospital 2

Hospital 2 has no labels, so nothing about it is reported as an accuracy. Its
contract has no tables: 76 services are each described in one prose clause,
scattered across 13 Articles between pages of boilerplate.

### Who decides what

| stage | decided by | where |
|---|---|---|
| contract rules: rates, bases, uplifts, discounts, caps, bundles, exclusions | Python, parsed from the contract text | `contract.py` |
| service identity, when the text is clear | Python, deterministic | `matcher.py` |
| service identity, when it is not | LLM classifier, then Jev verifier, then a Python gate | `semantic.py` |
| every price, total, threshold, finding and confidence | Python | `audit.py` |

The LLM decides **identity only**. It never sees a billed price, a total, an
invoice id or a patient id, and it never calculates anything. The classifier
and Jev prompts live in `prompts/hospital_2/`, and the code loads and sends
them from there, so the documented prompt is the executed prompt. Their
content hash is recorded as the prompt version on every decision.

### Configuration

**No API key is needed to use this project.** Without any key, all of these
work:

- the tests;
- the Hospital 1 audit and evaluation;
- the Hospital 2 deterministic audit and the submission;
- `semantic h2 prepare` and `status`;
- the Jev Playground export and import.

Two keys matter only for the two semantic steps that call an external API:

| variable | required only for |
|---|---|
| `OPENROUTER_API_KEY` | `python -m src.main semantic h2 classify` |
| `TYPESAFE_API_KEY` | `python -m src.main semantic h2 jev` (direct Jev verification via TypeSafe SystemOne) |

Optional:

| variable | default | purpose |
|---|---|---|
| `JEV_API_KEY` | none | fallback Jev key, used only if `TYPESAFE_API_KEY` is not set |
| `JEV_API_URL` | `https://api.typesafe.ai/v1/systemone` | override the Jev endpoint. **Normally leave unset** |
| `JEV_MODEL` | `jev-1.13.0` | Jev model, recorded with every verdict (`JEV_VERSION` also accepted) |
| `JEV_THRESHOLD` | `0.90` | the acceptance gate |
| `H2_CLASSIFIER_MODEL` | `z-ai/glm-5.3-flash` | OpenRouter model |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | |
| `H2_CLASSIFIER_MAX_ATTEMPTS` | `3` | bounded retries on invalid output |

An empty value means "use the default".

#### Setting the keys with `.env`

On startup, `src/main.py` loads a project-root `.env` file once. The file is
optional, and variables already set in the OS environment take precedence over
it.

1. Copy the template:

   ```bash
   cp .env.example .env
   ```

2. In `.env`, fill in `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY`.

3. Run the semantic steps, then the audit and submission:

   ```bash
   python -m src.main semantic h2 classify
   ```

   ```bash
   python -m src.main semantic h2 jev
   ```

   ```bash
   python -m src.main audit h2
   ```

   ```bash
   python -m src.main submission
   ```

- `.env` is local and ignored by Git. Never commit it.
- `.env.example` contains no secrets and is committed.
- API keys are not required for the deterministic audits or the tests.

Plain `export OPENROUTER_API_KEY=...` / `export TYPESAFE_API_KEY=...` works too.

```bash
python -m src.main semantic h2 status
```

`status` shows what is configured (never the credentials) and every count.

### Generating service mappings (deliberate, never on every audit)

```bash
python -m src.main semantic h2 prepare
```

`prepare` writes `contract_rules.json`, `description_clusters.json`,
`service_mappings.json` and `unresolved.json`. Earlier semantic decisions are
kept only if the contract fingerprint, the matcher version, the prompt version
and the cluster's candidate set are all unchanged; otherwise they are
discarded, and the reason is recorded.

```bash
python -m src.main semantic h2 classify
```

`classify` sends each semantic-pending **cluster** once. The classifier sees a
cluster's descriptions, never an individual line. Today that is 69 calls, and
those 69 clusters cover 2,093 line occurrences. Output must match a strict
JSON schema. Invalid output is retried a bounded number of times and then
recorded as a failure, never scraped or guessed.

### Clusters and line occurrences are different units

A **cluster** is one normalised description; a **line occurrence** is one
invoice line. Reports name each count explicitly. Current values:

| count | value | meaning |
|---|---|---|
| `total_normalized_clusters` | 441 | distinct descriptions after `/SA-####` is stripped |
| `deterministic_matched_clusters` | 358 | identified from text alone |
| `deterministic_unknown_clusters` | 14 | a recognised word contradicts every candidate. Decided from text; **not** semantic work |
| `semantic_pending_clusters` | 69 | sent to the classifier + Jev, not yet verified |
| `semantic_pending_line_occurrences` | 2,093 | invoice lines carrying those 69 descriptions |
| `unit_basis_provisional_line_occurrences` | 384 | of those lines, ones the billed basis resolved for now (a verified semantic decision would override it) |
| `currently_ambiguous_line_occurrences` | 1,709 | lines the audit carries as a set of possible services (2,093 − 384) |
| `total_unresolved_or_unknown_clusters` | 83 | everything in `unresolved.json`: 69 pending + 14 deterministic UNKNOWN, in separate groups |

### Jev verification

Jev judges each classifier decision and returns P(ACCEPT), P(REJECT) and
P(UNCERTAIN). Both modes below produce the same internal verdict,
`{"choice", "probabilities", "confidence", "model"}`, and both pass through
one validator and one gate:

| Jev verdict | effect |
|---|---|
| P(ACCEPT) ≥ 0.90 | the decision stands **with its own status** |
| P(REJECT) ≥ 0.90 | rejected; the cluster stays semantic-pending |
| anything else | semantic-pending |

An accepted AMBIGUOUS stays AMBIGUOUS, and an accepted UNKNOWN stays UNKNOWN.

**Direct API mode**, once `TYPESAFE_API_KEY` is set (no URL needed):

```bash
python -m src.main semantic h2 jev
```

This sends `POST https://api.typesafe.ai/v1/systemone` with
`Authorization: Bearer $TYPESAFE_API_KEY` and a body of
`{"state", "model": "jev-1.13.0", "questions"}`, the same state and questions
the Playground export writes. It then applies the per-question Choice results
(`choice`, `probabilities`, `confidence`). The transport is one isolated
function, `semantic.jev_http_transport`. Its reply goes through the same
validator and gate as a Playground import.

**What Jev sees.** Both modes build each case with one function, so the
Playground and the API always show Jev identical evidence:

- the descriptions;
- the candidate services, with clause ids, contractual unit bases and redacted
  contract text;
- the classifier's status, selected service, confidence and reason.

Jev never receives a price, a line, invoice or expected total, an invoice or
patient id, or any rate-derived hint.

The billed unit basis is withheld (`"identity_evidence": {"used_unit_basis_for_identity": false}`)
**unless** the decision being verified actually consumed it as a tie-break. In
that case, and only then, the case states
`{"used_unit_basis_for_identity": true, "billed_unit_basis": "..."}`, so Jev
sees all the evidence behind the decision.

The current classifier never receives the billed basis, so every case today
carries `false`. The deterministic per-line tie-break in the audit is not a
semantic decision and is not sent to Jev. A basis consumed for identity can
never also support `wrong_unit_basis`.

**Playground mode**, always available:

```bash
python -m src.main semantic h2 jev-export
```

This writes two files:

- `artifacts/hospital_2/jev_state.json`: `{"cases": {"h2_<cluster_id>": {...}}}`.
  Each case holds the description, its candidates (clause, contractual unit
  basis, redacted contract text) and the classifier's decision.
- `artifacts/hospital_2/jev_questions.json`: one question per case, keyed
  `verify_h2_<cluster_id>`, in the Playground's
  `{"type": "choice", "instructions": ..., "criteria": {"ACCEPT", "REJECT", "UNCERTAIN"}}`
  format. The question text is loaded from `prompts/hospital_2/003_jev_verifier.md`.

Run the batch in the Playground. Then copy each question's choice and three
probabilities into `artifacts/hospital_2/jev_results.json`:
`{"model": "jev-1.13.0", "results": {"verify_h2_<cluster_id>": {"choice": "ACCEPT", "probabilities": {"ACCEPT": 0.95, "REJECT": 0.03, "UNCERTAIN": 0.02}}}}`.
This is the project's own input format; the Playground's native export format
is not documented here.

```bash
python -m src.main semantic h2 jev-import
```

Import is all-or-nothing. It refuses, listing every problem:

- unknown or non-exported question ids;
- a choice other than ACCEPT, REJECT or UNCERTAIN;
- a probability that is missing, non-numeric or outside [0, 1];
- probabilities that do not sum to 1;
- a choice that is not the most probable outcome;
- a different model;
- any verdict issued for a classifier answer that has since changed.

### The complete flow, once both keys exist

With `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY` exported (`JEV_API_URL`
normally unset):

```bash
python -m src.main semantic h2 classify
```

```bash
python -m src.main semantic h2 jev
```

```bash
python -m src.main audit h2
```

```bash
python -m src.main submission
```

### Running fully offline

Once `service_mappings.json` exists, `audit h2` and `submission` read it and
call nothing (a test disables all network access and runs the audit).
Unresolved clusters are not skipped: each such line is carried as the set of
services it could be, through cumulative utilisation, Service Day aggregates,
bundles and exclusion history. It is flagged only if its price is wrong under
every reading.

### Expected totals are submitted only when defensible

A number in `expected_total_cents` claims to know what the invoice should have
totalled. So it is filled **only** when `correction_reconstructable` is true,
and left as an empty field otherwise: never the billed total, zero, or a
partial or cap-adjusted guess. The three states are:

| state | meaning |
|---|---|
| `pricing_complete` | every line of the invoice was priced exactly, from resolved identities and determinate inputs |
| `correction_reconstructable` | that exact total is also defensible: no cap breach, malformed date or unknown service blocks it |
| `expected_total_cents` | filled if and only if reconstructable |

`outputs/hospital_2/predictions.csv` also carries
`provisional_expected_total_cents`. This is a diagnostic (for example, an
unresolved line priced at the reading its billed rate matches), kept in its own
column so it cannot be mistaken for a corrected total. It never reaches the
submission.

Reconstructability updates automatically. Once the semantic stage verifies a
mapping, the next `audit h2` prices those lines exactly, and every invoice with
no other blocker gets its expected total. No manual step is needed.

### Reused invoice numbers

Seven invoice numbers are each used by two physical invoices. Internally every
occurrence is audited separately. The submission has one row per number, and
that row represents the **later** occurrence (the one that reused the number):
its billed and expected totals, its pricing state and its own findings,
including `duplicate_invoice_id`. The earlier occurrence's findings are not
copied onto that row; they remain in `findings.csv` and in the audit report,
attributed to the earlier occurrence.

### Current state

In the current run no classifier or Jev call has been made, so no semantic
result appears anywhere.

- **Clusters:** 441 in total. 358 were matched deterministically, 14 are
  deterministic UNKNOWN, and 69 are semantic-pending (covering 2,093 line
  occurrences).
- **Lines:** 384 are unit-basis provisional, and 1,709 are currently ambiguous.
- **Invoices:** of 1,125 rows, 74 are flagged, 238 are `pricing_complete` and
  237 are reconstructable. 888 therefore have a blank `expected_total_cents`.

See `outputs/hospital_2/audit_report.md` and
`outputs/hospital_2/decision_log.md`.

## Adding a hospital

When Hospital *N* is implemented, it gets `src/hospital_N/` and
`tests/hospital_N/`, written for *its* contract. It should reuse `shared/`
where that fits, and duplicate otherwise. Code moves into `shared/` only once
two real implementations show it is actually shared. Hospital 1's structure
(contract → matcher → audit → evaluation) is a starting point, not an
interface to implement. No base classes, registries or plug-in points exist
for hospitals that have not been built.

Hospital 2 was added this way. It has its own four modules and reuses
`shared/` (data loading, money, the result types and the submission writer),
but none of Hospital 1's code. `main.py` accepts `h1` and `h2`; a new hospital
adds its own choice and calls.
