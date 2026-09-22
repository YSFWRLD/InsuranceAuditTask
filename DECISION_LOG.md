# Decision log

A one-page summary. The full reasoning is in the per-hospital logs:
[Hospital 1](outputs/hospital_1/decision_log.md) and
[Hospital 2](outputs/hospital_2/decision_log.md).

## Scope

- **H1 is for development and evaluation only.** It is labelled and not
  scored, so it is not in `submission.csv`.
- **H2 was selected as the scored hospital.** It is the only hospital in the
  submission.
- **H3–H5 were not implemented** and were not used to produce submitted
  predictions.
- **Python decides every number, in integer cents.** An LLM decides only H2
  service identity, never a price, total or flag.
- **Confidence is evidence strength, not a calibrated probability.**

## Clause readings

| question | reading taken |
|---|---|
| daily cap exceeded | Detect it, but leave the corrected total blank. The delivered quantity cannot be observed, and the cap is reported as a ceiling. |
| "within N days" | Inclusive. Chosen on H1 dev data; applied to H2 as a reading. |
| exclusion scope (H1) | Same patient only. |
| unknown service | H1: the billed amount is carried and marked non-reconstructable. H2: it gets no amount, so the total is blank. |
| date out of term and after the invoice | One defect: out of term. |
| H2 Service Day 07:00–06:59 | The calendar date, because invoices carry no times. |
| H2 "per hour, per item" | A unit basis of its own. |

## Hospital 1

- **Labels were visible during development.** The locked split was created
  before the final engine implementation. Even so, the holdout is reported
  **only as a post-hoc check**, not as untouched validation.
- **The prediction path never reads labels.** Tests enforce this, including a
  test that hides every label file.
- **Results, all 913 invoices:**
  - detection TP 58 / FP 0 / TN 855 / FN 0;
  - 908 of 909 offered totals exact;
  - 4 cap-breach totals declined, because they are detectable but not
    reconstructable.
- **Earlier figures are not reproduced.** An earlier iteration reported
  99.56% exact and 333.5 cents MAE by pricing those 4 totals at the cap.
- **One matcher defect was left unfixed.** A description missing its
  specialty word matched confidently. H2's matcher avoids this by design.

## Hospital 2

- **Duplicate invoice ids.** The JSONL is canonical, and each invoice record
  is audited separately. The one submitted row per number represents the later
  occurrence.
- **`/SA-####` suffixes** are stripped as billing noise.
- **Identity.** The billed price is never used to identify a service. A match
  needs its qualifier and specialty in the text.
  - The billed unit basis may break a genuine textual tie. A basis used for
    identity can never also support `wrong_unit_basis`.
- **Unresolved descriptions stay unresolved.** Such a line is carried as a set
  of candidate services and flagged only if it is wrong under every reading.
- **Semantic escalation.**
  1. The GLM classifier proposes an identity.
  2. Jev verifies the proposal.
  3. Python applies the 0.90 gate.

  An accepted AMBIGUOUS stays AMBIGUOUS.
- **What the semantic run did.** 69 clusters required classification.
  - Bounded retries made that 75 OpenRouter HTTP attempts.
  - 67 clusters gave valid results; 2 failed with a provider credit error
    (HTTP 402).
  - Jev accepted all 64 AMBIGUOUS decisions and none of the 3 MATCHED.
  - **The semantic stage added 0 verified service mappings.** The submitted
    rows are unchanged by it.
- **Expected totals are blank when the correction cannot be reconstructed**
  (888 of 1,125 rows; 74 flagged).

## Stopping decision

- **H2 work was stopped on purpose.** We did not add credits, because the stage
  had produced 0 verified matches and the 2 failed clusters cover 55 lines.
  We did not introduce an unvalidated replacement model, because that would
  mix classifiers and tune after seeing results.
- **Request size is now bounded, not rerun.** The requests had no
  `max_tokens` limit; a limit of 4096 now exists and is tested.
- **Next steps:**
  1. retry the 2 clusters once credit is restored;
  2. have a person review the 3 unaccepted matches;
  3. build a reviewed qualifier/specialty table for the clusters that lack
     one;
  4. then Hospitals 3–5.
