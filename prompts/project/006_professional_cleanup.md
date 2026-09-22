# Prompt P006 — final professional wording and history cleanup

- **Used with:** Claude Code (Claude Opus 5)
- **Preceded by:** [005](005_documentation_cleanup.md)
- **Produced:** neutral terminology in non-frozen code and in reviewer-facing documents, and an amended HEAD commit message. No prediction, metric, semantic decision, frozen H1 source or runtime prompt changed
- **Provenance:** saved when issued (2026-09-22), copied verbatim from the session.

Transcribed verbatim below.

---

Perform one final professional-quality cleanup of the repository.

This is a wording/history cleanup only.

DO NOT:
- change H1 or H2 prediction logic
- change contract interpretation
- change matching thresholds
- change confidence values
- change any submitted prediction
- rerun OpenRouter
- rerun Jev
- change persisted semantic decisions
- start H3/H4/H5
- modify historical prompt text that is intentionally preserved verbatim
- modify active runtime prompt files
- rewrite old commits other than the current HEAD commit
- modify frozen H1 prediction-path files just to improve comments

The goal is to remove stale, inaccurate, overly casual, or self-congratulatory wording and replace the latest weak commit message with a professional one.

Before changing anything:
1. run `git status`
2. confirm the current branch is `main`
3. record the current HEAD SHA
4. fetch origin
5. confirm `origin/main` points to the same current HEAD before planning any history rewrite
6. create a LOCAL backup branch at the current HEAD, for example:
   `backup/pre-professional-cleanup`
   Do not push that backup branch.

Save THIS EXACT prompt as:

prompts/project/006_professional_cleanup.md

Add it to `prompts/README.md` as the next development prompt, marked as saved when issued.

==================================================
1. FIX STALE H1 "UNSEEN" LANGUAGE
==================================================

Review:

src/hospital_1/evaluation.py

This file still contains historical wording such as:

- "created once, before development"
- "unseen measurement"

That conflicts with the current honest disclosure that the complete H1 labels had been visible during development and the holdout is therefore post-hoc.

Replace those phrases with neutral language such as:

- "created once and retained as a fixed development/holdout partition"
- "post-hoc holdout measurement"
- "frozen holdout result"

The meaning should be:

The split/freeze mechanism is technically real and useful for detecting later source changes, but the holdout must NOT be presented as independent/unseen validation because the full H1 labels had already been visible during development.

IMPORTANT:

Do NOT edit `src/hospital_1/audit.py` solely to clean up its old "unseen measurement" comment.

That file is part of the frozen H1 prediction source set. Changing a harmless historical comment there would change the freeze hash for no functional benefit.

Likewise, `outputs/hospital_1/generalization_report.md` is preserved historical material and already has a prominent banner explaining that its old "unseen" wording must be interpreted as post-hoc. Do not rewrite the historical body merely to sanitize terminology.

Regenerate `outputs/hospital_1/evaluation.md` after changing its generator.

==================================================
2. FIX H2 CLASSIFIER CALL TERMINOLOGY
==================================================

In:

src/hospital_2/semantic.py

the `run_classifier` docstring currently says wording equivalent to:

"each distinct normalised description costs one call"

That is inaccurate because one classification operation may require multiple HTTP attempts under bounded retries.

Replace it with wording equivalent to:

"One classification operation is performed per pending description cluster. Bounded retries may result in multiple HTTP requests for that cluster."

Keep the distinction clear:

- 69 semantic clusters required classification
- 69 classification operations were attempted
- retries resulted in 75 OpenRouter HTTP attempts
- 67 clusters produced valid classifier results
- 2 ultimately failed

IMPORTANT:

DO NOT edit:

prompts/hospital_2/001_service_classifier.md
prompts/hospital_2/003_jev_verifier.md

Those are active runtime prompt files and their hashes are part of the persisted semantic provenance.

If clarification is useful, add a short note to `prompts/README.md` explaining that "per cluster" refers to one classification operation and bounded retries may generate multiple HTTP requests.

Do not change the runtime prompt hashes.

