# Hospital 3 - decision log

Every reading of the Hospital 3 contract package (`INS-H3-2024-0562`:
Base Agreement, Appendix B, Amendment No. 1) that the audit depends on, and
every policy the package does not force.

- **Contract** means the package states it.
- **Reading** means the wording admits more than one interpretation and this
  implementation chose one. The reason is always the Hospital 3 text.
- **Policy** means the package is silent and this implementation chose.

Billed prices never decide an interpretation or an identity, and neither do
other hospitals' labels or other participants' outputs. Where the Hospital 3
billing data happens to agree with a reading, that is noted as consistency
only. There are no Hospital 3 labels. Every switch named below is in
`src/hospital_3/audit.py::AuditPolicy` or `src/hospital_3/semantic.py::GateConfig`,
and `semantic_contribution.md` measures what each alternative would change.

## A. Scope and architecture

**1. What runs where.** Contract parsing, normalisation, matching, pricing,
findings and reconstruction are deterministic Python. Jev (TypeSafe
SystemOne, `jev-1.13.0`) is asked one kind of bounded choice question: which
contracted candidate an under-specified description names (prompt 001). No
general LLM, classifier or normalisation review is used. The audit reads the
committed reviews and calls nothing.

**2. Why Jev at all (measured before any review).** *Policy.*
- The deterministic matcher identifies 10,751 of 11,655 lines (92.2%). 890
  lines stay ambiguous: 838 omit exactly one name slot (35 clusters) and 52
  are genuine ties between two contracted services.
- Those lines alone block the corrected total of 501 of the 939 invoice
  records. The deterministic audit proves 353 of 932 totals.
- Each is a bounded choice among named contracted services, the case the
  brief allows Jev for. So the Hospital 5 missing-word question was reused;
  the normalisation question was not, because Hospital 3's vocabulary is
  deterministic (item 17).

**3. Occurrences.** *Contract, clause 10.1.* The JSONL is canonical; its 939
physical records (932 invoice numbers, 7 reused) are kept apart. A reused
number's row represents the later occurrence, which breaches 10.1; the
earlier stays in every hospital-wide index, and its findings are in
`findings.csv`. The CSVs are cross-checked against the JSONL on every run.

## B. The contract package

**4. Precedence.** *Contract, clause 1.3.* Amendment > Appendix B > Base
Agreement, built in as `contract.PRECEDENCE`. Rates are dated entries tagged
with their document; `rate_entry_on` takes, among the entries in force on a
Service Date, the one of highest precedence, and refuses a tie. Where two
documents state the same rule, the parser requires them to agree rather than
preferring one: the Appendix B "Daily cap" column must equal Section 7, and
the amendment's "Rate to 31 December 2024" must equal Appendix B. They do.

**5. The amendment operates by Service Date.** *Contract, A1.1.1-A1.1.2.*
A line dated on or after 1 January 2025 takes the substituted rate; an
earlier one keeps the Appendix B rate; "the date on which an invoice is
issued is irrelevant". The invoice date is never read for pricing.
- 282 lines of the seven amended services are priced under Appendix B and
  280 under A1.2. 28 of the Appendix B ones are dated in 2024 on invoices
  issued in 2025, and are billed at the 2024 rate: correct by Service Date.
- 4 lines bill the wrong period: 3 bill the 2025 rate for a 2024 Service
  Date (`amended_rate_applied_before_effective_date`) and 1 bills the 2024
  rate in December 2025 (`amended_rate_not_applied`).
- Boundary tests pin 2024-12-31, 2025-01-01 and 2025-01-02 for all seven
  services, with invoices issued later.

**6. The two added services.** *Contract, A1.3.* Advanced Dermatologic
Nutritional Support and Elective Pulmonary Imaging Interpretation "were not
contracted before that date and are not billable in respect of earlier
Service Dates".
- Identity is read from the text alone, never from the date. A line
  identified as one of them with an earlier Service Date gets
  `service_not_contracted_on_date`, a different finding from
  `unknown_service` (text naming nothing contracted).
- Its corrected line amount is 0, because the text says "not billable" (the
  same treatment as an exclusion window). *Policy;*
  `uncontracted_on_date_payable_zero=False` declines instead.
- In the data: 81 lines of the two services, all dated on or after 1 January
  2025. None occurs earlier, so the finding is tested but never raised.

