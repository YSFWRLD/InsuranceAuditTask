# Hospital 5 - decision log

Every reading of the Network Reimbursement Agreement (`INS-H5-2024-0731`)
that the audit depends on, and every policy the agreement does not force.

- **Contract** means the agreement states it.
- **Reading** means the wording admits more than one interpretation and this
  implementation chose one. The reason is always the text of the H5
  agreement.
- **Policy** means the agreement is silent and this implementation chose.

Billed prices never decide an interpretation, and neither do other
hospitals' labels or other participants' outputs. Where the H5 billing data
happens to agree with a reading, that is noted as consistency only. Data is
never used to identify a service or to price a line.

The switches are in `src/hospital_5/audit.py::AuditPolicy` and
`src/hospital_5/semantic.py::GateConfig`. A pre-commit review (section G)
re-examined the decisions that could inflate or weaken the result.

## A. Scope and architecture

**1. What runs where.** Contract parsing, matching, pricing, findings and
reconstruction are deterministic Python. Jev (TypeSafe SystemOne,
`jev-1.13.0`) is asked two kinds of bounded choice question and nothing
else:

- is a proposed token normalisation safe everywhere, safe only in context, or
  unsafe (prompt 001);
- which contracted candidate an under-specified description names (prompt 002).

The audit reads the committed reviews and calls nothing. No general LLM,
OpenRouter model, embedding or proposer is used for Hospital 5.

**2. Occurrences.** *Contract, clause 10.1.* The JSONL is canonical, and
each of the 1,057 physical records is kept (1,050 invoice numbers; 7 are
reused). A reused number's row represents the later occurrence, which is the
one that breaches 10.1. The earlier occurrence stays in every hospital-wide
index, and its findings are in `findings.csv`.

## B. Pricing (Sections 1-3)

**3. Facility column: from the invoice header.** *Policy; a data-schema
interpretation, not exact textual compliance.*
- Clause 1.2 and the Table 2 caption say the facility "recorded on a line
  item" selects the column.
- The H5 schema records no facility on a line. Line items carry only
  `line_id`, `invoice_id`, `line_no`, `service_date`, `description`,
  `quantity`, `unit_basis_as_billed`, `unit_price_cents` and
  `line_total_cents`. Each invoice header carries exactly one
  `facility_code`.
- So each line takes its invoice's facility. The data cannot show whether an
  invoice spans several facilities, and no line-level value is invented.
- An unrecognised facility code would make the invoice unpriceable
  (`unknown_facility`). None occurs.

**4. Plan tier: from the invoice.** *Contract, clause 1.3.*

**5. Order and rounding.** *Contract, clauses 3.1-3.4.*
- The order is bundle substitution → facility → tier → premium or uplift →
  cumulative discount → × quantity.
- Each step is rounded half up to the cent, including a multiplier of 1.
- The parser refuses any other stated order or rounding.

**6. Premium or uplift.** *Contract, 3.1(d) and 3.3.* The parser refuses a
service carrying both a Section 5 premium and a Section 6 uplift, because
the agreement never says how they combine. None does.

## C. Rules

**7. Section 5 threshold premium: the uplifted rate on the whole line.**
*Contract, with reading.*
- A premium is stage (d) of the unit rate (3.1, 3.3), so it applies to every
  unit of the line.
- Clause 5.1 assesses the Service Day aggregate "across all line items and
  all invoices". Clause 10.3 allows one billing per Patient, Service and
  Service Day, and the first billing stands (item 13).
- So in any reading where a line is the payable billing, every other same-day
  line of the service is a later repeat, which delivers nothing. The
  aggregate is therefore that line's own quantity (`day_quantity_if`).

**8. Section 6 non-business-day uplift.** *Contract, clause 2.2 and
Section 6.* It applies only to the nine listed services, on a Service Day
that is a Saturday or a Sunday.

**9. Section 7 bundles.** *Contract, clause 7.1.*
- Both services of a pair take their substituted rate, before the
  multipliers, when both are delivered to the same patient on the same
  Service Day, on any invoice.
