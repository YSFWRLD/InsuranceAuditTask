# Prompt P001 — README wording cleanup and local commit

- **Used with:** Claude Code (Claude Opus 5)
- **Produced:** the README wording fix about Hospitals 2–5 and commit `bba8bec`. The push to origin was interrupted and cancelled by the user; nothing was pushed
- **Provenance:** Recovered verbatim from the Claude Code session transcript on 2026-09-22 (it was not saved to the repository when it was issued).

Each message is transcribed verbatim below, in the order sent.

---

### Message sent 2026-09-21 18:44 UTC

Make these two cleanup changes only.

Do not refactor anything else.
Do not change H1 audit behavior, predictions, tests, or architecture.

1. Fix the inaccurate README/report wording about Hospitals 2–5
2. Commit the current refactor cleanly to Git

==================================================
1. FIX HOSPITAL 2–5 WORDING
==================================================

Search the repository for wording that claims Hospitals 2–5:

- were never opened
- were never read
- were never inspected
- were completely untouched

That wording is no longer accurate.

Replace it with wording that is technically defensible and focused on the implementation boundary.

Preferred wording:

"Hospital 1 is implemented. Hospitals 2–5 are not yet implemented, and no production audit code exists for them."

For leakage/generalization statements, use wording like:

"The Hospital 1 prediction path does not read or depend on Hospital 2–5 data."

or:

"Hospital 2–5 data is not used by the Hospital 1 predictor, matcher, pricing engine, or evaluation logic."

The important distinction is:

- It is okay that H2 has been manually inspected/discussed.
- What matters is that H1 prediction behavior does NOT depend on H2–H5 data.

Do not make historical claims such as "never viewed" unless the repository can actually prove them.

Check at least:

- README.md
- DECISION_LOG.md
- Hospital 1 evaluation/generalization reports
- prompts or documentation that repeat the claim

Do not rewrite unrelated documentation.

==================================================
2. COMMIT THE CURRENT REFACTOR
==================================================

Inspect Git status first.

The current per-hospital architecture should be committed so the repository history contains the refactor.

Before committing:

- run the existing test suite
- ensure all tests pass
- run the H1 audit/evaluation if those are normal project verification commands
- confirm no generated junk such as __pycache__, .pytest_cache, temporary files, or secrets are being committed
- inspect .gitignore and update it only if necessary for obvious generated files

Do NOT commit:

- API keys
- .env secrets
- cache directories
- temporary files
- irrelevant local artifacts

Do commit the intended project changes including:

- src/
- tests/
- prompts/
- documentation
- decision log
- intended artifacts/outputs that are part of the project deliverables

Then create a Git commit with this message:

Refactor Hospital 1 into per-hospital architecture

Do not push unless the repository is already configured for an expected push workflow and pushing is explicitly part of the current task.

==================================================
FINAL RESPONSE
==================================================

When finished, tell me only:

- which files had wording changed
- the exact replacement wording used
- test/evaluation result
- Git commit hash
- whether the working tree is clean

Do not stop after showing a plan.
Perform both changes completely.

### Message sent 2026-09-21 18:59 UTC

push it to origin

### Message sent 2026-09-21 18:59 UTC

[Request interrupted by user]

### Message sent 2026-09-21 18:59 UTC

stop not to the og repo

### Message sent 2026-09-21 18:59 UTC

locally only