**7. Other terms unaffected.** *Contract, A1.4.1.* Premiums, caps, bundles,
exclusions and conventions apply to substituted rates as to the old ones:
the weekend uplift of Specialist Psychiatric Discharge Planning, the cap of
Intensive Infectious Anaesthesia Administration and the tiers of Assisted
Urologic Endoscopic Procedure all apply to the 2025 rate. No bundled service
is amended; a bundled rate replaces whichever rate is in force (8.1).

**8. Settled invoices.** *Contract, A1.4.2; not operationalised.* The clause
exists: "Nothing in this Amendment affects an invoice already settled before
the effective date." The data has no settlement field, so whether an invoice
was settled cannot be evaluated, and the audit does not pretend to: no code
reads or infers settlement. No line is affected either way. A line it could
touch would be an amended-service line dated 2025 on an invoice issued before
2025, and none exists. (One unamended line dated 2026 on a 2024 invoice is
flagged for its date.)

**9. Facility and plan tier.** *Contract, clause 1.5.* One facility, F-MAIN;
"no facility differential applies, and all plan tiers are reimbursed
identically". Both multipliers are 1, and both stages are kept in the
pricing order. All 939 records say F-MAIN; the tiers are BRONZE, SILVER and
GOLD. A different facility code would be flagged `facility_mismatch` and
priced unchanged; none occurs.

**10. Order and rounding.** *Contract, clauses 3.1-3.3.* Bundle substitution
→ facility → plan tier → premium or uplift → cumulative discount →
× quantity; half up to the cent after each step. The parser refuses any other
stated order or rounding, and a service with both a threshold premium and a
weekend uplift (none has).

## C. Rules

**11. Threshold premium: the aggregate a payable line is assessed on.**
*Reading.*
- 4.1 assesses "the aggregate quantity of the Service delivered to the
  Patient on the Service Day"; 10.3 lets a Service be billed once per Patient
  and Service Date, the first billing standing (item 16).
- A second billing of the same Service that day is a repeat: it is paid 0 and
  delivers nothing more. So the aggregate against which the payable line is
  assessed counts no repeat. The aggregation is implemented across lines and
  invoices (`AuditContext.day_aggregate`); by construction it comes to the
  payable line's own quantity.
- The literal alternative sums every billed line, repeats included
  (`repeats_count_toward_day_aggregate=True`); both are tested with lines on
  two invoices.
- In the data only 4 patient-service-day groups have two lines, each an exact
  cross-invoice duplicate of a service with no premium or cap: the two
  readings change **0** rows.
- Boundaries tested: threshold − 1, threshold, threshold + 1 ("more than").

**12. Weekend uplift.** *Contract, 2.2 and Section 5.* Saturday or Sunday by
Service Date; no public-holiday logic. A malformed date whose possible days
include both is uncertain.

**13. Cumulative discount: the tier already exceeded before the line, for
the whole line.** *Contract, 6.1, with one residual reading.*
- 6.1 is explicit: whole term, all patients, Service Date order, "up to but
  excluding the line item being priced", ties by ascending line identifier,
  the deeper discount where two thresholds are met. A tier applies when prior
  utilisation is "more than" its threshold, so prior = threshold is not
  discounted.
- **A line that crosses a threshold** (prior 98, quantity 5, threshold 100)
  is priced at the tier its prior utilisation has exceeded, for every unit,
  because 6.1 counts to the line item and 3.2 gives a line one unit rate. The
  tier starts with the next line.
- The alternative splits the line per unit (`discount_mode="unit_split"`;
  implemented and tested). It changes 14 rows (15 lines; one invoice has
  two).
- A certain repeat is not utilisation. No line dated outside the term is a
  discount service, so how such a line counts does not arise.
- *Consistency only:* all 15 lines in the data that start at or cross a
  threshold are billed exactly at the whole-line rate.

**14. Daily cap: a breach is flagged, and the total is not reconstructed.**
*Reading.*
- Section 7 ("Maximum units per Patient per Service Day") and the Appendix B
  column state a number and nothing else. Hospital 4's "the excess is not
  payable" has no Hospital 3 counterpart and is not imported.
- A breach proves the billed quantity wrong without saying what it should be.
  So: `daily_cap_exceeded`, a blank expected total, and the capped amount as
  `maximum_contractually_payable_total_cents`. The cap itself is not a breach.
- 7 rows breach a cap; 4 are blank for that reason alone.
  `cap_breach_blocks_reconstruction=False` would price them at the cap
  (4 rows change).

