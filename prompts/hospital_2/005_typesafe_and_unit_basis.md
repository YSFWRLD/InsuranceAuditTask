# Prompt 005 — TypeSafe endpoint default and unit-basis evidence (development)

- **Used with:** Claude Code (Claude Opus 5)
- **Kind:** development prompt to the coding assistant
- **Produced:** the default Jev endpoint `https://api.typesafe.ai/v1/systemone`, `TYPESAFE_API_KEY` taking precedence over `JEV_API_KEY`, and the rule that billed unit basis is exposed as identity evidence only when it was used for identity
- **Provenance:** Recovered verbatim from the Claude Code session transcript on 2026-09-22 (it was not saved to the repository when it was issued).

Transcribed verbatim below.

---

Make these two targeted fixes to the Hospital 2 semantic/Jev integration.

Do not refactor anything else.
Do not change Hospital 1 behavior.
Do not change deterministic H2 pricing/audit logic.
Do not run OpenRouter or Jev unless real credentials are present.
Do not fabricate semantic results.
Do not commit.

Complete the changes, run tests, and report the result.

==================================================
FIX 1 — USE THE OFFICIAL TYPESAFE / JEV API DEFAULT
==================================================

The current Jev integration requires both:

JEV_API_KEY
JEV_API_URL

That is unnecessary.

Use the official TypeSafe SystemOne endpoint as the default:

https://api.typesafe.ai/v1/systemone

The API request is:

POST /v1/systemone

Authorization:
    Bearer <API_KEY>

Request body conceptually:

{
  "state": {...},
  "model": "jev-1.13.0",
  "questions": {...}
}

The response contains the Choice results including fields such as:

- choice
- probabilities
- confidence

Update the integration so I only need to provide the API key under normal use.

Support:

TYPESAFE_API_KEY

as the preferred environment variable.

For backwards compatibility, also allow:

JEV_API_KEY

Use this precedence:

1. TYPESAFE_API_KEY
2. JEV_API_KEY

Keep JEV_API_URL as an OPTIONAL override only.

Default:

JEV_API_URL =
    https://api.typesafe.ai/v1/systemone

So the normal future usage should be:

export OPENROUTER_API_KEY="..."
export TYPESAFE_API_KEY="..."

python -m src.main semantic h2 classify
python -m src.main semantic h2 jev

No JEV_API_URL should be required.

Keep the Jev model configurable, but default/pin to:

jev-1.13.0

Do not scatter TypeSafe HTTP code throughout semantic.py.

Keep the transport isolated in one small adapter/function.

The rest of the semantic pipeline should operate on the normalized internal
Jev result format.

==================================================
JEV RESPONSE NORMALIZATION
==================================================

Normalize API responses into the existing internal structure:

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

Continue using the existing shared decision gate:

P(ACCEPT) >= 0.90
    -> accept classifier decision

P(REJECT) >= 0.90
    -> reject classifier proposal

otherwise
    -> unresolved

Do not create separate gating behavior for API versus Playground imports.

Both must go through the same validator and same gate.

==================================================
FIX 2 — LET JEV SEE UNIT-BASIS EVIDENCE ONLY WHEN IT WAS USED
==================================================

The current semantic design correctly hides billed unit basis from Jev by
default.

Keep that behavior.

However, there is one important exception:

If the classifier or semantic resolution process used billed unit basis as a
legitimate second-stage tie-breaker, then Jev must see that same evidence.

Otherwise Jev is being asked to verify a decision without seeing all evidence
that produced it.

Implement the rule:

NORMAL CASE

If:

used_unit_basis_for_identity == false

then Jev input must NOT include billed unit basis.

Jev sees only:

- raw description
- normalized description
- candidate services
- candidate clause IDs
- relevant contract evidence
- candidate contractual unit bases
- classifier status
- classifier selected service
- classifier reasoning
- classifier confidence

TIE-BREAK CASE

If:

used_unit_basis_for_identity == true

then Jev may additionally receive:

- billed_unit_basis
- used_unit_basis_for_identity = true

Make it explicit in the Jev state that billed unit basis was consumed as
identity evidence.

For example:

{
  "description": "...",
  "candidates": [...],
  "classifier": {...},
  "identity_evidence": {
    "used_unit_basis_for_identity": true,
    "billed_unit_basis": "per_hour"
  }
}

If unit basis was not used:

{
  "identity_evidence": {
    "used_unit_basis_for_identity": false
  }
}

Do NOT include billed unit basis unnecessarily.

==================================================
STRICT ANTI-CIRCULARITY
==================================================

Preserve the existing rule:

If billed unit basis was used to resolve service identity:

used_unit_basis_for_identity = true

then that same unit basis must NOT later be used as evidence for:

wrong_unit_basis

Do not weaken this rule.

Add/retain a test proving:

1. two services are textually tied
2. billed unit basis breaks the tie
3. selected service is accepted
4. wrong_unit_basis is NOT subsequently emitted from that same evidence

==================================================
NEVER SEND PRICE INFORMATION
==================================================

Even in the unit-basis tie-break case, Jev must NEVER receive:

- unit_price_cents
- line_total_cents
- invoice_total_cents
- expected totals
- rate-derived candidate hints

Unit basis may be conditionally exposed.

Price must never be exposed for service identity.

==================================================
PLAYGROUND EXPORT
==================================================

Apply the same evidence rule to:

artifacts/hospital_2/jev_state.json

If unit basis was not used for identity:
    omit billed_unit_basis

If it was used:
    include it explicitly with:
        used_unit_basis_for_identity = true

The direct API path and Playground path must produce equivalent Jev state.

Do not maintain two separate Jev-case builders.

Create one canonical verification-case builder and use it for both:

- direct API
- Playground export

==================================================
README / ENVIRONMENT DOCUMENTATION
==================================================

Update the README so future setup is clear.

Document:

OPENROUTER_API_KEY
TYPESAFE_API_KEY

Optional overrides:

JEV_API_KEY
JEV_API_URL
JEV_MODEL

Example:

export OPENROUTER_API_KEY="..."
export TYPESAFE_API_KEY="..."

python -m src.main semantic h2 classify
python -m src.main semantic h2 jev
python -m src.main audit h2
python -m src.main submission

Mention that:

JEV_API_URL normally does not need to be set.

The default is:

https://api.typesafe.ai/v1/systemone

==================================================
TESTS
==================================================

Add/update tests for:

1. TYPESAFE_API_KEY is accepted
2. JEV_API_KEY remains accepted as fallback
3. TYPESAFE_API_KEY wins if both exist
4. default Jev URL is:
   https://api.typesafe.ai/v1/systemone
5. JEV_API_URL can override the default
6. default model is jev-1.13.0
7. API request uses:
   state
   model
   questions
8. bearer authorization is constructed correctly
9. API response is normalized into the same structure as Playground import
10. API and Playground results use the same validator/gate

Unit-basis evidence tests:

11. normal semantic case does NOT expose billed_unit_basis to Jev
12. tie-break case DOES expose billed_unit_basis
13. tie-break case sets used_unit_basis_for_identity=true
14. no price fields appear in either Jev state
15. Playground and API build identical verification evidence
16. unit basis consumed for identity cannot later trigger wrong_unit_basis

==================================================
REGRESSION
==================================================

Run the full test suite.

Then run:

python -m src.main audit h1
python -m src.main evaluate h1
python -m src.main audit h2
python -m src.main semantic h2 status

Do not run:

semantic h2 classify
semantic h2 jev

unless real credentials are already set.

Expected H1 behavior must remain unchanged.

The current H2 deterministic findings should also remain unchanged because this
task only changes semantic transport/evidence plumbing.

==================================================
GIT
==================================================

Do not commit.

Leave the working tree ready for review.

==================================================
FINAL RESPONSE
==================================================

Report only:

- files changed
- test count/result
- H1 regression result
- H2 deterministic regression result
- final supported environment variables
- final default Jev endpoint
- final default Jev model
- confirmation that billed unit basis is only exposed when actually consumed
  for identity
- confirmation that no price fields are ever sent to Jev
- confirmation that no semantic API calls were made without credentials
- confirmation that nothing was committed

Perform the fixes now.
