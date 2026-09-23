# Decision log

A one-page summary. The full reasoning is in the per-hospital logs:
[Hospital 1](outputs/hospital_1/decision_log.md),
[Hospital 2](outputs/hospital_2/decision_log.md) and
[Hospital 4](outputs/hospital_4/decision_log.md).

## Scope

- **H1 is for development and evaluation only.** It is labelled and not
  scored, so it is not in `submission.csv`.
- **H2 and H4 are the scored hospitals submitted.**
- **H3 and H5 were not implemented** and were not used to produce submitted
  predictions.
- **Python decides every number, in integer cents.** An LLM decides only H2
  service identity, never a price, total or flag. H4 uses no model.
- **Confidence is evidence strength, not a calibrated probability.**

## Clause readings

| question | reading taken |
|---|---|
| daily cap exceeded | H1, H2: detect, leave the total blank, report the cap as a ceiling. H4 (clause 6.1: the excess "is not payable"): price the limit. The H4 reading is flagged as open, because H1's labels used pre-breach quantities. |
| "within N days" | Inclusive. Chosen on H1 dev data; H4's data is consistent with it. |
| exclusion scope | Same patient only. |
| unknown service | H1: the billed amount is carried and marked non-reconstructable. H2, H4: no amount, so the total is blank. |
| date out of term and after the invoice | One defect: out of term. |
| repeat of a service on the same day | The first billing stands (by invoice date, id, line); later ones pay nothing. |

## Hospital 1

- **Labels were visible during development.** The holdout is reported only as
  a **post-hoc check**, not as untouched validation.
- **The prediction path never reads labels.** Tests enforce this.
- **Results, all 913 invoices:** detection TP 58 / FP 0 / TN 855 / FN 0.
  908 of 909 offered totals are exact. 4 cap-breach totals were declined.

## Hospital 2

- **Occurrences are kept separate.** The JSONL is canonical, and the row for
  a reused number represents the later occurrence.
- **`/SA-####` suffixes are stripped.** The billed price never identifies a
  service. The unit basis may break a genuine textual tie, and a basis used
  that way cannot also accuse the line.
- **Semantic stage.** GLM proposes, Jev verifies, and a 0.90 gate decides;
  an accepted AMBIGUOUS stays AMBIGUOUS.
  - 69 clusters needed classification: 75 OpenRouter HTTP attempts with
    retries, 67 valid results, 2 failures from provider credit (HTTP 402).
  - Jev accepted the 64 AMBIGUOUS decisions and none of the 3 MATCHED.
  - **The stage added 0 verified mappings.**
- **Stopped on purpose.** No credits were added and no unvalidated
  replacement model was used. 888 of 1,125 totals are blank, where the
  correction cannot be reconstructed.

## Hospital 4

- **Deterministic, with no LLM.** Every rule is parsed from the contract: 98
  services, 18 premiums, 18 limits, 7 bundles, 4 discount tiers and 15
  exclusions.
- **Matching is structural.** The same data conventions apply as in H2:
  occurrences kept separate, `/CW-####` stripped, price never used.
  - MATCHED needs the qualifier, the specialty and a concept word, and exactly
    one consistent service.
  - A description missing a slot stays AMBIGUOUS: 822 of 10,560 lines.
- **Unresolved lines are carried as intervals.** They widen Service Day
  aggregates and cumulative utilisation. A threshold is decided only when the
  whole interval is on one side of it.
- **Results.** 835 rows, 63 flagged. 307 totals are reconstructable and 528
  are blank.

## Stopping decisions and next steps

- **H2:** retry the 2 failed clusters once credit is restored, and have a
  person review the 3 unaccepted matches.
- **H2 and H4:** a reviewed evidence table for descriptions missing a name
  slot would raise coverage most.
- **H4:** decide the cap reading (one policy switch).
- **Then** Hospitals 3 and 5.