- A partner counts as delivered even when its own billing is a repeat or
  excluded.

**10. Section 8 cumulative discount: the tier already exceeded before the
line, applied to the whole line.** *Reading; the contract is genuinely
ambiguous.*

The relevant text:
- **Table heading:** "Cumulative utilisation | Discount on subsequent units".
  A row reads "more than 80 procedures | 12%".
- **Clause 8.1:** utilisation is counted across the term and all patients,
  "in Service Date order", and "where two line items share a Service Date
  they are counted in ascending order of line identifier". "Where two
  thresholds are met the deeper discount applies".
- **Clause 3.1:** "The line total is then the resulting unit rate multiplied
  by the billed quantity."

The readings, for prior utilisation 79, quantity 5 and "more than 80":

| reading | effect | text it contradicts |
|---|---|---|
| **A (adopted)**: a line takes the tier that cumulative utilisation before it has exceeded | none of the 5 units is discounted | none: one unit rate per line (3.1); the discount begins once utilisation is "more than" the threshold, for the units that follow |
| B1: per unit, each unit beyond the threshold | unit 80 not discounted, units 81-84 discounted | 3.1 (the line would have two unit rates) |
| B2: per unit, each unit after utilisation exceeds the threshold | units 80-81 not discounted, units 82-84 discounted | 3.1 |
| C: the whole line, if utilisation including it exceeds the threshold | all 5 units discounted | "subsequent units" (units 80 and 81 are not subsequent to anything) |

- A is the only reading that contradicts no sentence of the agreement, so it
  is adopted.
- B1 is the strongest alternative: it gives "units" its literal per-unit
  meaning, at the cost of 3.1. It is implemented
  (`AuditPolicy(discount_mode="unit_split")`) and tested.
- The readings differ only on a line that starts at or crosses a threshold.
- Other consequences of reading A:
  - The deeper tier replaces the shallower one.
  - A certain repeat is not utilisation, because the service was delivered
    once.
  - A line dated outside the term is counted at its recorded date.
- Billed prices were not used to choose.

**11. Daily cap: a breach is flagged, and the total is not reconstructed.**
*Reading.*
- The only cap text in H5 is the Table 1 column heading "Daily cap" and its
  values (for example "24 hours"). No clause says what a breach does.
- It could mean that units above the cap are not payable, which would make
  the corrected total the capped quantity. It could equally mean that the
  billed quantity is wrong, and the right quantity cannot be read from the
  invoice.
- H5 does not choose between these. Hospital 4's agreement states "the
  excess is not payable"; H5's does not, and Hospital 4's wording is not
  imported.
- So: `daily_cap_exceeded` is flagged, `expected_total_cents` is left blank,
  and the cap-priced amount is reported as
  `maximum_contractually_payable_total_cents`. That amount is a ceiling on
  either reading.
- `AuditPolicy(cap_breach_blocks_reconstruction=False)` prices the cap
  instead.
- Boundary: the cap quantity itself is not a breach.
- Affected: 7 lines on 7 invoices; 6 have no other blank reason.

**12. Section 9 exclusions: directed; either side of the trigger in time;
day N included.** *Contract for the direction of effect; reading for time and
boundary.*
- The text is only the table: "<Service> | Not billable within | N days |
  Of this Service". There are 7 rows.
- **Which service is barred:** the left-hand service is "not billable". The
  trigger service stays billable.
- **Time direction:** "within N days of" states a distance between two
  Service Dates, not an order. The text contains no "after" or "following".
  Reading it as "after the trigger only" would add a word, so both sides of
  the trigger count.
- **Boundary:** "within N days" includes a date exactly N days away. The
  same day (distance 0) is within.
- **Scope:** the same patient, across invoices. Billing order is irrelevant.
- Every one of the 7 windows is boundary-tested: 0, ±N and ±(N+1).
- **All 6 violations found are exactly N days *before* their trigger.**
  Each depends on both readings: an after-only reading, or an exclusive
  boundary, would find none. These are therefore the least certain findings
  in H5.
