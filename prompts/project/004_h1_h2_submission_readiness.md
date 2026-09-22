# Prompt P004 — H1 + H2 submission readiness

- **Used with:** Claude Code (Claude Opus 5)
- **Produced:** the submission files, the H1 evaluation report, the prompt index, the README and the decision log for the submission. No classification or Jev calls were rerun
- **Provenance:** Recovered verbatim from the Claude Code session transcript on 2026-09-22 (it was not saved to the repository when it was issued).

Transcribed verbatim below.

---

We are stopping algorithmic work on Hospital 2 now.

Your task is NOT to improve H2 further, replace the LLM, find another free model, add new matching heuristics, or start Hospital 3/4/5.

Your task is to make the current Hospital 1 and Hospital 2 work SUBMISSION-READY according to the challenge README, while preserving the actual results and being completely honest about limitations.

DO NOT STOP AFTER ANALYSIS.
DO NOT ASK ME FOR APPROVAL BETWEEN PHASES.
Inspect the actual repository, make the necessary edits, run the tests/commands, regenerate artifacts where appropriate, and then give me a final readiness report.

IMPORTANT:
- Do not fabricate results.
- Do not fabricate historical prompts.
- Do not change H1/H2 predictions merely to make metrics look better.
- Do not use Hospital 1 labels inside prediction logic.
- Do not use billed price to infer service identity.
- Do not introduce a replacement LLM for the 2 failed H2 clusters.
- Do not start work on Hospitals 3, 4, or 5.
- Do not expose API keys or secrets.
- Do not silently delete useful historical artifacts.
- Preserve honest provenance.
- The existing H2 semantic results are a valid stopping point.

==================================================
0. FIRST: READ THE CHALLENGE AND SAVE THIS PROMPT
==================================================

Read the root challenge README carefully and extract the exact submission requirements and grading philosophy before changing anything.

Pay special attention to:
- complete coverage is NOT expected
- a strong partial solution is better than a thin full solution
- confidently wrong extraction is worse than uncertainty
- confidence/calibration matters
- 6–8 hours is a cap
- when work remains, document what would have been done next
- AI assistance must be disclosed
- prompts must be included as versioned files
- required H1 evaluation report
- required one-page decision log
- final submission.csv format

Also save THIS EXACT Claude Code task prompt as a versioned prompt file in the repository because this itself is AI assistance used to prepare the submission.

Choose a sensible next filename without overwriting existing prompt history, for example:

prompts/project/004_h1_h2_submission_readiness.md

or follow the repository's existing prompt naming convention if there is already one.

Do not pretend older prompts existed if they do not exist.

==================================================
1. AUDIT THE CURRENT REPOSITORY BEFORE EDITING
==================================================

Inspect the entire implementation relevant to:

Hospital 1:
- src/hospital_1/
- tests/hospital_1/
- H1 outputs/reports
- H1 evaluation
- any H1 decision log
- any H1 prompts
- shared code used by H1

Hospital 2:
- src/hospital_2/
- tests/hospital_2/
- artifacts/hospital_2/
- outputs/hospital_2/
- prompts related to H2
- H2 decision log/report
- shared code used by H2

Also inspect:
- README.md
- requirements / dependency pins
- .gitignore
- .env.example
- src/main.py
- submission generation
- current combined submission.csv
- repository git status
- current tests
- prompt history

Before making changes, answer internally:

1. What exactly is currently implemented?
2. What happened during H2 semantic classification?
3. What changed after GLM + Jev?
4. What did NOT change?
5. What artifacts are stale?
6. What deliverables required by the challenge are missing?
7. Are H1 and H2 reproducible from a fresh clone?
8. Are the prompts sufficient to satisfy the AI-assistance disclosure requirement?
9. Is any claim in README/reports stronger than the evidence supports?
10. Is any secret or API key accidentally tracked?

Then proceed with fixes.

==================================================
2. PRESERVE THE ACTUAL H2 STOPPING POINT
==================================================

The current H2 semantic run ended with these known results.

Verify all of these against the ACTUAL files before reporting them. If the repository differs, use the repository as source of truth and explain the difference.

Expected current state:

Contract/service identity:
- 441 total normalized description clusters
- 358 deterministic matched clusters
- 14 deterministic unknown clusters
- 69 clusters required semantic classification
- those 69 represent 2,093 line occurrences

GLM classification:
- model: z-ai/glm-5.3-flash through OpenRouter
- 67 / 69 returned valid classifier decisions
- 2 / 69 failed after bounded retries because OpenRouter returned HTTP 402 / insufficient credit
- we intentionally did NOT add OpenRouter credit
- we intentionally did NOT switch to a different model just for those 2 cases

Jev:
- model: jev-1.13.0
- Jev was run on all 67 successful classifier decisions
- 67 answered
- 64 classifier AMBIGUOUS decisions were accepted by Jev
- 0 semantic MATCHED decisions were accepted
- 5 semantic clusters remained pending/unverified total:
  - 2 classifier failures
  - 3 classifier MATCHED proposals that did not pass the Jev acceptance gate
- therefore semantic_verified_matched_clusters = 0
- semantic_verified_ambiguous_clusters = 64

Final H2 audit:
- 1,132 invoice occurrences
- 1,125 unique/final invoice rows
- 74 flagged rows
- 238 pricing_complete
- 237 correction_reconstructable
- 888 final expected_total_cents blank
- 14,360 line occurrences
- 12,253 deterministic matched line occurrences
- 0 semantic verified matched line occurrences
- 384 unit-basis tie-break line occurrences
- 27 provisional unit-basis line occurrences in pending semantic clusters
- 1,709 currently ambiguous line occurrences
- 14 currently unknown line occurrences

The important conclusion must be stated accurately:

The semantic stage did NOT increase verified service-mapping coverage.
Its useful outcome was largely confirmation that many unresolved descriptions should remain ambiguous rather than forcing a service identity.

Do NOT spin that as a successful increase in coverage.

==================================================
3. DOCUMENT WHY WE STOPPED H2
==================================================

Create or update the appropriate H2 report / decision log so a reviewer can understand why work stopped.

This must include a concise section similar in meaning to:

"Stopping decision"

Hospital 2 was intentionally frozen after the semantic verification stage.

67 of the 69 semantic clusters received valid GLM classifications.
The remaining two classifier requests failed after bounded retries when the external OpenRouter quota was exhausted.

We did not purchase additional credits or introduce a different model solely for those two descriptions.

This decision was deliberate for three reasons:

1. The challenge explicitly imposes a 6–8 hour cap and values documenting unfinished work over continuing indefinitely.
2. Changing model/provider for only two cases would introduce a new, unvalidated inference path and weaken consistency/provenance.
3. Unresolved uncertainty is preferable to forcing a mapping that could silently propagate an incorrect contract rate.

Jev subsequently evaluated all 67 successful classifier decisions.
It accepted 64 AMBIGUOUS decisions and accepted no classifier MATCHED decision at the configured threshold.

As a result, no new verified service identity was introduced by the semantic stage.

The remaining cases stay unresolved and their uncertainty propagates through pricing/reconstruction rather than being guessed.

With additional time/resources, the next steps would be:
- rerun only the two provider-failed clusters once validated provider access is available;
- investigate the three classifier MATCHED proposals that did not meet the Jev gate;
- benchmark any replacement/local model before allowing it to affect mappings;
- evaluate semantic matching against a labelled benchmark before claiming an accuracy improvement.

Write this naturally and based on actual repository evidence.

Also disclose that the GLM provider failure was quota/credit related.
Do not imply the model itself failed semantically in those two cases.

==================================================
4. HOSPITAL-SPECIFIC OUTPUT FILES + ONE COMBINED SUBMISSION
==================================================

I want BOTH:

A. hospital-specific outputs
B. one final combined submission.csv

Make the output organization explicit and clean.

Hospital-specific scored submission-style files should be something like:

outputs/hospital_2/submission.csv

Later we will also have:

outputs/hospital_3/submission.csv
outputs/hospital_4/submission.csv
outputs/hospital_5/submission.csv

Do NOT create fake H3-H5 files now.

For Hospital 1, because it is development/labelled and NOT scored, keep appropriate H1 files such as:

outputs/hospital_1/predictions.csv
outputs/hospital_1/evaluation_report.md

