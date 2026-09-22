# Hospital 1 — generalization report

> **Validation status (added 2026-09-22).** This report was written at the
> freeze and is kept as history. The project owner reports that the full
> Hospital 1 labels were visible during development of this project, and the
> holdout has been printed by every `evaluate h1` run since. So where this
> report calls the holdout "unseen", read it as a **post-hoc check, not an
> independent test-set measurement**. The same applies to the temporal study.
> See [`evaluation_report.md`](evaluation_report.md).

Implementation frozen at `ab7bc8040ab8fe43` before the holdout was read.
183 tests passed at that freeze. 913 invoices audited, 58 flagged.

*Afterwards* the code was reorganised from 13 flat modules into `src/shared/`
and `src/hospital_1/` with no change in behaviour: `predictions.csv` is
byte-identical before and after, and `artifacts/hospital_1/freeze.json`
records both freezes with the same `predictions_sha256`. Paths below refer to
the reorganised layout. The suite now has 190 tests; see §8.

---

## 1. Did the predictor ever read labels?

**No, and it cannot.**

The dependency runs one way. `src/hospital_1/evaluation.py` imports the
prediction path; nothing in the prediction path imports it. The prediction
modules — `shared/{models,data,money,submission}.py` and
`hospital_1/{contract,matcher,audit}.py` — contain no path to `labels/` or
`artifacts/hospital_1/split/`.

Tests in `tests/hospital_1/test_evaluation.py` enforce this mechanically:

- no prediction module names a label file, column or directory (checked against
  source with comments and string literals stripped, so the check is about
  executable code, not prose);
- no prediction module imports the evaluator;
- **`test_results_do_not_depend_on_which_labels_exist`** renames every label
  file away (the master file and both halves of the split), re-runs the whole
  pipeline, and asserts that not one of the 913
  `(flagged, categories, expected_total)` triples changes;
- **`test_the_prediction_module_list_is_complete`** fails if a new source file
  appears on the prediction path without being added to the guarded list.

The split itself was created (now `create_split` in `evaluation.py`) before any development work,
from a fixed seed over sorted invoice ids, and the script prints only counts
and checksums — never label contents.

## 2. Is there hardcoding?

**No.** Searched for and asserted against, per module, in code (not comments):

| searched for | result |
|---|---|
| invoice ids (`INV-H1-…`) | none |
| patient ids (`PT-H1-…`) | none |
| line ids (`H1-L…`) | none |
| the contract number literal | none — it is parsed from the Agreement |
| any of the 108 Hospital 1 service names | none — all parsed from Section 4 |
| any known erroneous total | none |
| `hospital_2` … `hospital_5` | none; Hospital 2–5 data is not used by the Hospital 1 predictor, matcher, pricing engine, or evaluation logic |

Every rate, cap, threshold, uplift, discount tier, bundle and exclusion window
is read out of `provider_services_agreement.md` at run time. Deleting a row
from the contract changes the audit; editing the engine does not change what
the contract says.

The one hand-written table is the abbreviation dictionary in `matcher.py`
(`ent → otolaryngologic`, `wnd → wound`, …). A test asserts every entry is a
single word mapping to a single word, and that no key is a whole billing
description — that shape, one description in and one service out, is how an
answer table would have to be smuggled in.

### The one thing that *was* fitted, and how

Four readings of ambiguous contract clauses were chosen by testing them on the
**development** split: whether a cap breach zeroes the excess or blocks
reconstruction; whether an unknown service carries its billed amount or zero;
whether the exclusion-window boundary day counts; and whether a date outside
the term should also be reported as after the invoice date. Each is recorded in
`AuditPolicy` with the reasoning. They are interpretations of clause text that
apply to every invoice, not per-invoice adjustments — but they are the part of
this system with the weakest independent justification, and the first thing I
would re-derive against another hospital's contract.

## 3. Dev vs holdout: how big is the gap?

| | development (639) | locked holdout (274) | gap |
|---|---|---|---|
| detection accuracy | 1.0000 | 1.0000 | 0 |
| detection precision | 1.0000 | 1.0000 | 0 |
| detection recall | 1.0000 | 1.0000 | 0 |
| detection F1 | 1.0000 | 1.0000 | 0 |
| TP / FP / TN / FN | 46 / 0 / 593 / 0 | 12 / 0 / 262 / 0 | — |
| exact corrected total (where offered) | 636/636 (1.0000) | 272/273 (0.9963) | −0.0037 |
| MAE where offered (cents) | 0.00 | 159.89 | +159.89 |
| declined as unreconstructable | 3 | 1 | — |

