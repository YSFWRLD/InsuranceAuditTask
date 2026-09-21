# Invoice audit — contract-driven, deterministic, per hospital

The exercise brief is in [`EXERCISE.md`](EXERCISE.md). This file describes the
solution.

**Status:** Hospital 1 is implemented. Hospitals 2–5 are not yet implemented,
and no production audit code exists for them. The Hospital 1 prediction path
does not read or depend on Hospital 2–5 data.

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
└── hospital_1/              the Hospital 1 solution
    ├── contract.py          the Agreement -> ContractRules; fails loudly
    ├── matcher.py           description -> MATCHED / AMBIGUOUS / UNKNOWN
    ├── audit.py             context, pricing, checks, reconstruction, confidence
    └── evaluation.py        labels, locked split, metrics, freeze, research
                             (the ONLY module that reads labels)

tests/
├── shared/                  test_data.py, test_money.py
└── hospital_1/              conftest.py (a synthetic contract), test_contract.py,
                             test_matching.py, test_audit.py, test_evaluation.py

outputs/                     deliverables
├── submission.csv           template format
└── hospital_1/
    ├── predictions.csv      submission columns + uncertainty columns
    ├── findings.csv         one row per finding, with its line
    ├── evaluation.md        dev + locked-holdout scores
    └── generalization_report.md   the honest write-up

artifacts/hospital_1/        evidence behind the outputs
├── split/                   dev_labels.csv, holdout_labels.csv (70/30, fixed seed)
├── split_manifest.json      how the split was drawn, and when
├── freeze.json              prediction-source hashes + predictions fingerprint
├── service_matches.csv      every distinct description and how it was matched
└── research/                temporal_evaluation.md, ablation.md (development only)

prompts/hospital_1/          the prompts that produced this, versioned
DECISION_LOG.md              readings of the contract, and why
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

Python 3.11+. The engine uses only the standard library; pytest is for tests.

```bash
pip install -r requirements.txt
```

```bash
python -m pytest tests -q
```

```bash
python -m src.main audit h1
```

```bash
python -m src.main evaluate h1
```

```bash
python -m src.main submission
```

`audit` writes `outputs/hospital_1/{predictions,findings}.csv` and
`artifacts/hospital_1/service_matches.csv`. `evaluate` writes
`outputs/hospital_1/evaluation.md`. `submission` writes `outputs/submission.csv`
in the template's format. Today it contains Hospital 1's rows only, because
Hospital 1 is the only hospital implemented. Hospital 1 is the labelled
development hospital and is not what the exercise scores.

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

## Adding a hospital

When Hospital *N* is implemented, it gets `src/hospital_N/` and
`tests/hospital_N/`, written for *its* contract. It should reuse `shared/`
where that fits, and duplicate otherwise. Code moves into `shared/` only once
two real implementations show it is actually shared. Hospital 1's structure
(contract → matcher → audit → evaluation) is a starting point, not an
interface to implement. No base classes, registries or plug-in points exist
for hospitals that have not been built.

`main.py` accepts `h1` only; a new hospital adds its own choice and calls.
