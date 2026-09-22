# Hospital 1 — evaluation report

Hospital 1 is the labelled development hospital. The challenge does not score
it, and it is **not** in `outputs/submission.csv`. This is the short
evaluation report the challenge asks for: per-category performance, and an
error analysis grouped by failure type.

Every number below was regenerated from code on 2026-09-22. Commands:
`python -m src.main evaluate h1` (writes the split-level tables to
[`evaluation.md`](evaluation.md)). The full-913 figures come from the same
`evaluation.evaluate` function run over `labels/hospital_1_labels.csv`.
Predictions: [`predictions.csv`](predictions.csv); one row per finding:
[`findings.csv`](findings.csv).

## Validation status — read this first

- **These measurements are post-hoc, not an untouched test set.** The
  repository records the following split and freeze procedure:
  - the locked 70/30 split (seed `20240517`) was created at
    `2026-09-20T23:00:33Z`, before the final engine implementation;
  - development used the 639-invoice dev half;
  - the 274-invoice holdout was first scored once, after freeze
    `ab7bc8040ab8fe43`.

  But the project owner reports that the full Hospital 1 labels were visible
  during development of this project. That includes an earlier iteration
  whose results are quoted below and are not reproduced by this code. The
  repository cannot prove the opposite. Since then, the holdout and the
  temporal study have also been viewed many times, because every
  `evaluate h1` prints them. So the holdout and temporal figures are
  **post-hoc checks, not independent validation**, and should not be read as
  unseen test-set performance.
- **No label reaches prediction code.** This is enforced by tests in
  `tests/hospital_1/test_evaluation.py`:
  - the prediction modules never name a label file or column, and never
    import the evaluator;
  - no invoice, patient or line id and no service name is hardcoded;
  - the abbreviation table maps single words to single words, never a whole
    description;
  - renaming every label file away leaves all 913
    `(flagged, categories, expected_total)` triples unchanged.

  A grep of `src/` finds no `INV-H*`/`H*-L*` identifiers and no 7-digit
  literal other than the split seed.
- **Some choices were fitted to the dev labels.** Four readings of ambiguous
  clauses were chosen by testing them on the dev split: cap breach, unknown
  service amount, the exclusion boundary day, and out-of-term dates. They
  apply to every invoice and are listed in [`decision_log.md`](decision_log.md) (items 2, 3, 4 and 6).

## 1. Invoice-level detection

| scope | invoices | TP | FP | TN | FN | precision | recall |
|---|---|---|---|---|---|---|---|
| all labelled | 913 | 58 | 0 | 855 | 0 | 1.000 | 1.000 |
| dev split | 639 | 46 | 0 | 593 | 0 | 1.000 | 1.000 |
| holdout split (post-hoc) | 274 | 12 | 0 | 262 | 0 | 1.000 | 1.000 |

With 58 erroneous invoices, one miss would move recall to 0.983. So a perfect
score here is weak evidence that the true error rate is zero.

## 2. Per-category attribution (all 913)

Support is the number of labelled invoices carrying the category. Every
category has fewer than 13 examples, so every rate is indicative only.

| category | support | TP | FP | FN |
|---|---|---|---|---|
| `unknown_service` | 12 | 11 | 0 | **1** |
| `wrong_unit_basis` | 11 | 11 | **1** | 0 |
| `unit_price_mismatch` | 10 | 10 | **1** | 0 |
| `malformed_service_date` | 6 | 6 | 0 | 0 |
| `premium_incorrectly_applied` | 6 | 6 | 0 | 0 |
| `line_total_arithmetic` | 6 | 6 | 0 | 0 |
| `invoice_total_mismatch` | 6 | 6 | 0 | 0 |
| `service_date_after_invoice_date` | 5 | 5 | 0 | 0 |
| `duplicate_invoice_id` | 5 | 5 | 0 | 0 |
| `bundle_not_applied` | 5 | 5 | 0 | 0 |
| `service_date_out_of_window` | 5 | 5 | 0 | 0 |
| `contract_number_mismatch` | 5 | 5 | 0 | 0 |
| `volume_discount_incorrectly_applied` | 4 | 4 | 0 | 0 |
| `daily_cap_exceeded` | 4 | 4 | 0 | 0 |
| `exclusion_window_violation` | 4 | 4 | 0 | 0 |
| `cross_invoice_duplicate` | 4 | 4 | 0 | 0 |
| `volume_discount_omitted` | 4 | 4 | 0 | 0 |
| `premium_omitted` | 3 | 3 | 0 | 0 |

All three category errors fall on one invoice, `INV-H1-000236` (§5, type B).
On the dev split, attribution is exact.

## 3. Expected-total reconstruction (all 913)