- `AuditPolicy.exclusion_both_directions` and `exclusion_window_inclusive`
  switch the two readings.

**13. Clause 10.3 repeats: the first billing stands.** *Policy, as in
Hospitals 1, 2 and 4.*
- Clause 10.3 forbids a repeat but does not say which billing is the repeat.
- Order: invoice date, then invoice id, then occurrence, then line id.
- Later billings pay 0. A repeat on the same invoice is `duplicate_service`;
  one on another invoice is `cross_invoice_duplicate`.
- A certain repeat's own price is not audited. The day it repeats was
  delivered once, by another line, so there is no rate for it to be wrong
  against.

**14. Unknown service: no contract price, so the total is blank.** *Policy.*
- A description that a word contradicts for every contracted service names
  something the agreement does not price.
- The billed amount is never carried as a fallback.
- `AuditPolicy(unknown_service_line_carried_at_billed=True)` exists for
  comparison only.
- Affected: 11 invoices, for example `ELECT INFECT WARD BD OCC`. A twelfth,
  `DISP INPT REN PHARM`, is now an unresolved description instead (item 35).
  It stays blank and flagged.

**15. Findings that do not touch money.** Contract number, dates, unit
basis, and line or invoice arithmetic are flagged. The total is still priced
from the contract (3.1), because none of these clauses changes a rate.

**16. Malformed Service Dates: the days the string can stand for.** *Policy.*
- The legible parts are kept:
  - `2025-06-31` → any day of June 2025, or 1 July;
  - `31/02/2024` → any day of February 2024, or 2 March;
  - `2024-13-05` → the 5th of any month of 2024, or 13 May.
- Each day-based rule is evaluated over those days only: repeats, bundle
  presence, exclusions, the weekend uplift and the cumulative position.
- A day that cannot change the money does not blank the total. No final blank
  is caused by a malformed date; 8 lines carry one.

## D. Service identity

**17. Normalisation is proposed broadly and reviewed narrowly.**
- `normalization.py` proposes 266 token → contract-word readings for the 92
  corpus tokens that are not contract words. It uses prefixes, in-order
  subsequences, British/American spelling variants and one clinical acronym
  (ENT), plus whole-concept and specialty phrases.
- The phrase proposals exist to be rejected: they add a word the token does
  not stand for.
- Each proposal is one Jev question, whose state holds the contract
  vocabulary and the token's colliding contract words. No invoice text and
  no money is sent.

**18. Normalisation gates.** Thresholds are configurable (`.env.example`)
and were set before the run:
- **global:** choice safe, P(safe) ≥ 0.90 and confidence ≥ 0.85;
- **context-required:** not chosen unsafe, and P(safe) + P(context) ≥ 0.80;
- **otherwise rejected.**

Invariants, whatever the review says:
- a phrase that adds a qualifier or specialty is rejected, so normalisation
  never *creates* identity evidence;
- a phrase is never global;
- a token of fewer than 3 letters is never global (added in review; item 30).

A token with two or more accepted readings is contextual. Two recorded human
overrides apply: `bd` rejected (item 30) and `ent` allowed in context
(item 34).

Result: 62 safe-global, 45 context-required, 159 rejected. That gives 62
global tokens and 27 contextual tokens. Pinned in tests:
- NEURO → neurological: safe;
- ENDO → endocrine: context-required;
- IMG → diagnostic imaging: unsafe;
- CONSULT → palliative consultation: unsafe.

**19. Context-required tokens are resolved by structure, not by asking
again.**
- A reading survives only if some contracted service contains all its words.
- `EXT ENDO THTR TM` keeps only *endocrine*. `METAB ENDO PROC` keeps only
  *endoscopic*.
- A contextual token whose readings all fail stays **unread**. It never turns
  the description into a contradiction. This holds even when the token has
  only one accepted reading (`h5-matcher-2`; item 35).

