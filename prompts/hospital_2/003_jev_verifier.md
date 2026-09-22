# Prompt 003 — Jev verification of a classifier decision (choice question)

- **Supersedes:** [002](002_jev_verifier.md), a prose system/user prompt
  whose rendered questions used an invented `question`/`options` shape. 002
  was never put to Jev. It is kept unchanged as history.
- **Verifier:** Jev, model `jev-1.13.0` (override with `JEV_MODEL`).
- **Used by:** `src/hospital_2/semantic.py`. `load_jev_template` reads the one
  JSON block below *from this file*. `build_jev_batch` renders it once per
  case, replacing `{{case_id}}`. The result is written to
  `artifacts/hospital_2/jev_questions.json` (Playground mode) or sent over the
  API (direct mode). The prompt version recorded on every verdict is
  `003_jev_verifier@<first 12 hex of this file's SHA-256>`. Edit this file and
  earlier verdicts stop matching and are dropped as stale.

## How it is used

Jev receives two documents.

- **State** (`jev_state.json`), one entry per case under `cases`, keyed
  `h2_<cluster_id>`, holding:
  - the raw and normalised description;
  - the candidate services, each with its clause id, contractual unit basis
    and first contract sentence (amounts redacted);
  - the classifier's status, selected service, confidence, evidence clauses
    and reason.

  The state never holds a price, a total, an invoice or patient id, or the
  billed unit basis.
- **Questions** (`jev_questions.json`), one choice question per case, keyed
  `verify_h2_<cluster_id>`.

Jev returns a probability for each of ACCEPT, REJECT and UNCERTAIN. The code,
not Jev, applies the gate:

| Jev verdict | effect |
|---|---|
| P(ACCEPT) ≥ 0.90 | the decision stands **with its own status** |
| P(REJECT) ≥ 0.90 | rejected; the cluster stays unresolved |
| anything else | unresolved |

An accepted AMBIGUOUS stays AMBIGUOUS, and an accepted UNKNOWN stays UNKNOWN.

## Question

```json
{
  "type": "choice",
  "instructions": "Verify whether the classifier decision recorded in state.cases[\"{{case_id}}\"] is justified by that case's billing description and its supplied contract candidates. Judge the decision; do not look for a different answer. Billing descriptions are clinical shorthand: words are abbreviated, reordered and sometimes omitted, and trailing references such as /SA-1234 carry no meaning. Every service name has three parts: a qualifier, a clinical specialty and a service type. Prices are deliberately withheld, so identity must follow from the words alone. Accepting AMBIGUOUS or UNKNOWN means agreeing that no single candidate is identified; it never means choosing one.",
  "criteria": {
    "ACCEPT": "The decision in state.cases[\"{{case_id}}\"] is right. For MATCHED: the description identifies that one candidate and nothing in it contradicts that candidate. For AMBIGUOUS: the description genuinely fails to single out one candidate, including when it omits a word needed to tell a candidate apart from an uncontracted service of the same kind. For UNKNOWN: the description names something that is none of the candidates.",
    "REJECT": "The decision in state.cases[\"{{case_id}}\"] is wrong: a different candidate is clearly meant; a MATCHED decision ignores a contradicting or missing word; or an AMBIGUOUS or UNKNOWN decision overlooks a description that plainly identifies exactly one candidate.",
    "UNCERTAIN": "The description in state.cases[\"{{case_id}}\"] does not let you tell whether the decision is right or wrong."
  }
}
```

## Returning results (Playground mode)

This project holds no documentation of the Jev Playground's own export format.
So `jev_results.json` is *this project's* input format: copy the choice and the
three probabilities Jev reports for each question into it, unchanged.

```text
{
  "model": "jev-1.13.0",
  "results": {
    "verify_h2_c-0123456789": {
      "choice": "ACCEPT",
      "probabilities": {"ACCEPT": 0.95, "REJECT": 0.03, "UNCERTAIN": 0.02}
    }
  }
}
```

Include Jev's `confidence` too if the Playground shows it. `confidence` and a
per-item `model` are optional:

- `confidence` must be a number in [0, 1], but it is Jev's own figure and need
  **not** equal any probability. A live answer had P(ACCEPT) = 0.92 with
  confidence 0.87.
- `model`, if given, must match the configured model.

The direct API returns the same answer objects under an `answers` key (with
`usage` metadata beside them). Both are read by the same validator. The gate
uses the ACCEPT and REJECT probabilities only, never `confidence`.

Import is all-or-nothing, and refuses any result that:

- names an unknown or non-exported question id;
- has a choice other than ACCEPT, REJECT or UNCERTAIN;
- has a probability that is missing, non-numeric or outside [0, 1];
- has probabilities that do not sum to 1;
- has a choice that is not the most probable outcome;
- has a confidence that is not a number in [0, 1];
- was issued for a classifier answer that has since changed.