==================================================
3. REMOVE THE "INDEPENDENT MODEL CONFIRMED" OVERCLAIM
==================================================

In:

outputs/hospital_2/decision_log.md

there is wording equivalent to:

"an independent model confirmed that 64 descriptions cannot be pinned to one service"

That overstates what was established.

Jev is a separate verifier, but we have not established statistical or methodological independence.

Replace it with precise wording such as:

"The semantic run was still informative: Jev, used as a separate verifier, accepted the classifier's 64 AMBIGUOUS decisions and did not accept any of the 3 MATCHED proposals."

Do not say:
- independent model
- independently confirmed
- proved the descriptions are ambiguous
- validated accuracy

There are no H2 labels.

==================================================
4. REMOVE SUBJECTIVE / CASUAL LANGUAGE FROM H1 DECISION LOG
==================================================

In:

outputs/hospital_1/decision_log.md

replace wording such as:

"It is the right trade: a confident wrong number propagates silently, an admitted gap costs a reviewer a few minutes."

with a neutral technical statement, for example:

"This intentionally sacrifices corrected-total coverage rather than asserting an amount that cannot be reconstructed from the available invoice evidence."

Keep the underlying policy exactly the same.

Do not change the actual daily-cap logic.

==================================================
5. CLEAN THE H1 EVALUATION REPORT WORDING
==================================================

In:

outputs/hospital_1/evaluation_report.md

replace wording equivalent to:

"the repository shows a clean procedure"

with something factual, for example:

"the repository records the following split and freeze procedure"

Do not characterize the process as clean, strong, rigorous, robust, or otherwise self-evaluate it.

Let the facts speak for themselves.

Keep the explicit post-hoc disclosure.

==================================================
6. TIGHTEN README WORDING
==================================================

Review README.md for unnecessarily casual or imprecise language.

Make these specific changes:

A.

Current idea:

"one hospital done carefully, with its uncertainty stated, was preferred over thin passes across four"

Use more professional wording such as:

"a focused implementation of one scored hospital, with explicit uncertainty, was preferred over shallow coverage of all four scored hospitals."

Do not imply our solution is objectively "better."

B.

Near the installation/testing section, wording currently says:

"No API key and no network access is needed."

Because `pip install -r requirements.txt` may itself require network access, make this precise:

"After dependencies are installed, the tests and normal reproduction workflow require no API keys or network access."

Keep the separate section documenting which optional semantic commands call paid APIs.

C.

Scan README.md for any other wording that sounds:
- overly casual
- self-congratulatory
- defensive
- like an unsupported quality judgment

Only change genuine issues. Do not rewrite the README unnecessarily.

==================================================
7. PRESERVE HISTORICAL PROMPTS
==================================================

Do NOT clean up informal wording inside the saved development prompts merely because it sounds casual.

The files under:

prompts/hospital_1/
prompts/hospital_2/
prompts/project/

are evidence of how the project was actually developed.

Where their headers say "Transcribed verbatim", preserve the transcript exactly.

You may edit prompt INDEX metadata in `prompts/README.md` if needed for factual clarification, but do not rewrite historical prompt bodies.

Do NOT modify active runtime prompt files because doing so would change their hashes and make the persisted semantic decisions stale.

==================================================
8. PROFESSIONALISM SCAN
==================================================

After making the specific edits above, scan reviewer-facing documentation and non-frozen comments/docstrings for similar problems.

Look for wording such as:

- unseen / untouched validation claims that contradict the post-hoc disclosure
- independent confirmation claims
- "costs one call" when retries exist
- "right trade"
- "obviously"
- "perfect"
- "clean procedure"
- "just"
- "simply"
- self-congratulatory statements
- defensive wording
- unsupported claims of correctness, robustness, accuracy, or independence

Use judgment.

Do NOT mechanically replace every occurrence of these words.

For example:
- "perfect" inside a factual metric discussion can be valid;
- "simply not payable" may be legitimate contract interpretation;
- historical prompt transcripts must remain verbatim;
- frozen source comments should not be changed merely for style.

Report any suspicious wording you deliberately leave unchanged and explain why.

