# Prompt 001 — Hospital 3: Jev resolution of a missing-word ambiguity (choice question)

- **Reviewer:** Jev, model `jev-1.13.0` (TypeSafe SystemOne; override with `JEV_MODEL`).
- **Kind:** runtime prompt. The code sends it; no person edits the rendered text.
- **Used by:** `src/hospital_3/semantic.py`. `load_template` reads the one JSON
  block below *from this file*. `missing_word_request` builds one request per
  unresolved description cluster and billed unit basis, with one question whose
  options are that cluster's candidate services plus two fixed options.
- **Version:** every stored review records `001_jev_missing_word_resolution@<first
  12 hex of this file's SHA-256>`. Editing this file makes every stored review
  stale. Do not edit it in place once reviews depend on it.
- **Origin:** the JSON block is Hospital 5's reviewed question
  ([`../hospital_5/002_jev_missing_word_resolution.md`](../hospital_5/002_jev_missing_word_resolution.md)),
  copied unchanged so that the question, its options and its wording are not
  re-tuned for Hospital 3. Only this header and the paths differ. The code
  fills in Hospital 3's own candidates and vocabulary.

## When it is asked

Only after Hospital 3's deterministic normalisation, structural matching and
the unit-basis tie-break have all failed to identify one service, and only
when the text leaves a bounded set of contracted candidates (at most
`H3_JEV_MAX_CANDIDATES`, default 8). It is asked once per cluster (identity
key × billed unit basis), never once per line. No normalisation question is
asked for Hospital 3: its vocabulary is derived deterministically
(`src/hospital_3/normalization.py`).

## What one request contains

The static state fields are in the JSON block. The code adds these:

| field | content |
|---|---|
| `description.normalized_examples` | up to five normalised spellings of the description (reference suffix removed) |
| `description.words_read` | the contract words the description was read as |
| `description.unread_tokens` | tokens the Hospital 3 lexicon could not read |
| `description.occurrence_count` | how many invoice lines carry it |
| `billed_unit_basis` | the unit basis on those lines: secondary evidence only |
| `candidates[]` | every viable contracted service: option id, name, qualifier, specialty, concept, contracted unit basis, and which of qualifier / specialty / concept the description matches or misses |
| `contradicted_services[]` | contracted services that share the description's service concept but are contradicted by one of its words, with the contradicting slot |

Never sent: a unit price, base rate, amended rate, adjusted rate, line or
invoice total, a Service Date or invoice date, an invoice or patient id, or
anything about which candidate would make an invoice's arithmetic work.

## Criteria

One option per candidate, keyed by the service name in snake case
(`extended_musculoskeletal_endoscopic_procedure`), rendered from
`service_option` below, plus `ambiguous_contracted_service` and
`none_of_the_above_or_unknown`.

## Gate (applied in code, not by Jev; fixed before any Hospital 3 review was run)

| gated outcome | condition (defaults; see `.env.example`) |
|---|---|
| service identified | choice is that service, P ≥ `H3_JEV_SERVICE_MIN_PROBABILITY` (0.90), confidence ≥ `H3_JEV_SERVICE_MIN_CONFIDENCE` (0.80), and the description evidences all but at most one of its three name slots |
| ambiguous among contracted candidates | two or more candidates, each missing at most one slot, choice is not `none_of_the_above_or_unknown`, and P(all service options) + P(ambiguous) ≥ `H3_JEV_CLOSURE_MIN_PROBABILITY` (0.90) |
| unknown (not a contracted service) | choice is `none_of_the_above_or_unknown` with P ≥ `H3_JEV_UNKNOWN_MIN_PROBABILITY` (0.90) |
| unresolved | everything else: the deterministic status stands |

These are Hospital 5's gates after its pre-commit review, unchanged. There is
no single-candidate closure.

## Question

```json
{
  "question_id": "missing_word_resolution",
  "question": {
    "type": "choice",
    "instructions": {
      "question": "Which interpretation of the billing description in state.description is best supported by its words: exactly one of the contracted services in state.candidates, several of them equally, or none of them?",
      "focus": "Choose one service only if allowed non-price evidence distinguishes it from every other candidate. Do not fill in an omitted word from nothing: if the omitted word is the only thing separating two or more candidates, the answer is ambiguous_contracted_service."
    },
    "service_option": {
      "what": "The description refers to the contracted service {service_name} (qualifier {qualifier}, specialty {specialty}, concept {concept}); the words it carries fit this service and do not fit any other candidate equally well.",
      "not_for": "Choosing {service_name} when another candidate fits the description's words equally well, when the only distinguishing word is missing from the description, or when a word of the description contradicts it."
    },
    "fixed_criteria": {
      "ambiguous_contracted_service": {
        "what": "The description is one of the contracted candidates, but its words fit two or more of them equally well, typically because the word that would separate them (a qualifier or a specialty) is missing.",
        "not_for": "A description whose words single out one candidate, or one that names something no candidate can be."
      },
      "none_of_the_above_or_unknown": {
        "what": "The description names something that none of the candidates can be: a word it carries contradicts every candidate, or it plainly describes a service this agreement does not contract.",
        "not_for": "A description that merely omits a word while fitting one or more candidates."
      }
    }
  },
  "state_static": {
    "task": "Decide which contracted service a hospital billing description refers to, when the description omits a word of the service name.",
    "facts": [
      "Every line was billed under this agreement. state.candidates lists every contracted service that no word of the description contradicts.",
      "Contracted service names have three parts: a qualifier, a clinical specialty and a service concept. Billing descriptions abbreviate, reorder and often omit one of these words.",
      "state.contradicted_services lists contracted services that share the description's concept but are ruled out by a word the description does carry."
    ],
    "allowed_evidence": [
      "The words the description carries, read as the contract words in state.description.words_read.",
      "The qualifier, specialty and concept of each candidate, and which of them the description matches, misses or contradicts.",
      "The fact that only one contracted service carries a particular combination of the description's words.",
      "The billed unit basis, as secondary evidence only, and only when the words leave a tie."
    ],
    "forbidden_evidence": [
      "Prices, rates, quantities or totals of any kind (none are supplied).",
      "Which candidate would make an invoice's arithmetic come out right.",
      "Inventing the omitted qualifier or specialty when it is the only word separating candidates.",
      "Any other party's prediction or answer."
    ],
    "financial_values": "none; prices, quantities and totals are deliberately withheld"
  }
}
```

## Returning results (Playground mode)

`semantic h3 missing-word-export` writes
`artifacts/hospital_3/jev_missing_word_requests.json`. Run each body in the
Playground and write `artifacts/hospital_3/jev_missing_word_results.json`:
an object with `results`, keyed by `review_id`, each with the copied
`request_sha256`, `choice`, `probabilities` over exactly that request's
options, and `confidence`. `semantic h3 missing-word-import` validates it
all-or-nothing against the current requests.