**20. MATCHED needs the qualifier, the specialty and a concept word, and
exactly one surviving service.** Otherwise the line is AMBIGUOUS, carrying
its whole candidate set, or UNKNOWN (a word that was read contradicts every
service).

**21. The unit basis is a narrow tie-break.**
- **Textual ties:** the basis decides only among two or more tied candidates,
  and only when exactly one of them is contracted on the billed basis
  (301 lines).
- **After review (added after the first run; kept):** the basis also decides
  when a review has closed a cluster to its two or more contracted
  candidates.
  - Before review, the unread token (`beds`) kept an uncontracted reading
    open.
  - Once the pre-declared closure gate rules that out, the text leaves a
    genuine tie among contracted services, which is exactly the tie-break's
    stated condition.
  - It affects 68 lines in one cluster, `oncology service transfusion ?beds`.
    Without it: 884 proven totals instead of 939.
- A basis used to decide identity is never then called a `wrong_unit_basis`.
- A reviewed choice counts the basis as used only when the basis separates
  the chosen service from an alternative. Otherwise a wrong basis is still
  reported (tested).

**22. Missing-word questions and gates.**
- There is one question per unresolved (identity key × billed basis) cluster
  with 1-8 candidates: 33 clusters, 1,042 lines. Three ENT clusters are no
  longer asked (item 34), and one is new (item 35).
- Gates:
  - **a service:** Jev chose it with P ≥ 0.90 and confidence ≥ 0.80, **and**
    the description evidences all but at most one of its three name slots
    (added in review; item 29);
  - **closed to the contracted candidates:** two or more candidates, each
    with at most one slot missing, and P(any candidate) + P(ambiguous) ≥ 0.90;
  - **unknown:** P(none) ≥ 0.90;
  - **otherwise unresolved,** meaning open world, and the line has no
    contract price.
- Result: 28 services, 1 closed, 4 unresolved:
  - the 3 genuinely missing-word clusters (item 28);
  - `DISP INPT REN PHARM`. Jev said "one of the contracted dispensings" (P =
    0.96), but only the concept is evidenced, so the review-slot guard
    refuses to close it.
- Case A (`comprehensive consultation`): Comprehensive Palliative
  Consultation, P = 0.97.
- Case B (constructed `supervised consultation`, per procedure): ambiguous,
  P = 1.00. Jev refused to invent the missing specialty.

**23. Staleness.** A review counts only if two things match what the current
code would send:
- the SHA-256 of the exact request body (state, question and model);
- the input fingerprint (contract, normalisation version, matcher version,
  template and model).

The audit refuses to run on a missing or stale review, and tests pin this.
The reviewed-lexicon hash was removed from the missing-word fingerprint in
review (item 31).

## E. Reconstruction

**24. Financial equivalence.**
- Each line carries the set of corrected totals over every still-possible
  reading: candidate services, bundle partner, threshold side, discount
  tier, repeat, exclusion, and the possible days of a malformed date.
  `None` in the set means "no contract price".
- A total is submitted exactly when every line's set is one number.
- *Measured:* it added 3 totals.
- Across candidate services it resolved nothing. Surviving candidates price
  identically on 0 of 67 multi-candidate ambiguous lines, and an open-world
  line is never priced.

**25. Final coverage.** 1,050 rows, 76 flagged. 945 have complete pricing
and 939 carry a proven total. The 111 blanks, by primary reason:
- 90: a line's service is not settled (items 28 and 35);
- 4: a discount position depends on those unresolved lines;
- 11: an unknown service;
- 6: a daily-cap breach.

**26. Confidence.** These are evidence bands, not probabilities, and there
are no H5 labels:
- **0.85:** structural findings only, or text-identified and exact;
- **0.65:** the findings rest on hospital-wide state or a reading, or an
  identity rests on review or a tie-break;
- **0.40:** the total is unprovable, or a finding rests on an unknown
  service.

Jev's probabilities are evidence about one description and are not this
number.