**Invoice-level detection did not degrade.** That is the claim I am most
confident in: 12 erroneous invoices in the holdout, all 12 found, no false
positives across 262 clean ones.

**Category attribution did degrade, on one invoice**, and the monetary error is
the same invoice. It is analysed in §7. The honest reading of the holdout is:
detection generalized; the *matcher* has a failure mode that the development
split did not contain.

A word on the gap being zero for detection: the holdout contains only 12
erroneous invoices. A single miss would have moved recall to 0.917. Perfect is
what the numbers say; it is not strong evidence that the true error rate is
zero, and §6 is explicit about that.

## 4. The temporal (prospective) evaluation

Every invoice re-audited against a context containing only lines dated on or
before that invoice's own latest service date. Averaged over the scored
invoices, **49.7%** of all line items were withheld (median 49.6%, maximum
99.9% — the earliest invoices were judged with essentially no history).

Result on the development split: **identical to the retrospective run.**
Detection 46/0/593/0, 636/636 exact corrected totals.

This is less surprising than it looks, and the reason matters. Cumulative
utilisation is defined by clause 2.4 as the running total *up to but excluding*
the line being priced, ordered by service date. An engine that implements that
literally is already causal — a line can only ever be discounted by lines that
came before it, so hiding the future changes nothing. Volume discounts, the
rule most obviously at risk, are prospective by construction.

Two rules *could* have broken and did not:

- **Exclusion windows** look in both directions (clause 10.1), so a
  prospective view can miss a violation whose partner service is billed later.
  In this data all four development violations have the excluded service
  *earlier* than its trigger, and the trigger falls within the same invoice's
  span, so the cut-off never hid it. This is luck about the data, not a
  property of the engine. On a stream where the trigger arrives weeks later,
  prospective recall on this category would drop.
- **Cross-invoice duplicates** flag the later billing, which is already the
  causal direction.

The one rule that is genuinely non-causal is the daily cap when a patient's day
is split across invoices submitted at different times; there are no such cases
in Hospital 1.

## 5. Do the rule components matter?

Each row disables one component and re-runs the whole audit on the development
split.

| configuration | precision | recall | F1 | TP | FP | FN | exact corrected totals |
|---|---|---|---|---|---|---|---|
| full system | 1.000 | 1.000 | 1.000 | 46 | 0 | 0 | 636/636 |
| no description normalisation | 0.133 | 1.000 | 0.235 | 46 | 299 | 0 | 574/636 |
| no bundles | 0.215 | 0.978 | 0.353 | 45 | 164 | 1 | 460/636 |
| no premiums / uplifts | 0.179 | 0.978 | 0.303 | 45 | 206 | 1 | 416/636 |
| no volume discounts | 0.086 | 0.978 | 0.157 | 45 | 481 | 1 | 123/603 |
| no duplicate detection | 1.000 | 0.978 | 0.989 | 45 | 0 | 1 | 633/636 |
| no date validation | 1.000 | 0.913 | 0.955 | 42 | 0 | 4 | 636/636 |

Every component changes the result, so none is dead code, and the sizes are
interpretable:

- The three **pricing** rules (bundles, premiums, discounts) and
  **normalisation** destroy *precision*: without them the engine still finds
  every real error but prices hundreds of correct invoices wrongly and flags
  them. Volume discounts are the largest single contributor — removing them
  costs 481 false positives and 513 corrected totals, because the discounted
  services are high-volume.
- The two **detection-only** rules (duplicates, dates) cost *recall* and
  nothing else, which is what they should do: they find errors that leave the
  arithmetic untouched.
- Normalisation costs 299 false positives rather than false negatives, because
  an unnormalised description usually fails to match anything and the line
  becomes unpriceable rather than mispriced.

## 6. Are rare categories reliable?

**Mostly no, and the evaluation marks them.** Every category with fewer than
ten labelled invoices is starred in the evaluation tables. Across the whole
913-invoice data set the engine emits between 3 and 12 instances of each of 18
categories. Per-category F1 on that support is close to meaningless as a rate.

Holdout support by category: `unknown_service` 4, three categories at 3, and
**eight categories with a support of 1**. `premium_omitted` scoring 1.000 on
the holdout means one invoice was attributed correctly. It is not a 100%
accuracy claim.