==================================================
9. DO NOT BREAK THE H1 FREEZE OR H2 PROMPT PROVENANCE
==================================================

Before testing, verify that the cleanup has NOT modified:

- src/shared/*
- src/hospital_1/contract.py
- src/hospital_1/matcher.py
- src/hospital_1/audit.py
- prompts/hospital_2/001_service_classifier.md
- prompts/hospital_2/003_jev_verifier.md
- artifacts/hospital_2/service_mappings.json
- artifacts/hospital_2/unresolved.json

unless there is an actual functional bug, in which case STOP and report it instead of proceeding.

The objective is wording cleanup, not a new model version or new freeze.

==================================================
10. TEST AND VERIFY OUTPUT STABILITY
==================================================

Run:

python -m pytest -q

Expected current result:

414 passed

Also run the normal offline reproduction commands if necessary to regenerate documentation:

python -m src.main evaluate h1

Do NOT run:

python -m src.main semantic h2 classify
python -m src.main semantic h2 jev

Verify that these remain unchanged:

- outputs/hospital_1/predictions.csv
- outputs/hospital_2/predictions.csv
- outputs/hospital_2/submission.csv
- outputs/submission.csv
- artifacts/hospital_2/service_mappings.json
- artifacts/hospital_2/unresolved.json

Verify again that:

outputs/hospital_2/submission.csv

and:

outputs/submission.csv

are byte-identical.

If any prediction, metric, mapping decision, or submitted row changes, STOP and explain why.

==================================================
11. AMEND ONLY THE LATEST COMMIT MESSAGE
==================================================

The latest commit currently has the weak title:

"Make Hospital 1 and Hospital 2 submission-ready"

Replace that commit with a professional commit message.

DO NOT rewrite earlier commits.

The earlier commit titles:

- "Refactor Hospital 1 into per-hospital architecture"
- "Implement Hospital 2 deterministic audit with semantic/Jev workflow"
- the original exercise/package commit

are acceptable and should be left alone.

Use this new HEAD commit message:

Finalize H1 evaluation and H2 audit submission

- Record H2 semantic verification results while preserving unresolved service identities.
- Add hospital-specific and combined submission outputs with validation tests.
- Document H1 post-hoc evaluation limitations and the H2 stopping rationale.
- Harden Jev response parsing and bound classifier output tokens without rerunning semantic inference.
- Add versioned AI prompt provenance, concise decision logs, reproducibility guidance, and secret-handling checks.
- Standardize reviewer-facing terminology without changing predictions or metrics.

414 tests pass. H1/H2 predictions and scored submission rows are unchanged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

Include the professional-cleanup changes from this task in the amended commit.

Use `git commit --amend`, not a new cleanup commit.

==================================================
12. PUSH THE AMENDED HEAD SAFELY
==================================================

Because the old HEAD is already on GitHub, changing its message changes its SHA.

Before pushing:

- fetch origin again
- verify `origin/main` still equals the OLD HEAD recorded at the start
- verify no one else has pushed a new commit in the meantime

If it has changed, STOP. Do not overwrite someone else's work.

If it has not changed, push using:

git push --force-with-lease origin main

NEVER use plain `--force`.

Do not rewrite or force-update any earlier branch.

==================================================
13. FINAL REPORT
==================================================

After everything is complete, report:

1. old HEAD SHA
2. new amended HEAD SHA
3. exact new commit title
4. every file modified
5. every questionable phrase found during the professionalism scan
6. which questionable phrases were changed
7. which were deliberately preserved and why
8. confirmation that historical prompt transcripts were not rewritten
9. confirmation that active runtime prompt hashes did not change
10. confirmation that frozen H1 prediction files did not change
11. test result
12. whether H1 predictions changed
13. whether H2 predictions changed
14. whether H2 semantic decisions changed
15. whether either submission CSV changed
16. whether the hospital-specific and combined submission files are still byte-identical
17. git diff --stat for the amended commit
18. final git status
19. confirmation that origin/main now points to the new amended commit

Do not make any additional algorithmic improvements after this cleanup.
H1 and H2 are frozen after this task.