**15. Exclusion windows: directed; either side of the trigger; day N and
the same day included; same patient.** *Reading; the least certain in H3.*
- The text is only the table "Service | Not billable within | Of this
  Service", 10 rows. There is no clause, and no "after", "before",
  "following" or "either direction" (the parser refuses to go on if one
  appears).
- The left-hand service is the one "not billable"; the trigger stays billable.
- "Within N days of" states a distance between Service Dates, not an order,
  so both sides count; a date exactly N days away is within; distance 0 is
  within. The rule is about a patient's care, so it is per patient, across
  invoices.
- Tested at 0, ±5, ±N and ±(N + 1), with the switches
  `exclusion_both_directions` and `exclusion_window_inclusive`.
- **All 3 findings have the excluded service before its trigger**: 19 days,
  exactly 30 days and exactly 14 days before. None would exist under an
  after-only reading; two would not exist with day N excluded.
- A row whose only finding is such a violation gets **low** confidence (1
  row, INV-H3-000052). The other two rows are flagged for independent
  reasons, but their totals depend on the reading.

**16. Repeats.** *Contract 10.3; policy for which billing stands.* The first
billing stands, by invoice date, then invoice id, then occurrence, then line
id. A later one pays 0: `duplicate_service` on the same invoice,
`cross_invoice_duplicate` on another. Only a line whose identity is settled
can make a certain repeat; an unresolved line makes a possible one, which is
uncertainty. A reused invoice number is a different finding
(`duplicate_invoice_id`).

## D. Service identity

**17. The lexicon is Hospital 3's own.** *Policy.*
- Every reading is a Hospital 3 contract word reached from the invoice token
  by a letter rule: prefix (`pulm`), same-first-letter subsequence (`thtr`)
  or British/American spelling. No other hospital's table or review is
  consulted, and a test forbids importing one.
- A token is **global** when exactly one reading exists, it has at least
  three letters, and in the Hospital 3 corpus it never shares a description
  with another word of the same qualifier or specialty slot: 75 tokens.
- Otherwise it is **contextual** (21 tokens, for example `cr` → care /
  cardiac / comprehensive / conference / critical, `bd` → bed / bedside,
  `endo` → endocrine / endoscopic). A contextual reading survives only where
  some contracted service contains every word of the description; if none
  does, the token stays unread and never becomes a contradiction. Readings
  that lead to different services make the line a tie, never a choice.
- Evidence per token is in `artifacts/hospital_3/vocabulary_evidence.json`.

**18. ENT → otolaryngologic, in context only.** *Reading; human-reviewed
and accepted by the author on 2026-09-25 (prompt 002), recorded in
`normalization.py::ACRONYM_REVIEWS`. It is a contextual interpretation and
never a global alias.*
- ENT (ear, nose and throat) is the clinical abbreviation for
  otolaryngology; no letter rule reaches it.
- Hospital 3 evidence: `ent` appears in 19 spellings (15 distinct token
  sets, 424 lines), always in the specialty slot and never beside another
  specialty word. In all 15 the other words, read through the lexicon, are
  exactly those of a contracted Otolaryngologic service; together they cover
  all 7 Otolaryngologic services. 7 of the 15 also occur with
  "otolaryngologic" spelled out (for example `std ent disch plng` and
  `discharge otolaryngologic plng std`).
- No Hospital 3 evidence points to another meaning: no contract word begins
  with "ent", no document mentions an enteral service, and inside other words
  ("inpatient", "vent") the letters are never read as a token. With `ent`
  unread, `intens ent anaes admin` would tie Intensive Infectious with
  Intensive Otolaryngologic Anaesthesia Administration; nothing in the text
  supports "infectious".
- The decision rests on the description text and the contract vocabulary
  only (`build_lexicon(contract, descriptions)`); no price, total, invoice id
  or audit result is an input.
- It is contextual: `ENT CONSULT` (no Otolaryngologic Consultation is
  contracted) leaves `ent` unread. In the data it fits every time; it stays
  unread on 0 lines.
- **It decides 319 proven totals** (848 with it, 529 without). This is the
  largest single interpretive dependency in Hospital 3. Switching it off is
  `build_lexicon(include_acronyms=False)`.

**19. MATCHED needs the qualifier, the specialty and a concept word, and one
surviving service.** *Policy, as in Hospitals 2, 4 and 5.* Otherwise the line
is AMBIGUOUS, carrying its whole candidate set, or UNKNOWN (a word that was
read contradicts every service; 14 lines). 823 matched lines omit part of a
multi-word concept (`postop card wd occ`), which the rule allows.