If a submission-format H1 diagnostic file is useful, clearly name it as DEV/diagnostic so it cannot be confused with the scored submission.

The root/final combined file must remain ONE:

outputs/submission.csv

or the repository's established final path.

The combined submission should include ONLY implemented SCORED hospitals.

At the current stopping point:
- H1 must NOT be included because it is development data.
- H2 should be included.
- H3-H5 must NOT be fabricated.

When later hospitals are implemented, the same command must combine their hospital-specific submission rows into the one final submission.csv.

Refactor the submission writer if necessary so there is ONE source of truth for row serialization and hospital-specific + combined outputs cannot silently disagree.

Add tests for this behavior.

==================================================
5. REVIEW H1 FOR SUBMISSION READINESS
==================================================

Hospital 1 is not scored, but its evaluation is a required deliverable.

Inspect the actual H1 implementation and regenerate the evaluation from code.

Known historical results that MUST be verified rather than blindly copied:

- 913 invoices
- 58 true erroneous invoices
- invoice-level binary results previously:
  TP 58
  FP 0
  TN 855
  FN 0

- expected-total exact match previously ~99.56%
- expected-total MAE previously ~333.5 cents
- four expected-total misses were associated with daily-cap corruption where the original pre-corruption quantity is not observable from the submitted invoice
- per-category detection was previously exact

Also verify the random split / temporal holdout discussion.

CRITICAL HONESTY REQUIREMENT:

The full H1 labels were seen during development.

Therefore random/temporal holdout measurements performed afterward are POST-HOC and are NOT untouched independent validation.

The final report must explicitly disclose this.

Do not describe them as true unseen test-set performance.

Review H1 for:
- label leakage
- invoice-specific exceptions
- hardcoded known bad invoice IDs
- hardcoded expected totals
- code paths that access labels during prediction
- accidental contamination of runtime logic

The evaluator may read labels AFTER predictions.
Prediction code may not.

Run tests proving this where possible.

==================================================
6. H1 REQUIRED EVALUATION REPORT
==================================================

Ensure there is a concise H1 evaluation report satisfying the challenge requirement:

"A short evaluation report giving per-category performance on the hospital 1 development set, and an error analysis grouped by failure type."

The report should contain:

- invoice-level metrics
- per-category metrics
- expected-total reconstruction metrics
- confidence/calibration discussion
- failure analysis
- contamination/validation limitation
- representative examples where appropriate

Do NOT simply list four individual failed invoice IDs.

Group errors by SYSTEMATIC FAILURE TYPE.

If the actual H1 results contain fewer than 3–4 observed failure types, DO NOT invent errors to satisfy the wording.

Instead clearly separate:

Observed failures
from
Known methodological / model limitations

For example, if supported by the actual evidence:

Observed:
- unrecoverable pre-corruption quantity after a daily-cap violation

Methodological limitations:
- post-hoc holdout because labels were already visible during development
- confidence score is an evidence-strength heuristic, not a statistically calibrated probability
- free-text service identity could fail on unseen abbreviations/semantics

Only include limitations supported by the implementation/data.

==================================================
7. REVIEW H2 FOR SUBMISSION READINESS
==================================================

Perform a final H2 integrity review.

Verify:

- no H2 hidden labels exist or are used
- no competitor solution/mapping is used as ground truth
- billed unit price is never used to select service identity
- billed invoice total is never used to select service identity
- patient/invoice IDs are not exposed to the LLM for identity inference unless genuinely required
- unit-basis anti-circularity remains intact:
  if billed unit basis was used to disambiguate identity, the same evidence must not later be used to accuse the line of wrong_unit_basis
- duplicate invoice IDs are handled occurrence-safely
- one-row-per-invoice-ID final serialization is deterministic and documented
- earlier duplicate occurrences do not contaminate the selected final occurrence's unrelated findings
- unresolved semantic identity propagates conservatively through bundle, discount, cap, exclusion and reconstruction logic
- expected_total_cents is blank when correction is not reconstructable
- blank means blank, not "None", "null", or "NaN"
- confidence is described as evidence strength unless genuinely calibrated
- pricing uses integer cents / deterministic HALF_UP rules
- offline audit works from persisted mappings/artifacts without needing live API calls

Regenerate all H2 outputs from the persisted semantic state.

