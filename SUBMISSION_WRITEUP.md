# Invoice Audit Exercise

**Yousef Filmban** · Repository: <https://github.com/YSFWRLD/InsuranceAuditTask> · Predictions: `submission.csv` (attached; `outputs/submission.csv` in the repository)

## 1. Approach and how I measured results

**Sequencing.** I worked one hospital at a time and kept each contract's logic separate.

- **Hospital 1 first,** because it is the only hospital with labels. The core rules were developed there: conservative matching, a blank total rather than a guessed one, and evidence-based confidence.
- **Hospital 2 next,** to test the approach on a contract written in prose, with conservative semantic matching.
- **Hospital 4 next,** as a more structured, table-driven contract.
- **Hospital 5 last,** to improve service-identity coverage while keeping uncertainty explicit.
- **Hospital 3 was deliberately not implemented.** I chose to do three scored hospitals carefully rather than four thinly.

The submission covers H2 (1,125 invoices), H4 (835) and H5 (1,050): 3,010 rows. Hospital 1 is not in it.

**Hospital 1.** Against the labels, across all 913 invoices:

- 58 true positives, 0 false positives, 855 true negatives, 0 false negatives;
- 908 of the 909 expected totals offered were exact.

The H1 labels were visible to me during development, and a few ambiguous clauses were settled by checking them on the development split. These figures are therefore development (post-hoc) measurements, **not** an untouched estimate of how the method generalises. Details are in `outputs/hospital_1/evaluation_report.md`.

**Hospitals 2, 4 and 5.** There are no labels, so accuracy cannot be measured directly. Instead I checked:

- **contract consistency:** every rate, multiplier and rule is parsed from the contract text with its clause, and the parser stops on wording it does not recognise;
- **deterministic recalculation:** every proven total is priced step by step from the contract, with a trace for every line;
- **regression tests:** 711 automated tests, including boundary cases for the contract rules;
- **reproducibility:** the full submission regenerates byte-for-byte offline, with no API keys;
- **coverage:** how often an expected total could actually be reconstructed. H2 237 of 1,125, H4 307 of 835 and H5 939 of 1,050 totals are proven; the rest are deliberately blank.

## 2. Where I was uncertain, and why

My guiding rule was that a blank expected total is better than a confident wrong one. An amount is left blank whenever the service identity or the contract reading does not support a single answer.

**H2 semantic limitation.** An LLM classifier proposed identities for 69 unclear description groups, and Jev (TypeSafe) verified them. Under my acceptance gate this stage added **no** verified service matches. I left those identities unresolved rather than force coverage, which is why most H2 totals are blank.

**H5 service identity.** Descriptions are free-text shorthand. Hospital 5 combines deterministic matching with bounded Jev reviews: whether an abbreviation is safe to expand, and which contracted service an incomplete description refers to.

- Billed prices and totals were never used to decide which service a line is.
- One human-reviewed assumption matters: **ENT is read as Otolaryngologic,** only where the surrounding words fit a contracted Otolaryngologic service. 233 proven totals depend on it, and it is recorded as a human-reviewed assumption with its reason.
- Three description groups stay unresolved because the text genuinely omits a distinguishing word: `SUPERVISED SPCM ANLY`, `UROL HM VST` and `RTN PHYSIOTHERAPY SESS`. An earlier rule that accepted such cases was removed in my own review as too aggressive.

**Contract readings.** Some clauses admit more than one reading. Each choice and its alternative are in the decision logs:

- **Exclusions** ("not billable within N days of"): read as a distance on either side, day N included. This is the least certain reading.
- **Cumulative discounts:** a line takes the discount tier already exceeded before it, rather than being split at the threshold.
- **Daily caps:** a breach is flagged, but the total is left blank when the contract does not say what the corrected quantity is.
- **Facility:** the contract refers to the line's facility, but lines carry none, so the invoice's facility is used.

**Confidence.** The confidence values are evidence-strength judgments, such as whether a finding rests only on the invoice's own fields or on interpreting free text. They are **not** statistically calibrated probabilities.

## 3. What I would do differently with another week

1. **Implement Hospital 3.**
2. **Have the remaining ambiguous service groups reviewed independently,** especially the unresolved H5 descriptions.
3. **Calibrate confidence** against a properly held-out validation set rather than the development labels.
4. **Add sensitivity analysis** for the ambiguous contract readings (exclusions, discount thresholds, caps), showing how findings and totals change under each alternative.
5. **Simplify** the parts of the semantic pipeline that did not improve coverage, particularly Hospital 2's.

I would not raise coverage by lowering the acceptance thresholds. A confident mistake costs more than an explicit uncertainty.

## AI assistance and time

- **Development:** code, tests and documentation were written with Claude Code (Claude Opus models) as an AI coding assistant. I used it for implementation, code review, contract-reasoning support, and test generation and validation. I set the scope, rules and stopping points, and reviewed the results. All development prompts are versioned in `prompts/`.
- **Runtime:** models are used only as bounded reviewers of service descriptions. H2 uses an LLM classifier and Jev; H5 uses Jev alone. Pricing, arithmetic and every contract rule are deterministic Python. No model produced ground truth, a price or a total. The runtime prompts are also in `prompts/`.
- **Time spent:** approximately 4 hours per day over four days.
