# Prompts

This folder holds every prompt behind this repository, in two kinds:

- **Development** prompts are what the human author sent to the coding
  assistant (Claude Code: Claude Opus 5, and Claude Opus 5.5 for Hospitals 3,
  4 and 5).
- **Runtime** prompts are what the code itself sends to a model when it runs.

All AI use is disclosed in the root [README](../README.md#ai-use-disclosure).

## Runtime prompts

Hospital 4 has no runtime prompt: its pipeline calls no model.

### Hospital 2 (loaded and sent by `src/hospital_2/semantic.py`)

The code reads these files directly. Each decision records the prompt version
as `<file>@<first 12 hex of the file's SHA-256>`. So editing a runtime prompt
file makes every earlier decision stale on the next `semantic h2 prepare`.
Edit these files only when the stage is meant to be rerun.

| file | model | status | notes |
|---|---|---|---|
| [hospital_2/001_service_classifier.md](hospital_2/001_service_classifier.md) | OpenRouter `z-ai/glm-5.3-flash` | **active** | Written once and never iterated. Every stored classifier result carries version `@2b8f543469fb`. It was run on 2026-09-22: 69 clusters, 75 HTTP attempts including retries, 67 valid results and 2 failures. |
| [hospital_2/002_jev_verifier.md](hospital_2/002_jev_verifier.md) | Jev | **superseded by 003** | A prose prompt with an invented question shape. **Never sent to Jev**, and no verdict came from it. Kept unchanged as history. |
| [hospital_2/003_jev_verifier.md](hospital_2/003_jev_verifier.md) | Jev `jev-1.13.0` (TypeSafe SystemOne) | **active** | Uses the Playground `type: choice` format. Its documentation section (not the question) was edited once, on 2026-09-22T00:38Z, after the live parser fix. All 67 stored verdicts carry that edited version, `@7a2a82a900a9`. The committed version was `@ec6778c60f4b`. |

### Hospital 3 (loaded and sent by `src/hospital_3/semantic.py`)

One file with one JSON block, handled exactly as for Hospital 5 below: one
request with one choice question per unresolved description cluster, every
answer stored with the SHA-256 of the exact request body, and `audit h3`
refusing to run on a missing or stale review. Hospital 3 has no normalisation
question: its vocabulary is derived deterministically.

| file | model | status | notes |
|---|---|---|---|
| [hospital_3/001_jev_missing_word_resolution.md](hospital_3/001_jev_missing_word_resolution.md) | Jev `jev-1.13.0` (TypeSafe SystemOne) | **active** | Question `missing_word_resolution`. The JSON block is Hospital 5's prompt 002 copied unchanged, so the question was not re-tuned; only the header differs. Written once and never iterated; all 38 stored reviews carry `@faedc45476cb`. The gates were fixed in code before the run. Run on 2026-09-25. |

### Hospital 5 (loaded and sent by `src/hospital_5/semantic.py`)

Each file holds one JSON block: the question and the static part of the state.
The code adds the dynamic evidence, sends **one request with one choice
question per judgment**, and stores every answer with the SHA-256 of the exact
request body, state and question. A review counts only while the request the
current code would send hashes the same. So editing a template makes every
stored review stale, and `audit h5` then refuses to run.

| file | model | status | notes |
|---|---|---|---|
| [hospital_5/001_jev_normalization_review.md](hospital_5/001_jev_normalization_review.md) | Jev `jev-1.13.0` (TypeSafe SystemOne) | **active** | Question `normalization_safety`: safe everywhere, context required, or unsafe. Written once and never iterated; all 266 stored reviews carry `@76841b2950a2`. Run on 2026-09-24. |
| [hospital_5/002_jev_missing_word_resolution.md](hospital_5/002_jev_missing_word_resolution.md) | Jev `jev-1.13.0` | **active** | Question `missing_word_resolution`: one option per contracted candidate, plus `ambiguous_contracted_service` and `none_of_the_above_or_unknown`. Written once and never iterated; the 35 cluster reviews and the 1 constructed probe carry `@b30cd06320e2`. Run on 2026-09-24. |

The templates were not changed after any answer was seen. A single-candidate
closure gate was added to the code (not to a prompt) after the first
missing-word run, and removed again in the pre-commit review (prompt 15).
See section G of [the Hospital 5 decision log](../outputs/hospital_5/decision_log.md).
In that review, the stored missing-word records were re-stamped without being
re-asked, because the lexicon hash left the fingerprint and every request body
was unchanged. After prompt 16 they were re-stamped once more for the
matcher version bump, again with identical request bodies. One new question
(`DISP INPT REN PHARM`) was asked, and three ENT questions are retired.

Where the runtime prompts or the code say "per cluster", that means one
classification operation per cluster. Bounded retries can turn one operation
into several HTTP requests: the 2026-09-22 run made 69 operations and 75
OpenRouter HTTP attempts.

## Development prompts, in the order sent

| # | file | hospital | what it produced | saved |
|---|---|---|---|---|
| 1 | [hospital_1/001_build_reference_engine.md](hospital_1/001_build_reference_engine.md) | H1 | the Hospital 1 engine, locked split, freeze, holdout | at the time |
| 2 | [hospital_1/002_refactor_by_hospital.md](hospital_1/002_refactor_by_hospital.md) | H1 | the `src/shared` + `src/hospital_1` layout (behaviour-preserving) | at the time |
| 3 | [project/001_cleanup_and_local_commit.md](project/001_cleanup_and_local_commit.md) | – | README wording fix, commit `bba8bec` (push cancelled) | recovered |
| 4 | [hospital_2/000_implement_hospital_2.md](hospital_2/000_implement_hospital_2.md) | H2 | the Hospital 2 implementation | at the time |
| 5 | [hospital_2/004_fix_review_issues.md](hospital_2/004_fix_review_issues.md) | H2 | count terminology, Jev choice format (002 → 003), duplicate rows, blank totals | recovered |
| 6 | [hospital_2/005_typesafe_and_unit_basis.md](hospital_2/005_typesafe_and_unit_basis.md) | H2 | TypeSafe endpoint default, unit-basis evidence rule | recovered |
| 7 | [project/002_env_setup.md](project/002_env_setup.md) | – | `.env` loading, `.env.example`, `.gitignore` | recovered |
| 8 | [project/003_operational_requests.md](project/003_operational_requests.md) | H2 | local commit `bc8f4b1`, API check, the 69-cluster classifier run | recovered |
| 9 | [hospital_2/006_jev_response_parser.md](hospital_2/006_jev_response_parser.md) | H2 | the Jev `answers` parser fix | recovered |
| 10 | [project/004_h1_h2_submission_readiness.md](project/004_h1_h2_submission_readiness.md) | both | this submission's files, reports, index and README | recovered |
| 11 | [project/005_documentation_cleanup.md](project/005_documentation_cleanup.md) | both | wording fixes for the H3–H5 scope, the H1 holdout and the H2 classifier attempts; a shorter root decision log | at the time |
| 12 | [project/006_professional_cleanup.md](project/006_professional_cleanup.md) | both | neutral wording in non-frozen code and reviewer-facing documents; amended HEAD commit message | at the time |
| 13 | [hospital_4/000_implement_hospital_4.md](hospital_4/000_implement_hospital_4.md) | H4 | the deterministic Hospital 4 implementation and its addition to the combined submission (Claude Opus 5.5) | at the time |
| 14 | [hospital_5/000_implement_hospital_5.md](hospital_5/000_implement_hospital_5.md) | H5 | the Hospital 5 implementation: Jev-reviewed normalisation and missing-word identity, financial equivalence, and its addition to the combined submission (Claude Opus 5.5) | at the time |
| 15 | [hospital_5/003_pre_commit_validation.md](hospital_5/003_pre_commit_validation.md) | H5 | the pre-commit review: single-candidate closure removed, the review-slot guard, the `bd` override and 2-letter invariant, and text-only justifications for exclusions, discounts, caps and facility (Claude Opus 5.5) | at the time |
| 16 | [hospital_5/004_ent_contextual_normalization.md](hospital_5/004_ent_contextual_normalization.md) | H5 | the human-reviewed contextual reading `ent → otolaryngologic` (in `artifacts/hospital_5/human_review_overrides.json`), the `h5-matcher-2` single-reading contextual fix, and one new missing-word question (Claude Opus 5.5) | at the time |
| 17 | [hospital_3/000_implement_hospital_3.md](hospital_3/000_implement_hospital_3.md) | H3 | the Hospital 3 implementation: the three-document contract with explicit precedence and the amendment by Service Date, the deterministic H3 vocabulary, the bounded Jev missing-word stage, sensitivity runs, and its addition to the combined submission (Claude Opus 5.5) | at the time |
| 18 | [hospital_3/002_h3_finalization_review.md](hospital_3/002_h3_finalization_review.md) | H3 | the H3 finalization review: the ENT reading reviewed and accepted as a human-reviewed contextual interpretation, independent checks of the Jev requests, the amendment, every proven total and blank, a fix to the per-unit discount alternative, and the updated write-up and PDF (Claude Opus 5.5) | at the time |

**"Recovered"** means the prompt was not written to the repository when it was
sent. On 2026-09-22 it was copied verbatim from the Claude Code session
transcript, and each such file says so in its header. Nothing was paraphrased
or reconstructed from memory. Message 8 groups five one-line requests that
were sent separately.

These eighteen files hold the author's task prompts from this project's Claude
Code session, which begins with message 1. Some short operational chat messages
were not saved as prompt files, and have not been added retroactively:
- after P005: pushing to the author's own repository, and deleting a local
  backup branch;
- between prompts 13 and 14: Hospital 4 follow-ups (the cap-policy test and
  note, reverting a prompt file, a count fix, line-ending hygiene), and
  committing and pushing. Any work done outside that session
is not recorded here and cannot be recovered. That includes the earlier
iteration behind the historical Hospital 1 figures mentioned in the
[evaluation report](../outputs/hospital_1/evaluation_report.md). The
assistant's own intermediate reasoning is not included.

## Convention from now on

Every new prompt is saved here when it is used:

- **Development prompts** go under the hospital they concern, or under
  `project/` if they concern more than one. Use the next free number and a
  header giving the model, what it produced, and what it followed.
- **A runtime prompt is never edited in place once results depend on it.**
  Add the next number, mark the old file superseded with a banner, and point
  the code at the new file.
