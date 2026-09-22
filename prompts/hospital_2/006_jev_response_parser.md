# Prompt 006 — fix the Jev response parser (development)

- **Used with:** Claude Code (Claude Opus 5)
- **Kind:** development prompt to the coding assistant
- **Produced:** parsing of the `answers` container, ignoring `usage`, and treating `confidence` as independent of the probabilities. Only one live probe was run
- **Provenance:** Recovered verbatim from the Claude Code session transcript on 2026-09-22 (it was not saved to the repository when it was issued).

Transcribed verbatim below.

---

Fix the Hospital 2 Jev response parser using the real TypeSafe API response shape observed from a live probe.

Do not refactor unrelated code.
Do not change the semantic decision policy.
Do not change H1 behavior.
Do not run the full 69-cluster semantic pipeline yet.
Do not commit.

The OpenRouter classifier already works correctly.

The Jev API also works, but the current project parser rejects valid real responses because it made two incorrect assumptions.

==================================================
REAL JEV RESPONSE BEHAVIOR OBSERVED
==================================================

A live Jev request succeeded using:

jev-1.13.0

The API returned a structure with:

{
  "answers": ...,
  "usage": ...
}

rather than:

{
  "results": ...
}

A real answer also returned values equivalent to:

choice = ACCEPT

probabilities:
    ACCEPT = 0.92
    REJECT = 0.08
    UNCERTAIN = 0.00

confidence = 0.87

Important:

confidence is NOT necessarily equal to max(probabilities).

The current code incorrectly rejects this valid response.

==================================================
FIX 1 — PARSE `answers`
==================================================

Update the Jev API response parser so it understands the actual response shape.

The API result container is:

answers

Do not interpret top-level metadata such as:

usage

as question IDs.

Parse only the contents of `answers` as Jev answers.

If appropriate, support the old/internal `results` shape as backwards compatibility, but the real API `answers` shape must be the primary supported format.

Do not silently accept arbitrary unknown structures.

If neither a valid `answers` nor supported result container exists, fail visibly with a useful error.

==================================================
FIX 2 — CONFIDENCE IS INDEPENDENT
==================================================

Remove the rule requiring:

confidence == max(probabilities)

That assumption is incorrect.

Treat Jev's reported confidence as its own valid numeric field.

Validation should require:

- confidence is numeric
- 0 <= confidence <= 1

but it does NOT need to equal the largest probability.

Do not use `confidence` for the acceptance gate unless the existing design explicitly requires it.

The current decision gate remains probability-based:

if P(ACCEPT) >= 0.90:
    accept classifier decision

elif P(REJECT) >= 0.90:
    reject classifier proposal

else:
    unresolved

Do not change this threshold policy.

==================================================
NORMALIZED INTERNAL FORMAT
==================================================

Continue normalizing every answer into the existing internal form:

{
  "choice": "ACCEPT | REJECT | UNCERTAIN",
  "probabilities": {
    "ACCEPT": 0.0,
    "REJECT": 0.0,
    "UNCERTAIN": 0.0
  },
  "confidence": 0.0,
  "model": "jev-1.13.0"
}

The direct API path and Playground import path must still use the same downstream validator and gate wherever possible.

==================================================
VALIDATION
==================================================

Keep strict validation.

For each Jev answer verify:

- the question/case ID exists
- `choice` is one of:
    ACCEPT
    REJECT
    UNCERTAIN
- probabilities exist
- ACCEPT, REJECT and UNCERTAIN probabilities are numeric
- every probability is between 0 and 1
- probability totals are sensible, allowing tiny floating-point tolerance
- confidence is numeric and between 0 and 1
- stale or unknown case IDs fail visibly
- malformed answers are never silently accepted

Ignore documented top-level metadata such as `usage`.

Do not treat metadata keys as verdicts.

==================================================
ADD A REAL-SHAPE REGRESSION TEST
==================================================

Add a test fixture based on the observed live response shape.

Use sanitized data, for example:

{
  "answers": {
    "verify_h2_example": {
      "choice": "ACCEPT",
      "probabilities": {
        "ACCEPT": 0.92,
        "REJECT": 0.08,
        "UNCERTAIN": 0.0
      },
      "confidence": 0.87
    }
  },
  "usage": {
    "...": "sanitized metadata"
  }
}

The test must prove:

1. `answers` is parsed correctly
2. `usage` is ignored
3. confidence 0.87 is accepted even though max probability is 0.92
4. the normalized answer is valid
5. P(ACCEPT)=0.92 passes the existing 0.90 gate
6. no behavior depends on confidence equaling the top probability

Also retain/add tests for:

- P(REJECT) >= 0.90
- weak ACCEPT remains unresolved
- weak REJECT remains unresolved
- malformed `answers` fails safely
- unknown question IDs fail safely
- invalid probabilities fail safely
- invalid confidence fails safely
- Playground import still follows the same decision gate

==================================================
LIVE PROBE
==================================================

After the parser fix:

run the full test suite.

Then make ONE minimal Jev live probe through the project's own code only if a real TypeSafe/Jev key is already configured.

Do not run the full semantic pipeline.

The probe should confirm that the same real API response shape now:

- parses successfully
- normalizes successfully
- passes through the probability gate

Do not persist the probe verdict to:

service_mappings.json

Do not modify production mappings.

Do not expose or print API keys.

If credentials are unavailable, skip the live probe and state that clearly.

==================================================
REGRESSION
==================================================

Run:

python -m pytest

python -m src.main audit h1
python -m src.main audit h2
python -m src.main semantic h2 status

Do not run the full:

python -m src.main semantic h2 classify
python -m src.main semantic h2 jev

yet.

H1 must remain unchanged.

H2 deterministic outputs must remain unchanged.

==================================================
DOCUMENTATION
==================================================

Update the relevant README/decision-log text only if it currently documents the incorrect Jev response assumptions.

Document that:

- TypeSafe returns answers under `answers`
- `usage` is response metadata
- Jev confidence is independent from the choice probability distribution
- the project gate uses ACCEPT/REJECT probabilities, not confidence

Do not over-document unrelated details.

==================================================
GIT
==================================================

Do not commit.

Leave changes in the working tree.

==================================================
FINAL RESPONSE
==================================================

Report:

- files changed
- test result
- whether `answers` is now supported
- whether `usage` is correctly ignored
- confirmation confidence no longer needs to equal max probability
- result of the single live Jev probe, if credentials were available
- H1 regression result
- H2 deterministic regression result
- confirmation no mappings were persisted
- confirmation the 69-cluster pipeline was NOT run
- confirmation nothing was committed

Perform the fix now.
