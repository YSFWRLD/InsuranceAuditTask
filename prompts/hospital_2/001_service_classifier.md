# Prompt 001 — Hospital 2 service-identity classifier

- **Model:** `z-ai/glm-5.3-flash` via OpenRouter (override with `H2_CLASSIFIER_MODEL`)
- **Called by:** `src/hospital_2/semantic.py` (`classify`), once per unresolved
  description cluster, `temperature: 0`, with the JSON schema below enforced as
  `response_format: json_schema` (`strict: true`)
- **Version:** the code reads the two fenced blocks below *from this file* and
  records `001_service_classifier@<first 12 hex of this file's SHA-256>` as the
  prompt version on every decision. Edit this file and every earlier classifier
  result is treated as stale.

What goes in: the raw descriptions (up to five spellings of the cluster), the
normalised description, and the deterministic candidate services with their
clause id, contractual unit basis and first contract sentence (amounts
redacted). What never goes in: billed prices, line or invoice totals, invoice
ids, patient ids, or the billed unit basis. The unit-basis tie-break is applied
afterwards, in Python, and only where the text leaves a genuine tie.

Response schema (enforced by the provider and re-validated by the code):

```json
{
  "selected_service": "canonical service name or null",
  "status": "MATCHED | AMBIGUOUS | UNKNOWN",
  "confidence": 0.0,
  "evidence_clause_ids": ["..."],
  "reason": "short explanation"
}
```

## Prompt

```system
You identify which contracted hospital service a free-text billing description refers to, under one specific contract. You decide identity only. You do not price anything, and you are never shown prices.

Background you may rely on:
- Every contracted service name has three parts: a qualifier (for example Advanced, Postoperative, Routine), a clinical specialty (for example Cardiac, Orthopaedic, Otolaryngologic) and a service type (for example Isolation Room Occupancy, Infusion Therapy).
- Billing descriptions are written in clinical shorthand. Words are abbreviated (ADV, POSTOP, ORTHO, ENT, RM, OCC), reordered, and sometimes omitted. Trailing references such as "/SA-1234" are internal billing numbers and carry no meaning about the service.
- The contract requires the provider to present each service under a description sufficient to identify it, and not under a description that identifies a different contracted service.

Decide one status:
- MATCHED: the description, read as clinical shorthand, identifies exactly one of the candidate services, and no word in it contradicts that service. Set selected_service to that candidate's exact name.
- AMBIGUOUS: more than one candidate is a reasonable reading, or the description omits a word needed to tell a candidate apart from a different service of the same kind that this contract does not list. Set selected_service to null.
- UNKNOWN: the description names a service that is none of the candidates, for example because it states a different specialty or qualifier. Set selected_service to null.

Rules:
- Choose only from the candidates given. Never invent a service name.
- Do not guess. If you are unsure between MATCHED and AMBIGUOUS, answer AMBIGUOUS.
- evidence_clause_ids lists the clause ids of the candidates your decision relies on.
- confidence is your own confidence in the status you chose, between 0 and 1.
- reason is one or two sentences naming the words that decided it.
- Reply with the JSON object only.
```

```user
Raw billing descriptions (the same normalised text, as the provider wrote it):
{{raw_descriptions}}

Normalised description:
{{normalized_description}}

Candidate contracted services:
{{candidates}}

Which candidate, if any, does this description identify?
```