## F. Questionable decisions that remain

- **Item 12:** all 6 exclusion findings depend on the both-sides reading and
  the inclusive boundary.
- **Item 10:** reading A over B1 at a crossing line.
- **Item 11:** a cap breach is blank rather than capped.
- **Item 3:** the facility is taken from the invoice header.
- **Item 28:** the three genuinely missing-word clusters. Each lacks a
  discriminator in its text, so they stay unresolved. A human sign-off, recorded with provenance,
  would be needed to resolve them. Neither lowering a gate nor re-asking the
  same question is acceptable.
- **Item 34:** the human-reviewed ENT reading, which moves 233 totals. It rests
  on ENT being the standard abbreviation for otolaryngology.

## G. Pre-commit review (2026-09-24)

**27. Why a review.** The first version reached 1,032 proven totals with a
single-candidate closure gate added after the first missing-word run. The
review asked which decisions could inflate or weaken the result, and
accepted any coverage the evidence supports.

**28. The four single-candidate clusters: the closure gate is removed.**

| cluster (identity key, billed basis) | raw description | present | missing | Jev P(candidate) / P(ambiguous) / P(none), confidence | lines | invoices | totals depending on it alone |
|---|---|---|---|---|---|---|---|
| supervised specimen analysis, per item | `SUPERVISED SPCM ANLY` | qualifier, concept | specialty | 0.89 / 0.11 / 0.00, 0.83 | 58 | 56 | 58 |
| supervised specimen analysis + unread `ent`, per item | `SUPV ENT SPCM ANLY`, `SUPV ENT SPCM ANALYSIS`, `SUPV ENT SPECIMEN ANLY`, `ANALYSIS SUPV ENT SPCM` | qualifier, concept; `ent` unread in the specialty slot | specialty | 0.80 / 0.20 / 0.00, 0.69 | 252 | 234 | 253 |
| urologic home visit, per visit | `UROL HM VST` | specialty, concept | qualifier | 0.89 / 0.11 / 0.00, 0.83 | 26 | 26 | 26 |
| routine physiotherapy session, per visit | `RTN PHYSIOTHERAPY SESS` | qualifier, concept | specialty | 0.88 / 0.12 / 0.00, 0.82 | 12 | 12 | 12 |

Together they decide 326 totals (1,032 → 706).

How the candidates were narrowed:
- **Elimination, in every cluster:** every contracted service other than the
  survivor was eliminated by a word the description does carry, never by the
  unit basis or by an absent word.
  - The specimen analyses: "Specimen Analysis" is a concept only one
    contracted service has, so the other `supervised` services are ruled out
    by the concept.
  - The home visit: the two dermatologic home visits are ruled out by
    "urologic". The contract has two qualifiers for dermatologic home visits,
    so an uncontracted qualifier variant is a realistic possibility.
  - The physiotherapy session: the hepatic and metabolic sessions are ruled
    out by "routine".
- **Normalisation involved:**
  - `spcm`, `anly`, `sess` and `urol` are global;
  - `hm` and `rtn` are contextual, each resolved by structure;
  - `ent` was rejected (P(safe) = 0.61), and so it is unread.
- **What was positively identified:** in each cluster, two of the three name
  slots, including the whole concept, and the combination is unique in the
  contract. The third slot is absent. The remaining service is the only
  contracted reading, but an uncontracted service differing only in the
  missing slot is not excluded by the text.

Structurally these four are the same shape as the 30 clusters accepted by
the pre-declared gate (one slot missing, eliminations by present words; three
of the 30 even have an unread token in the missing slot). **No general
structural rule separates them.** What separates them is that Jev's
confidence was below the bar set in advance.

The closure gate tried to bridge that gap by adding P(ambiguous) to
P(candidate). With one candidate, the ambiguous option cannot mean "one of
several contracted services". Mass on it is the reviewer hedging over the
missing word, and counting it as support reads doubt as confirmation. The
alternatives were rejected:
- a lower bar for single-candidate cases, chosen after seeing that these four
  miss 0.90;
