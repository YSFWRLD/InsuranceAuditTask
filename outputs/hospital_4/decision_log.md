# Decision log — Hospital 4

The readings, policies and open questions behind the Hospital 4 audit. Every
entry is marked as one of three kinds, and they are not mixed:

- **CONTRACT RULE**: the agreement states it; the code applies it.
- **IMPLEMENTATION POLICY**: the agreement is silent or incomplete, so we
  chose a reading. The alternative is named.
- **UNRESOLVED AMBIGUITY**: the text supports more than one reading, and the
  choice could change submitted results.

There are no Hospital 4 labels. Nothing here was tuned towards a number of
flagged invoices, and no accuracy is claimed. No model or external API is used
anywhere in the Hospital 4 pipeline. Figures come from the current run; see
[`audit_report.md`](audit_report.md) for the live counts.

---

## Data

**1. The JSONL is canonical, and occurrences are never merged.**
IMPLEMENTATION POLICY.

- There are 840 physical invoice records, 835 invoice numbers and 10,560
  lines. Five numbers are each used by two records.
- Lines are taken from the JSONL nesting, never joined by `invoice_id`.
- The two CSVs agree with the JSONL field for field. This is checked on every
  run, and the audit refuses to run if they disagree.

**2. A reused invoice number: the row represents the later occurrence.**
CONTRACT RULE (clause 11.1: "an invoice number unique across the term") plus
IMPLEMENTATION POLICY (which record the row describes).

- The later record carries `duplicate_invoice_id`. The submitted row reports
  its billed total, expected total, pricing state and findings.
- The earlier record is audited separately and its findings are in
  `findings.csv`; they are not copied onto the row.
- The earlier record stays in every hospital-wide index: Service Day
  aggregates, cumulative utilisation, bundles, exclusions and repeats.
- This is Hospital 1's labelled convention and Hospital 2's policy.

## Service identity

**3. The billed price is never used for identity.** IMPLEMENTATION POLICY
(the price is what is audited, so using it would be circular).

- The matcher's public functions take a description, plus a billed
  unit-basis token for one narrow tie-break.
- Tests pin the signatures, scan the identity code for price, total and id
  fields, and check that no price-based matching path exists.

**4. Structure, not similarity.** IMPLEMENTATION POLICY.

- Every service name is `<qualifier> <specialty> <concept>`. The parser checks
  that the three vocabularies are disjoint.
- Normalising a description means:
  - lower case;
  - the `/CW-####` reference stripped, as billing noise;
  - punctuation collapsed;
  - words expanded through an explicit abbreviation table;
  - comparison as a set, so word order is irrelevant.
- The abbreviation table was built from the shorthand actually present in the
  Hospital 4 data. Every entry maps one word to one contract word, or to a
  listed non-contract word (dermatologic, endocrine, isolation, dispensing,
  pharmacy). A test enforces this.
- `beds` → bedside was added from the data: it appears only with Bedside
  services.
- 534 raw descriptions reduce to 211 clusters.

**5. MATCHED needs all three name slots.** IMPLEMENTATION POLICY; the same
rule as Hospital 2, adopted after Hospital 1's one matcher defect.

- MATCHED requires the qualifier, the specialty and at least one concept word
  to be present, no contradicting word, and exactly one consistent contracted
  service.
- A description that omits a slot stays AMBIGUOUS even when only one
  contracted service fits, for example `renal ANAES admin`, which has no
  qualifier. The text cannot rule out an uncontracted service of the same
  shape.
- This is the main limit on coverage:

| cluster status | clusters |
|---|---|
| MATCHED | 157 |
| AMBIGUOUS, a slot missing | 32 |
| AMBIGUOUS, genuine tie | 9 |
| UNKNOWN | 13 |

**6. The unit-basis tie-break.** IMPLEMENTATION POLICY.

- The billed basis decides only a genuine textual tie (two or more consistent
  services) in which exactly one tied service is contracted on that basis.
- This resolved 147 lines, all recorded with
  `used_unit_basis_for_identity = true`.
- None of those lines can carry `wrong_unit_basis`: one piece of evidence
  cannot both identify the service and accuse it.
- The compound basis `per_hour_per_item` (Intermittent Urologic Telemetry
  Monitoring) is compared as a whole token, never split.

**7. Unknown services.** IMPLEMENTATION POLICY.

- A description that every contracted service contradicts is flagged
  `unknown_service`. That is 13 lines on 9 invoices, for example
  `CONSULT rtn NEURO` and `LAB - emer DERM`.