**20. The unit basis is a narrow tie-break.** *Policy.* It decides only
between two or more tied candidates, and only when exactly one of them is
contracted on the billed basis: 85 lines in 4 clusters. It never turns a
missing-word or unknown description into an identity. A basis used for
identity is never then called a `wrong_unit_basis`; otherwise a wrong basis
is flagged on its own (14 rows).

**21. Jev missing-word review and its gates.** *Policy; gates fixed before
any Hospital 3 review was run.*
- One question per unresolved (identity key × billed basis) cluster with
  1-8 candidates: 38 questions, 890 lines. The state holds words only: no
  price, rate, quantity, total, date or id. The only digits in a stored
  request body are the model name (`jev-1.13.0`) and each cluster's line
  count; the word "price" occurs only in the fixed instructions that forbid
  price evidence. Tests check both.
- The gates are Hospital 5's after its pre-commit review, unchanged:
  - **a service:** P ≥ 0.90 and confidence ≥ 0.80, and the description
    evidences all but at most one of its three name slots;
  - **closed to the contracted candidates:** two or more candidates, each
    missing at most one slot, P(any candidate) + P(ambiguous) ≥ 0.90;
  - **unknown:** P(none) ≥ 0.90;
  - **otherwise unresolved** (open world, no contract price). There is no
    single-candidate closure.
- Result: 34 services (818 lines), 3 closed ties (52 lines), 1 unresolved:
  `dispensing inpatient pharmaceutical` (20 lines), P = 0.89. It stays
  unresolved; no rule was added after seeing it.
- The 3 closed ties do not reach a price: their two candidates price
  differently (for example Inpatient Metabolic vs Musculoskeletal Theatre
  Time, 210.00 vs 92.50), and the billed basis does not separate them.
  Their 47 totals stay blank.
- **Jev decides 495 proven totals** (848 with it, 353 without).
- Requests, answers, gated outcomes and staleness fingerprints are committed
  (`artifacts/hospital_3/jev_missing_word_*`); the audit refuses a missing or
  stale review.

## E. Reconstruction

**22. Financial equivalence.** *Policy, as in Hospital 5.* Each line carries
the set of corrected totals over every still-possible reading: candidate
services, rate period (malformed dates), bundle partner, threshold side,
discount tier, repeat and exclusion. A total is submitted exactly when every
line's set is one number. It added 6 totals, all from uncertainty that does
not reach the amount; no ambiguous line priced identically across
candidates.

**23. Final coverage.** 932 rows, 70 flagged, 852 with complete pricing,
**848 proven totals**, 84 blank. Blanks by primary reason:
- 47: a closed tie whose candidates price differently (item 21);
- 20: the one unresolved missing-word cluster;
- 13: an unknown service (no contract price; the billed amount is never
  carried);
- 4: a daily-cap breach (item 14).

**24. Findings that do not touch money.** Contract number (6 rows quote
another hospital's contract), dates, unit basis, and line or invoice
arithmetic (7 each) are flagged. The total is still priced from the contract,
because none of these changes a rate. Line arithmetic (did the hospital
multiply correctly?) and contract pricing (was the rate right?) are separate
findings.

**25. Malformed Service Dates.** *Policy, as in Hospital 5.* The legible
parts are kept: `2025-02-30` is some day of February 2025 or 2 March;
`31/02/2024` some day of February 2024 or 2 March; `2024-13-05` the 5th of
some month of 2024 or 13 May; `2024-00-17` the 17th of some month of 2024;
`not-a-date` any day. Every day-based rule, including which side of the
amendment date the line falls, is evaluated over those days. 7 lines carry
one; none blanks a total.

**26. Confidence.** Evidence bands, not probabilities:
- **0.85:** structural findings only, or text-identified and exact;
- **0.65:** findings rest on hospital-wide state or a reading, or an identity
  rests on review, a tie-break or the ENT reading;
- **0.40:** the total is unprovable, a finding rests on an unknown service,
  or the only finding is a reading-dependent exclusion (item 15).

## F. Decisions to review

- **Item 18:** the ENT reading, 319 totals. Human-reviewed and accepted, but
  still the largest single dependency.
- **Item 21:** the 34 Jev service decisions, 495 totals. Each supplies one
  omitted name slot.
- **Item 15:** all 3 exclusion findings depend on the two-sided reading.
- **Item 13:** whole-line versus per-unit discount at a crossing line
  (14 rows).
- **Item 14:** a cap breach is blank rather than capped (4 rows).
- **Item 11:** repeats do not add to a day's aggregate (0 rows on this data).