| metric | value |
|---|---|
| corrected total offered | 909 / 913 |
| declined (blank `expected_total_cents`) | 4 — all daily-cap breaches |
| exact, where offered | 908 / 909 (0.9989) |
| exact, over all 913 (a declined total counts as not exact) | 908 / 913 (0.9945) |
| MAE where offered | 48.02 cents (one invoice, 43,650 cents off) |

**Difference from the previously quoted figures.** The earlier figures were
exact ≈ 99.56% and MAE ≈ 333.5 cents, with "four misses from daily-cap
corruption". This code does not reproduce them. They fit an earlier behaviour
that priced the four cap-breach invoices at the capped quantity:

- the gaps between the capped upper bound and the labelled total are 14,775 +
  25,425 + 76,275 + 188,000 = 304,475 cents;
- 304,475 / 913 = 333.5, and 909 / 913 = 99.56%.

The current engine declines to reconstruct those four instead, and shows the
capped figure only as `maximum_contractually_payable_total_cents`. Section 5
explains why. The historical figures are reported here for provenance only.

## 4. Confidence and calibration

The confidence score takes only three values:

| score | band | invoices | flagged | detection correct |
|---|---|---|---|---|
| 0.92 | high | 154 | 11 | 154 / 154 |
| 0.70 | medium | 740 | 28 | 740 / 740 |
| 0.40 | low | 19 | 19 | 19 / 19 |

- **Not a calibrated probability.** The score measures evidence strength (how
  the services were identified, whether the total can be reconstructed), set
  by hand in `src/hospital_1/audit.py` §6. Read as P(detection correct), it is
  badly under-confident: the low band was right 19 of 19 times.
- **The low band marks reconstruction trouble, not doubtful detection.** Every
  low-band invoice is flagged, and every one has
  `correction_reconstructable = 0`:
  - 4 are the declined cap breaches;
  - 15 carry an unknown service or a malformed date. For these a total is
    offered, but it is marked non-reconstructable.
- **The bands cannot be validated here.** No band contains a detection error,
  so this data cannot show whether the ordering is right. The one wrong total
  (§5 B) sat in the medium band, at 0.70.

## 5. Failure analysis by systematic type

### Observed failures

Only two systematic failure types occur in the labelled data. No others were
found, and none are invented to fill a list.

**A. The pre-breach quantity cannot be recovered after a daily-cap breach.**
There are 4 invoices: 000015, 000049, 000227, 000725. Each one is detected
correctly with the right category, but its total is declined.

- Example: `INV-H1-000015` bills 9 units against a daily cap of 4.
- The invoice cannot say how many units were really delivered, so the
  corrected total is unknowable from the invoice.
- The engine gives the capped price (1,210,600) as an upper bound only. The
  labelled total is 1,195,825, lower than the capped price, which confirms
  that pricing at the cap would have been confidently wrong.
- This costs 4 blank totals, and no wrong numbers.

**B. The matcher accepts a description that lacks its discriminating word.**
There is 1 invoice, `INV-H1-000236` (holdout).

- Line 3, `Fract Outpatient Radiotherapy`, has no specialty word. The contract
  has six `<qualifier> <specialty> Radiotherapy Fraction` services, and the
  missing word is exactly what separates them.
- The matcher chose *Outpatient Metabolic Radiotherapy Fraction* (score
  0.843, runner-up 0.418). All alternatives lost the same token, so the
  margin looked wide.
- Result: `unit_price_mismatch` and `wrong_unit_basis` were reported, and the
  labelled `unknown_service` was missed. The total was off by 43,650 cents,
  at confidence 0.70.
- Detection was still right only because a separate finding
  (`premium_omitted`) was also on the invoice.
- This is the one *confidently wrong* output in Hospital 1.
- It was found after the freeze and left unfixed on purpose. The fix would be
  to weight tokens by how much they discriminate and return AMBIGUOUS when the
  missing tokens are the discriminators. The same idea is built into
  Hospital 2's matcher, which requires the qualifier and specialty to be
  evidenced.

### Known methodological and model limitations

These are not observed errors. They are limits on what the numbers above can
claim.

1. **Post-hoc validation.** See the status section above. The holdout and
   temporal figures are not independent evidence.
2. **Confidence is a heuristic, not a calibrated probability.** See §4.
3. **Free-text service identity depends on a hand-written abbreviation table
   and token scoring.** Type B shows how it fails on an unseen shorthand. 113
   invoices already carry a line with an unresolved identity, and they are
   priced over an interval of possible services.
4. **Support is small.** Every category has 3–12 examples.
5. **Exclusion windows under prospective audit.** The temporal study was
   run on the dev split and matched the retrospective run exactly. That is a
   property of this data, not of the engine: every excluded service comes before its trigger
   ([`artifacts/hospital_1/research/temporal_evaluation.md`](../../artifacts/hospital_1/research/temporal_evaluation.md)).
6. **The four fitted clause readings** (see the status section) are the
   weakest-justified part of the system. They would need re-deriving for
   another contract.