- It gets no contract price, so the invoice's `expected_total_cents` is blank.
- The diagnostic `provisional_expected_total_cents` carries the line at its
  billed amount; it is never submitted.
- Hospital 1's labels suggest the corrected total keeps such a line intact.
  We did not submit that, because the contract is silent on unscheduled
  services.

**8. Why no LLM stage.** IMPLEMENTATION POLICY.

- Hospital 2's semantic stage added no verified mappings.
- This task required a deterministic solution first, and asked for a coverage
  report before any semantic stage.
- 822 lines (7.8%) remain AMBIGUOUS, on 523 of 835 invoice numbers.
  `unresolved.json` ranks the 41 ambiguous clusters by impact, so that a
  reviewed evidence table, or a semantic stage, can be judged later.

## Pricing

**9. Clause 4.1 order, rounded half up after every step.** CONTRACT RULE
(4.1, 4.2).

- The order is: bundle substitution → facility → plan tier → premium →
  cumulative discount.
- Every step rounds half up to a whole cent. Tests pin a case where rounding
  only at the end would differ.
- The facility and plan-tier multipliers are 1 (clause 2.2). The parser
  refuses to assume this if the wording changes.

**10. Interval uncertainty.** IMPLEMENTATION POLICY.

- An AMBIGUOUS line is carried as its whole candidate set. It widens the
  upper bound of every Service Day aggregate and of cumulative utilisation for
  each candidate.
- A line with a malformed date widens the upper bound of cumulative
  utilisation everywhere, because its position is unknown.
- A threshold is decided only when the interval lies wholly on one side of it.
  Otherwise that stage is left open: the line is flagged only if its price is
  wrong under every open reading, and the invoice gets no submitted total.

**11. Bundles (Section 7).** CONTRACT RULE (7.1, 7.2) plus IMPLEMENTATION
POLICY (ambiguity).

- A bundle applies when the partner is *certainly* delivered to the same
  patient on the same Service Day, on any invoice.
- If the partner is only possibly present, both readings are carried and the
  bundle is not assumed.

**12. Threshold premiums (Section 5).** CONTRACT RULE (5.1, 5.3) plus
IMPLEMENTATION POLICY (ambiguity, repeats).

- The premium is assessed on the (patient, service, Service Day) aggregate
  across every line and invoice, and the aggregate counts every billed line,
  including repeats. This follows Hospital 1's convention. No repeat in the
  Hospital 4 data touches a premium or capped service, so this choice changes
  nothing today.
- A straddling interval leaves the premium open.

**13. Cumulative discounts (Section 8).** CONTRACT RULE (8.3–8.5, 4.3) plus
IMPLEMENTATION POLICY (ambiguity).

- Utilisation is counted across the whole data set and all patients, in
  Service Date order, with line identifier breaking ties within a day.
- The count is prior-exclusive: the line on which a threshold is first
  crossed is not discounted.
- Only the deepest applicable tier applies.
- Unresolved or undated lines widen the interval. Pricing stays certain unless
  the interval straddles a threshold, which happens on 7 lines.

## Daily limits, repeats and exclusions

