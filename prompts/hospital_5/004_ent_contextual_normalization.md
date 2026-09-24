# Prompt 004 — Hospital 5: ENT contextual normalisation

- **Used with:** Claude Code (Claude Opus 5.5)
- **Kind:** development prompt to the coding assistant (not a runtime prompt; the code does not load this file)
- **Produced:** `artifacts/hospital_5/human_review_overrides.json` (the `ent → otolaryngologic` contextual reading, plus the existing `bd` rejection moved into it); `load_review_overrides`; the `h5-matcher-2` single-reading contextual fix; one new missing-word review; regression tests; decision log section H; regenerated H5 outputs and combined submission
- **Preceded by:** [`003_pre_commit_validation.md`](003_pre_commit_validation.md)
- **Provenance:** saved when issued (2026-09-24), copied verbatim from the session.

Transcribed verbatim below.

---

Do one final targeted H5 fix.

Do NOT restore the old single-candidate closure rule.

The goal is to recover the one defensible cluster where information is actually present, while leaving the genuinely missing-word clusters unresolved.

Current reviewed state:
- Old aggressive result: 1,032/1,050 totals proven
- After removing the questionable closure rule: 706/1,050 proven
- The lost coverage comes mainly from 4 clusters
- Only Cluster 2 has an actual unread discriminator token present in the text

==================================================
1. TARGET CLUSTER
==================================================

Focus on this cluster:

SUPV ENT SPCM ANLY

and its spelling variants.

Current interpretation:

SUPV -> supervised
SPCM -> specimen
ANLY -> analysis

The unresolved token is:

ENT

The H5 contract contains:

Supervised Otolaryngologic Specimen Analysis

The current system treats ENT as unread.

This is a normalization problem, not a true missing-word problem.

==================================================
2. ADD A REVIEWED CONTEXTUAL NORMALIZATION
==================================================

Add:

ENT -> otolaryngologic

but DO NOT make it a global unconditional normalization rule.

Treat it as a reviewed H5 contextual mapping.

Reason:

In this description structure:

SUPV ENT SPCM ANLY

ENT occupies the specialty slot.

The surrounding evidence gives:

qualifier:
supervised

specialty:
ENT

concept:
specimen analysis

The contract contains:

Supervised Otolaryngologic Specimen Analysis

So the specialty information is actually present in abbreviated form.

This is different from descriptions where the specialty is completely absent.

==================================================
3. PROVENANCE
==================================================

Store this as an explicit human-reviewed override/artifact.

Include something like:

{
  "token": "ent",
  "canonical": "otolaryngologic",
  "scope": "hospital_5_contextual",
  "status": "human_reviewed",
  "global": false,
  "reason": "ENT appears in the specialty slot of SUPV ENT SPCM ANLY and resolves to the H5 contract specialty Otolaryngologic."
}

Use the project's existing artifact/provenance conventions.

Do not silently hardcode it without documentation.

==================================================
4. DO NOT APPROVE THE OTHER THREE CLUSTERS
==================================================

Keep these unresolved:

SUPERVISED SPCM ANLY

Reason:
specialty is actually missing.

UROL HM VST

Reason:
qualifier is actually missing.

RTN PHYSIOTHERAPY SESS

Reason:
specialty is actually missing.

Do NOT restore a generic rule such as:

"if only one contract candidate remains, accept it."

That rule was already removed because it was post-hoc and too aggressive.

==================================================
5. RE-RUN MATCHING
==================================================

After adding the contextual ENT mapping:

- rerun H5 normalization
- rerun structural matching
- rerun missing-word resolution
- rerun pricing
- rerun global discount state
- rerun financial-equivalence logic
- regenerate H5 output
- regenerate combined submission

Measure the actual effect.

Expected direction:
the SUPV ENT SPCM ANLY cluster should now become deterministically identified as:

Supervised Otolaryngologic Specimen Analysis

Do not assume the final total count beforehand.

==================================================
6. CHECK FOR CIRCULARITY
==================================================

The resolution must use only:

- raw description text
- contextual normalization
- H5 contract vocabulary
- structural slot position

Do NOT use:

- billed unit price
- line total
- invoice total
- contract price compatibility
- another participant's solution

==================================================
7. TESTS
==================================================

Add regression tests covering:

1. ENT is NOT globally normalized everywhere.

2. In the contextual description:

SUPV ENT SPCM ANLY

ENT resolves to:

otolaryngologic

3. The full description resolves to:

Supervised Otolaryngologic Specimen Analysis

4. A different ambiguous use of ENT, if one can be constructed, must not automatically resolve unless context supports it.

5. These remain unresolved:

SUPERVISED SPCM ANLY

UROL HM VST

RTN PHYSIOTHERAPY SESS

6. The removed single-candidate closure behavior must remain removed.

==================================================
8. UPDATE DOCUMENTATION
==================================================

Update:

- outputs/hospital_5/decision_log.md
- outputs/hospital_5/semantic_contribution.md
- README.md / DECISION_LOG.md only if needed

Document clearly:

- why ENT is different from the other 3 clusters
- that this is a human-reviewed contextual normalization
- that no generic closure rule was restored
- exact coverage gained from this one change

==================================================
9. REGRESSION CHECK
==================================================

Run the full test suite.

Verify:

- all tests pass
- H1 unchanged
- H2 unchanged
- H4 unchanged
- previous H1/H2/H4 outputs remain byte-identical
- CRLF-safe diff check remains clean
- no commit is made

==================================================
10. FINAL REPORT
==================================================

Report only the important final numbers:

- how many lines the ENT cluster contains
- how many invoices it affects
- how many additional totals became proven
- new H5 correction_reconstructable count
- new blank total count
- new flagged count
- whether the other 3 clusters remain unresolved
- whether any downstream discount findings changed
- total tests passing
- confirmation H1/H2/H4 are unchanged
- confirmation nothing was committed

Most important:

Recover only information that is actually present.

ENT is present and readable with context.

The other three descriptions are genuinely missing a discriminator, so leave them unresolved.
