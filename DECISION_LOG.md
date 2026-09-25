# Decision log

A one-page summary of the assumptions, the ambiguities and what was decided.
The full reasoning, with every alternative, is in the per-hospital logs:
[Hospital 1](outputs/hospital_1/decision_log.md),
[Hospital 2](outputs/hospital_2/decision_log.md),
[Hospital 3](outputs/hospital_3/decision_log.md),
[Hospital 4](outputs/hospital_4/decision_log.md) and
[Hospital 5](outputs/hospital_5/decision_log.md).

## Scope and principles

- **H1 is for development and evaluation only.** It is labelled and not
  scored, so it is not in `submission.csv`.
- **H2, H3, H4 and H5 are submitted.** H3 was implemented last.
- **Python decides every number, in integer cents.** Models are used only for
  service identity: an LLM plus Jev in H2, and Jev alone in H3 and H5. No model sets
  a price, total or flag. H4 uses no model.
- **The billed price never identifies a service.** The unit basis may break a
  genuine tie between contracted services. A basis used that way is never
  then called wrong.
- **A blank total beats a guessed one.** `expected_total_cents` is filled
  only when the contract and the service identity determine it.
- **Confidence is evidence strength, not a calibrated probability.**

## Clause readings

| question | reading taken |
|---|---|
| daily cap exceeded | H1, H2, H3, H5 (a bare cap with no stated consequence): flag it, leave the total blank, report the cap as a ceiling. H4 says the excess "is not payable", so there the limit is priced. |
| "within N days of" (exclusions) | Day N is inside, and only the left-hand service loses payment. For H3 and H5 the wording states a distance, so both sides of the trigger count. **This is the least certain reading;** all 3 H3 and all 6 H5 exclusion findings depend on it. |
| amendment (H3) | Priced by Service Date, never invoice date (A1.1.2). A service the amendment adds, found on an earlier date, is identified and flagged `service_not_contracted_on_date`, paid 0; `unknown_service` stays for text that names nothing contracted. |
| discount crossing a threshold (H3, H5) | A line takes the tier already exceeded before it (3.1: one unit rate per line). The per-unit reading is implemented as a switch. |
| facility (H5) | Clause 1.2 names the line item, but lines carry no facility field, so the invoice's facility is used. |
| unknown service | H1 carries the billed amount (non-reconstructable). H2 to H5: no contract price, so the total is blank. |
| reused invoice number | Occurrences are kept separate; the row represents the later one. |
| same service twice on one day | The first billing stands (invoice date, id, line); later ones pay nothing. |
| date out of term and after the invoice | One defect, reported as out of term. |

## Hospital 1

- **Labels were visible during development,** so every H1 figure is post-hoc,
  not untouched validation. The prediction path never reads labels; tests
  enforce this.
- **All 913 invoices:** TP 58 / FP 0 / TN 855 / FN 0. 908 of 909 offered
  totals are exact, and 4 cap-breach totals were declined.

## Hospital 2

- **Semantic stage:** GLM proposes, Jev verifies, and a 0.90 gate decides. Of
  69 clusters, 67 gave valid results and 2 failed on provider credit. **The
  stage added 0 verified mappings.**
- **Stopped on purpose,** with no unvalidated replacement model. 888 of 1,125
  totals are blank.

## Hospital 3

- **Three documents, explicit precedence:** Amendment > Appendix B > Base
  Agreement. Seven rates change, and two services are added, from 1 January
  2025 by Service Date.
- **Vocabulary is derived from H3's own contract words** by letter rules and
  checked against H3's own descriptions; no other hospital's table is used.
  `ent` → otolaryngologic is a human-reviewed reading, used in context only.
  **319 totals depend on it.**
- **Jev, only because it was measured to matter:** before review, lines
  missing one name slot blocked 501 of 939 invoice records. 38 bounded
  questions, with H5's gates fixed in advance, gave 34 services, 3 closed ties
  and 1 unresolved (P = 0.89).
- **Results:** 932 rows, 70 flagged, 848 totals proven, 84 blank. Each
  alternative reading's effect is measured in `semantic_contribution.md`.

## Hospital 4

- **Deterministic, with no model.** A description missing a name slot stays
  ambiguous (822 of 10,560 lines). Unresolved lines widen shared aggregates
  as intervals.
- **Results:** 835 rows, 63 flagged, 307 totals proven.

## Hospital 5

- **Normalisation:** 266 proposed abbreviation readings, one Jev question
  each, then fixed gates: 62 global, 45 context-only, 159 rejected.
  Normalisation may never add a qualifier or specialty.
- **Missing words:** 33 clusters get one bounded Jev choice each. A review
  may supply at most one missing name slot.
- **Removed in review:** a single-candidate closure rule added after the
  first run. It read Jev's hedging as confirmation.
- **Human-reviewed overrides, recorded with reasons:**
  - ENT is read as Otolaryngologic only where a contracted service fits.
    233 totals depend on it.
  - `bd → bedside` is rejected.
- **Left unresolved** because the text omits a discriminator:
  `SUPERVISED SPCM ANLY`, `UROL HM VST`, `RTN PHYSIOTHERAPY SESS`.
- **Results:** 1,050 rows, 76 flagged, 939 totals proven, 111 blank.

## Next steps

- **Human sign-off** on the remaining ambiguous clusters (H2 to H5), recorded
  with provenance.
- **Sensitivity runs** for the exclusion, discount and cap readings in H4 and
  H5, as H3 now has.
- **Confidence calibration** against a properly held-out set.
- **H2:** retry the 2 failed clusters.
