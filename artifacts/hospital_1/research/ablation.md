# Hospital 1 - ablation study

Each row disables one component and re-runs the whole audit against the **development** split. A component that changes nothing when removed is either never exercised by this data or is not doing what it claims.

| configuration | precision | recall | F1 | TP | FP | FN | exact corrected totals |
|---|---|---|---|---|---|---|---|
| full system | 1.000 | 1.000 | 1.000 | 46 | 0 | 0 | 636/636 |
| no description normalisation | 0.133 | 1.000 | 0.235 | 46 | 299 | 0 | 574/636 |
| no bundles | 0.215 | 0.978 | 0.353 | 45 | 164 | 1 | 460/636 |
| no premiums / uplifts | 0.179 | 0.978 | 0.303 | 45 | 206 | 1 | 416/636 |
| no volume discounts | 0.086 | 0.978 | 0.157 | 45 | 481 | 1 | 123/603 |
| no duplicate detection | 1.000 | 0.978 | 0.989 | 45 | 0 | 1 | 633/636 |
| no date validation | 1.000 | 0.913 | 0.955 | 42 | 0 | 4 | 636/636 |