What *is* supported by the data: the categories rest on deterministic checks
that are independently tested at their boundaries (cap−1/cap/cap+1,
threshold−1/threshold/threshold+1, weekday/Saturday/Sunday, window boundary in
both directions, discount tier crossings). I would rather rely on those
synthetic tests than on a per-category F1 computed over one example.

Categories I am least confident will transfer: `volume_discount_omitted` and
`volume_discount_incorrectly_applied` (4 each), because they depend on the
cumulative index being complete, and the index is exactly what ambiguous
descriptions corrupt — see §7.

## 7. Does service matching generalize?

11,415 line items, 488 distinct descriptions.

| outcome | lines | distinct descriptions |
|---|---|---|
| MATCHED on text margin | 11,066 | 469 |
| MATCHED via unit-basis tie-break | 218 | 4 |
| AMBIGUOUS | 120 | 4 |
| UNKNOWN | 11 | 11 |

Of the matched lines, 9,727 (86%) score ≥ 0.90 — whole-word agreement. The
weakest accepted match scores 0.772; the 1st percentile is 0.817. So roughly
14% of matches rest on expanded abbreviations, and `confidence.py` downgrades
any invoice containing one to the medium band. 740 of 913 invoices are medium
for that reason, which is the system correctly reporting that most invoices
contain at least one abbreviated description.

**The four ambiguous descriptions are genuine.** `Procedure Immun Endosc` (53
lines) fits both *Ambulatory* and *Preoperative Immunologic Endoscopic
Procedure* — the contract carries both, they share a unit basis, and the text
names neither qualifier. `Visit Amb Hm` (35), `Continuous Wnd Care` (20) and
`Cr Cont Wnd` (12) are the same shape. No amount of matcher work resolves
these; the information is not in the string. The engine does not guess: it
carries all candidates forward, and where the ambiguity straddles a discount
threshold it prices a *set* of plausible rates and flags only what is wrong
under every one of them. This is what turned 19 false positives into zero
during development, and it is the single design decision I would keep if I kept
nothing else.

**The unit-basis tie-break fired on 218 lines (4 descriptions).** Where it
fires, `used_unit_basis_for_matching` is set and `wrong_unit_basis` is
suppressed for that line — the basis cannot both identify the service and be
evidence against it. This is tested directly.

**The billed price is never used to identify a service.** `ServiceMatcher.match`
takes a description and an optional unit basis; there is no price parameter,
and a test asserts the signature stays that way. Using price would make the
audit circular: the price is the thing under audit.

### The failure mode the holdout exposed

`INV-H1-000236`, line 3: `Fract Outpatient Radiotherapy`, billed per_visit at
8225. The engine matched *Outpatient Metabolic Radiotherapy Fraction* at 0.843
with a runner-up at 0.418, and then reported `wrong_unit_basis` and
`unit_price_mismatch`. The label says `unknown_service`.

The description is missing its **specialty word**. The contract holds six
services of the form `<qualifier> <specialty> Radiotherapy Fraction`; the token
that separates them is exactly the one absent. The scoring function treats that
as an ordinary recall miss — three of four service tokens matched, every
description token used — which clears the 0.72 threshold comfortably.

The margin test did not save it, and this is the instructive part: when the
*discriminating* token is missing, every alternative loses the same token, so
they all fall together and the winner's margin stays wide. A wide margin looked
like confidence when it was an artifact of symmetric ignorance.

I fixed the mirror-image case during development — a description carrying an
*extra* contract word the candidate cannot explain (`Advanced Orthopaedic
Recovery Room` against *Advanced Cardiac Recovery Room Occupancy*) is penalised
by `CONTRADICTION_DECAY` and falls to UNKNOWN. The missing-discriminator case
is the same error with the evidence removed instead of added, and I did not
anticipate it.

**The fix I would make, and did not**: weight service tokens by how much they
discriminate within the rate schedule, and when the winner's *unmatched* tokens
are precisely its discriminators, return AMBIGUOUS or UNKNOWN rather than
MATCHED. That would have converted this row from two wrong categories into an
honest uncertainty. I have not implemented it, because the implementation was
frozen before the holdout was read and changing it now would destroy the only
unseen measurement this exercise has.

Cost of the miss: two wrong categories and one missing one on one invoice, and
43,650 cents of error on that invoice's corrected total (the whole of the
holdout's 159.89-cent MAE). The invoice was still correctly flagged, because a
second, independent finding on it (`premium_omitted`) was right.