- a new structural acceptance rule, which would have to accept all 34
  single-candidate clusters on structure alone, reversing the open-world
  stance of the design;
- re-asking the same questions until they pass.

**Verdict: removed.** The four clusters stay unresolved, and their invoice
totals are blank. The gate code and its environment switch are deleted, and
tests pin that a hedged single candidate is not accepted.

**29. A new general guard: a review may supply at most one missing name
slot.**
- A description naming only a concept ("SPECIMEN ANALYSIS"), or only a
  qualifier, does not identify a service, however confident the reviewer.
  The uniqueness would come from the contract's vocabulary, not from the
  description.
- The same bound applies to closing a multi-candidate cluster.
- It changes no current decision: every reviewed cluster misses exactly one
  slot.
- Adversarial tests pin it:
  - concept only;
  - an unread token in the missing slot;
  - a basis that disagrees with a reviewed service (still flagged);
  - a plausible uncontracted service (left unpriced).

**30. `bd → bedside`.**
- **Why Jev got it wrong:** the review state lists only *contract* words as
  collisions, and for `bd` that list was just "bedside". Jev therefore judged
  whether BD could abbreviate bedside, with no visible alternative, and said
  safe (P = 0.96).
- **What it really means:** in clinical shorthand BD is "bed", or "twice
  daily". Its only corpus use, `ELECT INFECT WARD BD OCC`, is a ward bed
  occupancy, which the contract does not carry.
- **Two changes:**
  - a general invariant: a token of fewer than 3 letters is never global,
    because two letters carry too little lexical evidence. `rm → room`
    becomes context-only;
  - a recorded human override (`REVIEW_OVERRIDES`) rejects `bd → bedside`,
    with its reasoning.
- **Output effect:** none. The one BD description still contradicts every
  service.