The final audit should NOT automatically rerun OpenRouter or Jev.

==================================================
8. PROMPTS / AI ASSISTANCE AUDIT — VERY IMPORTANT
==================================================

The challenge explicitly requires prompts as versioned files.

Audit this carefully.

Inspect every current prompt file.

For each one determine:
- what it was used for
- whether it is runtime inference or development assistance
- model/provider if known
- whether it is current or superseded
- whether its filename/versioning makes that clear

At minimum, H2 currently has historical semantic prompt versions around:
- classifier
- previous verifier iteration(s)
- current Jev verifier
- this submission-readiness Claude Code prompt

Do not delete superseded prompts if they show genuine iteration.
Mark superseded versions clearly.

Make sure the current runtime artifact records the exact relevant prompt version/hash where applicable.

IMPORTANT:
The README's phrase "prompts as versioned files" is broader than simply runtime LLM prompts.

Review whether AI was used to DEVELOP H1/H2 code and whether those real prompts are available.

If real historical development prompts exist anywhere in the repo/history/project, preserve and organize them.

If historical development prompts do NOT exist, DO NOT reconstruct fake prompts and present them as historical.

Instead:
- disclose that only retained/versioned prompts are included;
- save all prompts used from this point onward;
- make the AI-assistance disclosure honest about what is and is not retained.

Create a short prompt index, for example:

prompts/README.md

It should explain:
- filename
- version/order
- purpose
- model/system if relevant
- active/superseded
- whether it affects runtime predictions or only development/review
- relevant artifacts produced

Do not include API keys.

==================================================
9. README / REPRODUCIBILITY
==================================================

Make the repository README submission-ready.

A reviewer should be able to clone the repo and understand:

1. What was implemented
2. What was intentionally not implemented
3. How H1 was used
4. Why H2 was selected
5. How H2 works
6. Where uncertainty is intentionally preserved
7. Where AI was used
8. Why H2 work stopped
9. How to install dependencies
10. How to run tests
11. How to reproduce H1 evaluation
12. How to reproduce H2 audit from committed artifacts
13. How to generate hospital-specific files
14. How to generate the combined submission
15. Which commands require external API credentials
16. Which normal grading/reproduction commands do NOT require external API credentials

The normal reproduction path should use committed/persisted semantic mappings and SHOULD NOT require spending money on external APIs.

Live semantic regeneration can be documented as optional.

Clearly state current scope:

Implemented:
- Hospital 1 development/evaluation
- Hospital 2 scored auditing

Not implemented yet:
- Hospitals 3–5

Do not apologize for partial coverage.
Explain the sequencing decision using the challenge's own emphasis on depth, uncertainty and time budget.

==================================================
10. DEPENDENCIES / ENVIRONMENT / SECRETS
==================================================

Check:

- dependencies are pinned
- python-dotenv behavior is documented
- .env is ignored
- .env.example contains placeholders only
- no real OPENROUTER_API_KEY is committed
- no real TYPESAFE_API_KEY / JEV_API_KEY is committed
- no generated artifact accidentally contains API keys
- no terminal dump containing secrets is committed

The semantic configuration should not require absurd output-token limits.

If classifier max_tokens is currently set to something like 131072 for a tiny JSON response, reduce it to a reasonable bounded value suitable for the schema and add/test the configuration.

However:
- DO NOT rerun H2 classification afterward
- DO NOT alter the already persisted semantic decisions
- document this as a runtime/configuration cleanup if changed

==================================================
11. DECISION LOG
==================================================

Ensure there is a concise one-page decision log satisfying the challenge.

It should cover actual consequential decisions, including:

H1:
- interpretation decisions
- H1 label use and contamination limitation
- any non-reconstructable monetary case

H2:
- JSONL/occurrence-safe duplicate handling
- /SA-#### stripping as nonsemantic noise
- unit-basis identity tie-break policy
- prohibition on billed-price identity matching
- semantic escalation policy
- GLM + Jev separation
- Jev >= 0.90 acceptance/rejection gating
- accepted ambiguity remains ambiguity
- unresolved state propagation
- detection vs reconstructability
- expected total withholding policy
- OpenRouter quota stopping decision
- why no replacement model was introduced

