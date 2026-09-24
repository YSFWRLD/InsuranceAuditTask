# Decision log

A one-page summary. The full reasoning is in the per-hospital logs:
[Hospital 1](outputs/hospital_1/decision_log.md),
[Hospital 2](outputs/hospital_2/decision_log.md),
[Hospital 4](outputs/hospital_4/decision_log.md) and
[Hospital 5](outputs/hospital_5/decision_log.md).

## Scope

- **H1 is for development and evaluation only.** It is labelled and not
  scored, so it is not in `submission.csv`.
- **H2, H4 and H5 are the scored hospitals submitted.**
- **H3 was not implemented** and was not used to produce submitted
  predictions.
- **Python decides every number, in integer cents.** An LLM decides only H2
  service identity, never a price, total or flag. H4 uses no model. H5 uses
  Jev only as a bounded reviewer of vocabulary and missing-word identity.
  Its audit reads the stored reviews and calls nothing.
- **Confidence is evidence strength, not a calibrated probability.**

## Clause readings

| question | reading taken |
|---|---|
| daily cap exceeded | H1, H2, H5 (a bare "Daily cap" column with no stated consequence): detect, leave the total blank, report the cap as a ceiling. H4 (clause 6.1: the excess "is not payable"): price the limit. This is the chosen H4 policy; the alternative is documented because H1's labelled cases used pre-breach quantities. |
| "within N days" | Inclusive. Chosen on H1 dev data; H4's data is consistent with it. For H5, from the text alone: "not billable within N days of" states a distance, so both sides of the trigger count and day N is inside. All 6 H5 exclusion findings depend on that reading. |
| exclusion scope | Same patient only; only the left-hand service loses payment. |
| unknown service | H1: the billed amount is carried and marked non-reconstructable. H2, H4, H5: no amount, so the total is blank. |
| H5 discount crossing a threshold | Genuinely ambiguous. Adopted: a line takes the tier already exceeded before it, the only reading that contradicts no sentence (3.1: one unit rate per line). The per-unit reading ("subsequent units") is implemented as a switch. |
| H5 facility column | Clause 1.2 names the line item, but the data only records the invoice's facility, so the invoice's facility is used. |
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

## Hospital 5

- **Contract parsed strictly:** 84 services, 3 facilities × 3 tiers of
  multipliers, 9 caps, 10 premiums, 9 weekend uplifts, 3 bundles, 15 discount
  tiers and 7 exclusions. It is cross-checked against the .txt rendering.
- **Normalisation is proposed broadly and reviewed by Jev.** Of 266
  proposals, gates admit 62 as global and 45 as context-dependent, and reject
  159. Two of these decisions are recorded human-review overrides. Invariants stop a normalisation from adding a qualifier or specialty.
  Context-dependent tokens are settled by the surrounding words.
- **Missing words.** 33 clusters (1,042 lines) are asked one bounded choice
  each: 28 resolved to a service, 1 closed to its two candidates, 4 left
  unresolved (the 3 missing-word clusters below, plus one naming only a
  concept). Jev refused to invent a specialty in the constructed stress
  test.
- **Pre-commit review.** A single-candidate closure gate, added after the
  first run, was removed, and it was not restored later. In 4 clusters Jev chose the only candidate at
  0.80-0.89 and put the rest on "ambiguous"; the gate had counted that hedge
  as confirmation. It had lifted proven totals from 706 to 1,032.
  - A review may now supply at most one missing name slot.
  - A known reviewer error (`bd → bedside`) is overridden with provenance,
    and 2-letter tokens are never global.
- **ENT.** Of those four clusters, only `SUPV ENT SPCM ANLY` carries its
  discriminator. ENT (otolaryngology) sits in the specialty slot. A
  human-reviewed, context-only reading with provenance resolves it, and
  recovers 233 totals. The other three stay unresolved.
- **Financial equivalence** prices every still-possible reading. It added 3
  totals: surviving candidates never priced identically.
- **Results.** 1,050 rows, 76 flagged, 939 totals proven. The 111 blanks are
  mostly invoices with a line whose service is not settled.

## Stopping decisions and next steps

- **H2:** retry the 2 failed clusters once credit is restored, and have a
  person review the 3 unaccepted matches.
- **H2 and H4:** a reviewed evidence table for descriptions missing a name
  slot would raise coverage most.
- **H4:** cap reading chosen; the alternative remains documented as a policy switch.
- **H5:** the 3 remaining single-candidate clusters genuinely omit a word, so
  only a recorded human sign-off could resolve them. No rule does.
- **Then** Hospital 3.