**31. Fingerprint re-stamp.**
- The lexicon changes above altered a hash that the missing-word fingerprint
  included, although all 35 request bodies (and the probe's) stayed
  byte-identical.
- Re-asking identical questions would only re-roll them, which matters most
  for the four borderline clusters.
- The lexicon hash was removed from the fingerprint. Everything the lexicon
  can change about a question is inside the request body, whose SHA-256 is
  still checked.
- The 36 stored records were re-stamped. Each keeps its answer and records
  the previous fingerprint and this reason. No Jev call was made in the
  review.

**32. Readings re-derived from the text alone.**
- Items 10 (discount), 11 (cap) and 12 (exclusions) previously cited billing
  patterns or Hospital 1's labels as support.
- The behaviour is unchanged. The justification now rests on the H5 text,
  and the remaining ambiguity is stated.

**33. Measured effect of the review.**

| | before review | after review |
|---|---|---|
| identified lines | 13,209 | 12,861 |
| ambiguous / unknown lines | 0 / 12 | 348 / 12 |
| pricing complete | 1,038 | 711 |
| correction reconstructable | 1,032 | 706 |
| blank totals | 18 | 344 |
| flagged | 76 | 75 |

- `INV-H5-000163` is no longer flagged, and `INV-H5-000147` loses
  `volume_discount_omitted`. Both discount findings sat on Supervised
  Otolaryngologic Specimen Analysis lines whose cumulative position now
  depends on the unresolved lines.
- `volume_discount_omitted` falls from 5 to 3 occurrences. Every other
  finding count is unchanged.

## H. Final targeted fix: ENT (2026-09-24)

**34. `ent → otolaryngologic`, human-reviewed, in context only.**
- Of the four clusters in item 28, only `SUPV ENT SPCM ANLY` (and its
  spellings) carries its discriminator.
  - `SUPV`, `SPCM` and `ANLY` read as supervised, specimen and analysis.
  - `ENT` sits in the specialty slot, and ENT (ear, nose and throat) is the
    standard abbreviation for otolaryngology.
  - The contract carries Supervised Otolaryngologic Specimen Analysis.
- The line was unresolved only because the normalisation gate did not accept
  `ent → otolaryngologic`: Jev chose "safe", but at P = 0.61. That is a
  normalisation gap, not a missing word.
- `artifacts/hospital_5/human_review_overrides.json` now records the reading
  with provenance:

  ```text
  token: ent
  canonical: otolaryngologic
  scope: hospital_5_contextual
  status: human_reviewed
  global: false
  decision: context_required
  ```

  plus its reason. The same file holds the `bd` rejection (item 30).
  `load_review_overrides` refuses an entry that is global, lacks a reason, or
  names no proposal.
- **Context only.** ENT is read as otolaryngologic only where a contracted
  Otolaryngologic service fits the other words. Otherwise it stays unread:
  - it fits in `SUPV ENT SPCM ANLY` and `CONT ENT TRANSP SVC`;
  - it does not fit in `ENT CONSULT`, `SUPV ENT CONSULT` or
    `EXT ENT TRANSP SVC`.
- **What resolves the line.** Only the raw text, the contextual reading, the
  H5 vocabulary and the slot the word occupies. Price, totals and rate
  compatibility play no part.
- **Why the other three differ.**
  - `SUPERVISED SPCM ANLY` has no specialty.
  - `UROL HM VST` has no qualifier.
  - `RTN PHYSIOTHERAPY SESS` has no specialty.

  Nothing in their text stands for the missing word, so they stay unresolved.
  **No closure rule was restored**, and tests pin that.
- **Missing-word questions.** The three ENT clusters (252 specimen lines and
  90 transport lines) are now identified by structure and are no longer
  asked. Jev had already resolved the transport ones to the same service at
  P = 0.94. Their stored reviews are kept, marked `retired`, and are not read.

**35. Matcher fix (`h5-matcher-2`).**
- A context-required token with a *single* accepted reading was being treated
  as fixed. Where its reading fitted nothing, it turned the description into a
  contradiction (UNKNOWN), when it should have stayed unread.
- Only the matcher changed; the gates did not. Such tokens are now contextual
  like any other: `ent`, and already `rm`, `svc`, `img`, `inpt` and others.
- Effect: `DISP INPT REN PHARM` (1 line, INV-H5-000294) is now an open
  AMBIGUOUS description instead of an unknown service. It became one new
  missing-word question, asked once. The guard refused to close it (item 22).
  The invoice stays flagged, now as `wrong_unit_basis|unit_price_mismatch`
  instead of `wrong_unit_basis|unknown_service`, and its total stays blank.
- The matcher version bump changes the review fingerprint. The 32 unchanged
  questions and the probe were re-stamped: their request bodies are
  byte-identical, their answers are untouched, and the history is kept in
  `fingerprint_migrations`.

**36. Measured effect of items 34-35** (from the state after section G):

| | after G | after H |
|---|---|---|
| identified lines | 12,861 | 13,113 |
| ambiguous / unknown lines | 348 / 12 | 97 / 11 |
| pricing complete | 711 | 945 |
| correction reconstructable | 706 | 939 |
| blank totals | 344 | 111 |
| flagged | 75 | 76 |

- The ENT specimen cluster is 252 lines on 234 invoices.
- **+233 totals are proven.** None lost, none changed. The contribution
  report's `ablation_without_human_review_overrides` reproduces the 706
  exactly.
- **Downstream discount findings.** INV-H5-000163 regains
  `volume_discount_omitted`: its prior utilisation of 276-336 is now certainly
  above the 180 tier. INV-H5-000147 gains `unit_price_mismatch` on one of its
  still-unresolved `SUPERVISED SPCM ANLY` lines. That line is billed at the
  undiscounted rate, which is wrong under its only contracted reading now
  that cumulative use is known to exceed the tier. The invoice was already
  flagged.
- Finding counts after H: `unit_price_mismatch` 32, `volume_discount_omitted`
  4, `wrong_unit_basis` 13, `unknown_service` 11. The rest are unchanged.