Keep it concise enough to genuinely be "one-page" in spirit.

If hospital-specific decision logs are retained for engineering detail, also provide the concise challenge-facing decision log.

==================================================
12. TEST EVERYTHING
==================================================

Run the complete test suite.

Also run the actual public/reviewer workflow from the README.

At minimum verify equivalent commands for:

H1 evaluation
H2 semantic status
H2 audit
combined submission generation

Do NOT rerun paid classification calls.

Verify byte/content consistency where appropriate.

Check:
- H1 predictions unchanged unless fixing a serialization-only bug
- H2 74 flagged rows remain unless a genuine bug discovered
- H2 1,125 final invoice rows
- H2 hospital-specific submission matches the H2 subset of combined submission exactly
- combined submission schema matches submission_template.csv exactly
- invoice IDs unique within final submission
- money columns contain integer cents or valid blanks only
- confidence values are within [0,1]
- no extra index columns
- no NaN/null strings
- deterministic output ordering
- all tests pass

If anything changes a prediction or metric, STOP treating it as formatting.
Investigate it and explain exactly why it changed.

==================================================
13. DO NOT START H3/H4/H5
==================================================

This task ends after H1 + H2 are submission-ready.

Do NOT implement H3.
Do NOT implement H4.
Do NOT implement H5.

It is fine for the combined submission to currently contain only H2 because the challenge explicitly allows partial coverage.

==================================================
14. FINAL RESPONSE — ANSWER THESE QUESTIONS
==================================================

After doing the work, give me a concise but complete report answering these exact questions:

A. WHAT HAPPENED?
1. What happened during the H2 GLM run?
2. Why did 2 classifier clusters fail?
3. What did Jev do with the 67 successful classifier decisions?
4. Why are 64 clusters still ambiguous even after Jev?
5. What happened to the 3 classifier MATCHED proposals?
6. Did the semantic stage add any verified service mappings?
7. Did the H2 final audit numbers change from the deterministic baseline?

B. WHY DID WE STOP?
8. Why did we stop H2 instead of adding OpenRouter credits?
9. Why did we not introduce another free model for the final 2 cases?
10. What would we do next if more time/resources were allowed?

C. H1 READINESS
11. Is H1 prediction logic label-independent?
12. What are the final H1 metrics?
13. Is the H1 evaluation report compliant with the challenge?
14. Is the post-hoc/label-contamination limitation clearly disclosed?
15. Are H1 failure types documented honestly?

D. H2 READINESS
16. Is H2 now frozen and reproducible?
17. What are the final H2 counts?
18. Are uncertainty and non-reconstructable totals handled correctly?
19. Does offline reproduction work without OpenRouter/Jev calls?
20. Are the remaining unresolved clusters clearly documented?

E. PROMPTS
21. List every prompt file now in the repo.
22. Which are active?
23. Which are superseded?
24. Which were used for runtime semantic inference?
25. Which were used only for development/review?
26. Are we fully honest about any historical prompts that were not retained?
27. Does the prompt structure now satisfy the challenge requirement as well as possible without fabricating history?

F. SUBMISSION FILES
28. What hospital-specific output files now exist?
29. Does Hospital 2 have its own submission file?
30. Does the root/final submission.csv contain the exact same H2 rows?
31. Is H1 correctly excluded from the scored combined submission?
32. Are H3-H5 correctly absent rather than fabricated?

G. REPRODUCIBILITY / QUALITY
33. What exact commands should the reviewer run?
34. Do they require paid APIs?
35. How many tests pass?
36. Did any tests fail?
37. Did any prediction change during this cleanup?
38. Did any metric change?
39. Are dependencies pinned?
40. Are secrets protected?

H. FILES CHANGED
41. Give me a concise list of every file created/modified and why.
42. Give me the final git diff summary.
43. Give me git status.
44. Tell me whether H1 and H2 are now SUBMISSION-READY: YES or NO individually.
45. If NO for either, state the exact blocker.

Do all work first, then answer these questions.

The goal is NOT to make the project look perfect.

The goal is to make it:
- reproducible
- defensible
- conservative where evidence is weak
- honest about AI usage
- honest about validation limitations
- compliant with the challenge deliverables
- ready for a reviewer to inspect without us having to explain missing context verbally.
