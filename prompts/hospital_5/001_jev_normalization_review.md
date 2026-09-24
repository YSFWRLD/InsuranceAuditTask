# Prompt 001 — Jev review of one proposed normalisation (choice question)

- **Reviewer:** Jev, model `jev-1.13.0` (TypeSafe SystemOne; override with `JEV_MODEL`).
- **Kind:** runtime prompt. The code sends it; no person edits the rendered text.
- **Used by:** `src/hospital_5/semantic.py`. `load_template` reads the one JSON
  block below *from this file*. `normalization_request` adds the dynamic state
  fields for one proposal and sends one request with one question.
- **Version:** every stored review records `001_jev_normalization_review@<first
  12 hex of this file's SHA-256>`. Editing this file makes every stored review
  stale; the audit then refuses to run until the reviews are redone. Do not
  edit it in place once reviews depend on it: add the next number instead.

## What one request contains

One proposal per request: `normalization_safety` is the only question, and the
state holds the evidence for that one proposal.

The static state fields are in the JSON block. The code adds these:

| field | content |
|---|---|
| `contract_vocabulary` | every qualifier, specialty and service concept in the Hospital 5 agreement (words only) |
| `candidate_under_review.invoice_token` | the token, lower case, e.g. `endo` |
| `candidate_under_review.proposed_normalization` | the proposed contract word or phrase, e.g. `endocrine` |
| `candidate_under_review.proposed_word_slots` | the slot of each proposed word (qualifier / specialty / concept) |
| `candidate_under_review.generated_by` | the deterministic heuristics that proposed it |
| `colliding_contract_words` | every contract word the same token was proposed for |

No price, rate, quantity, total, invoice id or patient id is ever sent. No
invoice description is sent either: the review is about what the token can
mean against the contract vocabulary, not about how one invoice used it.

## Gate (applied in code, not by Jev)

| gated decision | condition (defaults; see `.env.example`) |
|---|---|
| `safe_global_normalization` | choice is safe, P(safe) ≥ `H5_JEV_GLOBAL_MIN_PROBABILITY` (0.90), confidence ≥ `H5_JEV_GLOBAL_MIN_CONFIDENCE` (0.85), and the proposal is a single word |
| `context_required` | not unsafe by choice, and P(safe) + P(context) ≥ `H5_JEV_CONTEXT_MIN_PROBABILITY` (0.80); also any single-word proposal judged safe below the global bar, and any phrase proposal that would otherwise have been global |
| `unsafe_normalization` (rejected) | everything else, and every proposal that adds a qualifier or specialty word (code invariant, whatever the review says) |

## Question

```json
{
  "question_id": "normalization_safety",
  "question": {
    "type": "choice",
    "instructions": {
      "question": "May the invoice token in state.candidate_under_review.invoice_token be rewritten as state.candidate_under_review.proposed_normalization in every Hospital 5 billing description, without changing, narrowing or inventing meaning?",
      "focus": "Judge this one mapping only. Compare the token with state.colliding_contract_words: if the token could just as plausibly stand for another word listed there, the mapping needs the surrounding words of each description. If the proposed normalization contains any word the token itself does not stand for, the mapping invents meaning. No prices are supplied and none may be assumed."
    },
    "criteria": {
      "safe_global_normalization": {
        "what": "The token is a recognisable abbreviation, truncation or spelling variant of exactly the proposed words and would not plausibly be read as any other word in state.colliding_contract_words, so rewriting it everywhere keeps the same meaning.",
        "not_for": "A token that could plausibly stand for another listed contract word, or a proposal containing a word the token does not stand for."
      },
      "context_required": {
        "what": "The proposed words are a plausible reading of the token, but the token could also plausibly stand for another word in state.colliding_contract_words, so only the surrounding words of a description can decide which is meant.",
        "not_for": "A proposal that adds, narrows or invents meaning, or a token whose only plausible reading is the proposal."
      },
      "unsafe_normalization": {
        "what": "The proposal changes, narrows or invents meaning: it adds a word the token does not stand for (such as a specialty or a second concept word), maps the token to a word that merely shares some of its letters, or turns a general word into a more specific phrase.",
        "not_for": "A faithful expansion of the token, including one that needs context to choose between readings."
      }
    }
  },
  "state_static": {
    "task": "Review one proposed normalisation of an abbreviated word found in hospital billing descriptions, before it may be used to match descriptions to contracted services.",
    "about_the_descriptions": "The provider writes each billed service as free-text shorthand: words are abbreviated, reordered and sometimes omitted, and reference suffixes such as /PH-1234 are removed before review. Every contracted service name has three parts: a qualifier, a clinical specialty and a service concept.",
    "hard_normalization_rules": [
      "A normalisation may only expand what the token itself abbreviates; it must never add a word the token does not stand for.",
      "A normalisation must not narrow a general word into a more specific phrase.",
      "A token that could stand for two different contract words cannot be expanded globally.",
      "Sharing letters with a word is not enough: the token must be a recognisable way of writing that word."
    ],
    "allowed_behavior": [
      "Recognise standard clinical and billing abbreviations, truncations and consonant skeletons.",
      "Recognise British and American spellings of the same word."
    ],
    "forbidden_behavior": [
      "Inventing a qualifier, specialty or concept word that the token does not abbreviate.",
      "Choosing a reading because it would make some description match a particular service.",
      "Using prices, quantities or totals (none are supplied)."
    ],
    "financial_values": "none; prices, quantities and totals are deliberately withheld"
  }
}
```

## Returning results (Playground mode)

`semantic h5 normalize-export` writes `artifacts/hospital_5/jev_normalization_requests.json`:
one entry per pending proposal, each with its `review_id`, its
`request_sha256` and the exact `{state, model, questions}` body. Run each body
in the Playground and write `jev_normalization_results.json`:

```text
{
  "model": "jev-1.13.0",
  "results": {
    "endo->endocrine": {
      "request_sha256": "<copied from the export entry>",
      "choice": "context_required",
      "probabilities": {"safe_global_normalization": 0.1, "context_required": 0.85, "unsafe_normalization": 0.05},
      "confidence": 0.78
    }
  }
}
```

`semantic h5 normalize-import` validates every entry against the *current*
request (the same validator the direct API path uses) and refuses the whole
file if any entry is malformed, names an unknown proposal, or carries a
`request_sha256` that no longer matches.
