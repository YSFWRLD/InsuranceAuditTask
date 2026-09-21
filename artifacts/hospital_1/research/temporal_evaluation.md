# Hospital 1 - temporal (prospective) evaluation

Every invoice is audited against a context containing only lines whose service date is on or before that invoice's own latest service date. Cumulative utilisation, exclusion history and duplicate detection therefore see the past only.

Scored on the **development** split, so it is comparable with the retrospective development numbers and independent of the holdout.

The cut-off had teeth: averaged over the scored invoices, **49.7%** of all line items were hidden from the context used to audit them (median 49.6%, maximum 99.9%). The earliest invoices were judged against almost no history at all.

## Temporal (prospective) evaluation, development split

Invoices scored: **639**

### 1. Invoice-level error detection

| metric | value |
|---|---|
| accuracy | 1.0000 |
| precision | 1.0000 |
| recall | 1.0000 |
| F1 | 1.0000 |
| TP / FP / TN / FN | 46 / 0 / 593 / 0 |

### 2. Category attribution

Support is the number of labelled invoices carrying the category. 
A category with single-digit support cannot be read as a rate.

| category | support | precision | recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| `unit_price_mismatch` | 10 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 |
| `unknown_service` * | 8 | 1.000 | 1.000 | 1.000 | 8 | 0 | 0 |
| `wrong_unit_basis` * | 8 | 1.000 | 1.000 | 1.000 | 8 | 0 | 0 |
| `contract_number_mismatch` * | 5 | 1.000 | 1.000 | 1.000 | 5 | 0 | 0 |
| `duplicate_invoice_id` * | 5 | 1.000 | 1.000 | 1.000 | 5 | 0 | 0 |
| `invoice_total_mismatch` * | 5 | 1.000 | 1.000 | 1.000 | 5 | 0 | 0 |
| `premium_incorrectly_applied` * | 5 | 1.000 | 1.000 | 1.000 | 5 | 0 | 0 |
| `bundle_not_applied` * | 4 | 1.000 | 1.000 | 1.000 | 4 | 0 | 0 |
| `exclusion_window_violation` * | 4 | 1.000 | 1.000 | 1.000 | 4 | 0 | 0 |
| `service_date_after_invoice_date` * | 4 | 1.000 | 1.000 | 1.000 | 4 | 0 | 0 |
| `volume_discount_incorrectly_applied` * | 4 | 1.000 | 1.000 | 1.000 | 4 | 0 | 0 |
| `volume_discount_omitted` * | 4 | 1.000 | 1.000 | 1.000 | 4 | 0 | 0 |
| `cross_invoice_duplicate` * | 3 | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| `daily_cap_exceeded` * | 3 | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| `line_total_arithmetic` * | 3 | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| `malformed_service_date` * | 3 | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| `premium_omitted` * | 2 | 1.000 | 1.000 | 1.000 | 2 | 0 | 0 |
| `service_date_out_of_window` * | 2 | 1.000 | 1.000 | 1.000 | 2 | 0 | 0 |

`*` = fewer than 10 labelled examples; treat the rate as indicative only.

### 3. Monetary reconstruction

| metric | value |
|---|---|
| corrected total offered | 636 of 639 (0.9953) |
| exact match, where offered | 636 / 636 (1.0000) |
| MAE, where offered (cents) | 0.00 |
| declined as unreconstructable | 3 |

### Detection accuracy by stated confidence band

| band | invoices | detection correct |
|---|---|---|
| high | 108 | 108/108 (1.0000) |
| medium | 519 | 519/519 (1.0000) |
| low | 12 | 12/12 (1.0000) |

These are observed accuracies, not calibrated probabilities. The confidence value is a review priority; see section 6 of `src/hospital_1/audit.py`.
