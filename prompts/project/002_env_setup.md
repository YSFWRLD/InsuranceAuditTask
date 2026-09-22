# Prompt P002 — `.env` / python-dotenv setup

- **Used with:** Claude Code (Claude Opus 5)
- **Produced:** `load_environment()` in `src/main.py`, `.env.example`, the `.gitignore` entries, and the setup tests
- **Provenance:** Recovered verbatim from the Claude Code session transcript on 2026-09-22 (it was not saved to the repository when it was issued).

Transcribed verbatim below.

---

Make these small project setup fixes.

Do not refactor the project.
Do not change H1 or H2 audit behavior.
Do not run OpenRouter or Jev.
Do not commit.

==================================================
1. FIX README KEY WORDING
==================================================

The README currently makes the API keys sound globally required.

Change the wording so it is clear that the keys are only required for the semantic steps.

Use wording equivalent to:

OPENROUTER_API_KEY
    Required only for:
        python -m src.main semantic h2 classify

TYPESAFE_API_KEY
    Required only for:
        python -m src.main semantic h2 jev

Optional:
    JEV_API_KEY
    JEV_API_URL
    JEV_MODEL
    JEV_THRESHOLD
    H2_CLASSIFIER_MODEL
    OPENROUTER_BASE_URL
    H2_CLASSIFIER_MAX_ATTEMPTS

Also make it clear that these commands should still work without API keys:

- H1 audit/evaluation
- H2 deterministic audit
- H2 semantic status
- Jev Playground export/import
- tests

Do not rewrite unrelated README sections.

==================================================
2. ADD .ENV SUPPORT
==================================================

Add support for loading environment variables from a project-root `.env` file.

Use `python-dotenv`.

Add the dependency to the existing dependency file used by the project.

Do not create a second dependency-management system.

At application startup, load the project-root `.env` file once.

Prefer a simple implementation such as:

from dotenv import load_dotenv

load_dotenv()

Place this in the project entry point or another appropriate startup location.

Do not scatter `load_dotenv()` calls throughout multiple modules.

Existing real OS environment variables should still work normally.

==================================================
3. CREATE .env
==================================================

Create a project-root file:

.env

with placeholders only:

OPENROUTER_API_KEY=
TYPESAFE_API_KEY=

# Optional overrides
JEV_API_KEY=
JEV_API_URL=
JEV_MODEL=jev-1.13.0
JEV_THRESHOLD=0.90
H2_CLASSIFIER_MODEL=z-ai/glm-5.3-flash
OPENROUTER_BASE_URL=
H2_CLASSIFIER_MAX_ATTEMPTS=

Do NOT place any real API keys in the file.

==================================================
4. CREATE .env.example
==================================================

Also create:

.env.example

with the same variable names and explanatory comments.

This file IS safe to commit.

For example:

# Required only for Hospital 2 semantic classification
OPENROUTER_API_KEY=

# Required only for direct Jev verification
TYPESAFE_API_KEY=

# Optional fallback/overrides
JEV_API_KEY=
JEV_API_URL=
JEV_MODEL=jev-1.13.0
JEV_THRESHOLD=0.90
H2_CLASSIFIER_MODEL=z-ai/glm-5.3-flash
OPENROUTER_BASE_URL=
H2_CLASSIFIER_MAX_ATTEMPTS=

==================================================
5. UPDATE .gitignore
==================================================

Ensure `.gitignore` contains:

.env

Do NOT ignore:

.env.example

Also ensure obvious secret/local files are not accidentally tracked.

Do not remove existing useful gitignore rules.

==================================================
6. VALIDATE THE SETUP
==================================================

Add or update a small test if appropriate to verify that:

- project startup loads `.env`
- OS environment variables still work
- `.env` does not need to exist for normal non-semantic commands
- no real secrets are present in committed files

Do not add excessive tests for this simple setup change.

==================================================
7. README USAGE EXAMPLE
==================================================

Add a short setup example:

1. Copy `.env.example` to `.env`
2. Fill in:

   OPENROUTER_API_KEY
   TYPESAFE_API_KEY

3. Run:

   python -m src.main semantic h2 classify
   python -m src.main semantic h2 jev
   python -m src.main audit h2
   python -m src.main submission

Also state clearly:

- `.env` is local and ignored by Git
- `.env.example` contains no secrets and may be committed
- API keys are not required for deterministic audits/tests

==================================================
REGRESSION
==================================================

Run the full test suite after the change.

Then run at least:

python -m src.main audit h1
python -m src.main audit h2
python -m src.main semantic h2 status

Do not run semantic classification or Jev calls.

H1 and H2 deterministic results must remain unchanged.

==================================================
GIT
==================================================

Do not commit.

Leave the working tree ready for review.

==================================================
FINAL RESPONSE
==================================================

Report only:

- files changed/created
- whether python-dotenv was added
- where `.env` is loaded
- confirmation `.env` is gitignored
- confirmation `.env.example` is not gitignored
- test result
- H1 regression result
- H2 deterministic regression result
- confirmation no API calls were made
- confirmation nothing was committed

Perform the changes now.