**14. Daily limits (Section 6).** CONTRACT RULE (6.1: "A quantity in excess of
the limit is not payable"), applied as instructed for this hospital.

- The payable quantity is `min(limit, billed quantity)`, and the corrected
  total is reconstructable.
- No allocation between competing lines is ever invented. Clause 11.3 leaves
  at most one payable line per patient, service and day, so the limit applies
  to that line alone. Repeats pay nothing.
- There are 6 breaches. The 4 with no other blocker get a submitted total at
  the limit.
- **UNRESOLVED AMBIGUITY, see item 19.**

**15. Repeats (clause 11.3).** CONTRACT RULE (the prohibition) plus
IMPLEMENTATION POLICY (which record is the repeat).

- The contract forbids billing the same service twice for the same patient
  and Service Date, but does not say which record stands.
- We keep the first billing, ordered by invoice date, then invoice id, then
  occurrence, then line id. Later lines are `duplicate_service` (same invoice)
  or `cross_invoice_duplicate`, and pay nothing.
- If an unresolved line billed earlier might be the same service, the resolved
  line's payability is left open.

**16. Exclusion windows (Section 9).** CONTRACT RULE (9.1: the same patient,
either direction, across invoices) plus IMPLEMENTATION POLICY (the boundary
day).

- Only a *certain* trigger proves a violation. The excluded line pays
  nothing; the trigger stays payable, because the table is directional.
- A trigger that is only possible is recorded as uncertainty, not asserted.
- "Within N days" is read **inclusively**: a trigger exactly N days away
  violates. This matches Hospital 1's labelled convention.
- The Hospital 4 data is consistent with it. Every certain trigger inside a
  window sits exactly N days away, and near-miss pairs sit N+1 to N+3 days
  away. An exclusive reading would flag no exclusion at all.

## Invoice-level checks

**17. Identity-independent checks.** CONTRACT RULE. These do not depend on
service identity, so an unresolved line never suppresses them:

- `contract_number_mismatch` (11.1);
- `duplicate_invoice_id` (11.1);
- `malformed_service_date`;
- `service_date_out_of_window` (11.2, 2.1);
- `service_date_after_invoice_date` (11.2 states it outright here);
- `line_total_arithmetic` (4.4);
- `invoice_total_mismatch` (4.4).

A date both outside the term and after the invoice date is reported once, as
out of term. A malformed date blocks reconstruction.

## Detection, reconstruction and confidence

**18. Three separate answers.** IMPLEMENTATION POLICY.

- `pricing_complete`: every line of the represented occurrence has an exact
  contract price, from resolved identities and settled thresholds.
- `correction_reconstructable`: additionally, no finding blocks
  reconstruction (a malformed date or an unknown service).
- `expected_total_cents` is filled only when reconstructable. It is never the
  billed total, zero, a guess, or an amount from a forced match.
- The current run: 308 complete, 307 reconstructable, 528 blank.

**Confidence** is evidence strength, not a probability: high 0.85, medium
0.65, low 0.40.

| row | band |
|---|---|
| flagged, every finding identity-independent | high |
| flagged, unknown service or pricing incomplete | low |
| flagged, rests on hospital-wide state, a recorded policy or a tie-break | medium |
| flagged, a price or basis finding on text-identified services | high |
| not flagged, with an unresolved line | low |
| not flagged, tie-break or not reconstructable | medium |
| otherwise | high |

Nothing is calibrated, because there are no labels. The bands were set before
the results were seen and were not tuned to any other participant's output.

## Unresolved ambiguities

**19. What a cap breach's corrected total is.**

- Clause 6.1 says the excess "is not payable". Read literally, the corrected
  total prices the limit, and that is what this submission does, as
  instructed.
- Hospital 1's labels contradict this for the same generator family. There,
  the corrected quantities for cap breaches were the *pre-breach* quantities
  (3, 3 and 9 against billed 11, 14 and 15). Pricing at the cap would have
  been wrong on all four Hospital 1 cases. Hospital 1's cap clause lacks the
  "not payable" sentence, which is why the reading differs here.
- The alternative is one switch: `AuditPolicy(cap_excess_priced_at_cap=False)`.
  It flags the same invoices and leaves their totals blank, with the limit as
  `maximum_contractually_payable_total_cents`.
- Affected: 4 submitted totals (INV-H4-000165, 000483, 000540, 000554).
- **Decision: keep `cap_excess_priced_at_cap=True` for Hospital 4.** The two
  hospitals differ on purpose, because their contract texts differ:

| | Hospital 1 | Hospital 4 |
|---|---|---|
| cap clause | Section 8 table only: "Maximum billable units per Patient per Service Day" | §6.1 adds: "A quantity in excess of the limit is not payable" |
| policy | `AuditPolicy.cap_breach_blocks_reconstruction = True` (`src/hospital_1/audit.py`) | `AuditPolicy.cap_excess_priced_at_cap = True` (`src/hospital_4/audit.py`) |
| corrected total after a breach | blank; the capped amount is a ceiling only | the limit priced; reconstructable |

  `tests/hospital_4/test_audit.py::test_h4_cap_policy_contrasts_with_h1` pins
  both the wording difference and both policies, so neither hospital's cap
  behaviour can drift into the other's.

**20. "Within N days".** The boundary day is read inclusively (item 16). The
text does not say, but the data and the Hospital 1 precedent both point that
way.

**21. Repeats and aggregates.**

- Clause 11.3 applies Section 6.2 to "any attempt" to bill a repeat, so a
  repeat counts towards the daily limit. We apply the same to the premium
  aggregate, which the contract does not address.
- No current invoice is affected.

**22. Descriptions missing a name slot.** Item 5. Treating them as the one
contracted service they fit would resolve most of the 822 ambiguous lines,
and it would also turn any uncontracted look-alike into a confident price
error.

## Not done

- **No semantic or LLM stage.**
- **No differential review against other participants' outputs.** None were
  available in this environment, and none were used.
- **Hospitals 3 and 5 are not implemented.** (This was the state at the time of
  this H4 stage. Hospitals 5 and 3 were implemented afterwards.)