## 8. Are the synthetic contract tests passing?

**190 of 190** (183 at the freeze; see the note at the top). They run against an
invented contract in `tests/hospital_1/conftest.py`
carrying one instance of each rule family — no Hospital 1 invoice, description,
patient or expected total appears in any test.

| area | what is covered |
|---|---|
| rounding | half away from zero at `0.5`, `1.5`, `2.5`, negatives; and a case proving per-step rounding gives a different cent from rounding once at the end |
| contract parsing | 13 required *failures*: wrong row shape, unknown unit basis, sub-cent rate, rule naming an unknown service, Sections 4 and 8 disagreeing about a cap, non-monotonic discount tiers, a service in two bundles, unsupported rounding convention, unreadable facility/plan-tier clause, emptied rule section, silently truncated rate table |
| matching | normalisation, reference suffixes, reordered/abbreviated descriptions, UNKNOWN, AMBIGUOUS, unit-basis tie-break, no-double-counting, no price parameter |
| caps | cap−1, cap, cap+1; aggregation across lines, across invoices; isolation across patients and days; breach declines reconstruction |
| premiums | threshold−1, threshold, threshold+1; daily aggregate not per line; omitted and incorrectly-applied both named |
| weekend uplifts | weekday, Saturday, Sunday; omitted and incorrectly applied |
| discounts | before, crossing (prior-exclusive), after; second tier; wrong tier named; same-date ordering by line id; across patients; service-date order beating invoice-date order |
| bundles | A only, B only, A+B same day, different days, different patients, across invoices, bundled rate without the partner |
| exclusions | offsets 0, 1, 6, 7 inside in both directions; 8 and 38 outside; per patient; across invoices; only the left-hand service unbillable |
| duplicates | same invoice, cross invoice, different patient, different day, reused identifier as two physical occurrences |
| leakage | the structural guards in §1 and §2 |

## 9. What cannot be reconstructed

Stated plainly, because the alternative is inventing numbers.

**Daily-cap breaches — 4 invoices (3 dev, 1 holdout).** A quantity of 11
against a cap of 8 proves the figure is wrong. It does not say what was
delivered; the true quantity could be anything from 1 to 11. Pricing the capped
quantity gives the *ceiling*, not the correction. These invoices carry an empty
`expected_total_cents`, the ceiling in
`maximum_contractually_payable_total_cents`, and the low confidence band.

I can say this is right rather than merely cautious: on the three development
cap breaches, the labelled corrected quantities are 3, 3 and 9 against caps of
8, 12 and 12 and billed quantities of 11, 14 and 15. No function of (billed,
cap) produces that sequence. Anyone reporting a confident corrected total here
is reporting a guess.

**Unknown services — 11 lines across 11 invoices.** A description naming no
contracted service cannot be priced from this agreement. The engine carries the
billed amount through — we do not know it is worth nothing — sets
`correction_reconstructable=0`, and drops the invoice to the low band. 19
invoices are not reconstructable for this or a related reason.

**Ambiguous service identity — 120 lines, 4 descriptions.** Where the
candidates disagree about the line total and the billed figure matches none of
them, no corrected total is offered.

**Malformed service dates — 6 lines.** A line with no valid date cannot be
placed in the cumulative sequence, so its own discount tier and its
contribution to every other line's tier are both unknown.

**Not claimed at all:** that the confidence numbers are probabilities. They are
review priorities — 0.92 / 0.70 / 0.40 for the high / medium / low bands,
assigned from the quality of the evidence, never from whether a label matched.
The observed detection accuracy within each band is reported in the evaluation
files (1.000 in all three bands on both splits), but with 7 low-band holdout
invoices that is a description of what happened, not a calibration.

## 10. What I would do next

1. **The discriminative-token fix in §7.** It is the one known defect, it has a
   clear general form, and it would move a wrong answer to an honest "I don't
   know".
2. **Re-derive the four `AuditPolicy` readings against Hospital 2's contract**
   rather than carrying them over. They were chosen on Hospital 1 development
   data and are the least transferable part of the system.
3. **Calibrate confidence, or stop calling it confidence.** Three fixed values
   attached to three bands is a triage order. Turning it into a probability
   needs a held-out calibration set that this exercise's 12 holdout positives
   cannot provide.
4. **Make the temporal test stricter** — cut off at the invoice date rather
   than the invoice's last service date. The current cut-off is defensible but
   generous, and §4 shows exclusion windows would be the first casualty.
