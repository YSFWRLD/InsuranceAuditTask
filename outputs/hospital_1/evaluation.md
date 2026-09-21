# Hospital 1 - evaluation

Three questions, reported separately and never combined: is the invoice wrong (detection), what is wrong with it (category attribution), and what should it have cost (monetary reconstruction).

The development split is `artifacts/hospital_1/split/dev_labels.csv`. The 30% holdout was not read while the system was being built.


## Development split (70%)

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


---

The holdout was first scored once, on implementation `ab7bc8040ab8fe43` frozen 2026-09-20T23:37:05.441736+00:00. No logic was changed in response to it. The current prediction sources match freeze `7f075ea2733f1eff` (structural refactor of ab7bc8040ab8fe43 into src/shared + src/hospital_1; no logic change; predictions.csv byte-identical (predictions_sha256 unchanged)).

## Locked holdout split (30%)

Invoices scored: **274**

### 1. Invoice-level error detection

| metric | value |
|---|---|
| accuracy | 1.0000 |
| precision | 1.0000 |
| recall | 1.0000 |
| F1 | 1.0000 |
| TP / FP / TN / FN | 12 / 0 / 262 / 0 |

### 2. Category attribution

Support is the number of labelled invoices carrying the category. 
A category with single-digit support cannot be read as a rate.

| category | support | precision | recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| `unknown_service` * | 4 | 1.000 | 0.750 | 0.857 | 3 | 0 | 1 |
| `line_total_arithmetic` * | 3 | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| `malformed_service_date` * | 3 | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| `service_date_out_of_window` * | 3 | 1.000 | 1.000 | 1.000 | 3 | 0 | 0 |
| `wrong_unit_basis` * | 3 | 0.750 | 1.000 | 0.857 | 3 | 1 | 0 |
| `bundle_not_applied` * | 1 | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| `cross_invoice_duplicate` * | 1 | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| `daily_cap_exceeded` * | 1 | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| `invoice_total_mismatch` * | 1 | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| `premium_incorrectly_applied` * | 1 | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| `premium_omitted` * | 1 | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| `service_date_after_invoice_date` * | 1 | 1.000 | 1.000 | 1.000 | 1 | 0 | 0 |
| `unit_price_mismatch` | 0 | 0.000 | 0.000 | 0.000 | 0 | 1 | 0 |

`*` = fewer than 10 labelled examples; treat the rate as indicative only.

### 3. Monetary reconstruction

| metric | value |
|---|---|
| corrected total offered | 273 of 274 (0.9964) |
| exact match, where offered | 272 / 273 (0.9963) |
| MAE, where offered (cents) | 159.89 |
| declined as unreconstructable | 1 |

### Detection accuracy by stated confidence band

| band | invoices | detection correct |
|---|---|---|
| high | 46 | 46/46 (1.0000) |
| medium | 221 | 221/221 (1.0000) |
| low | 7 | 7/7 (1.0000) |

These are observed accuracies, not calibrated probabilities. The confidence value is a review priority; see section 6 of `src/hospital_1/audit.py`.
