# Decision log — Hospital 2

The readings, assumptions and open questions behind the Hospital 2 audit, and
what was done about each. Figures come from the current run; the generated
`audit_report.md` beside this file has the live counts.

There are no Hospital 2 labels. Nothing here was tuned towards a number of
flagged invoices, and no accuracy is claimed.

---

## Sources

**1. The JSONL is canonical, and occurrences are never merged.** There are
1,132 invoice records and 1,125 invoice numbers: seven numbers are used by two
records, each with a different patient, date and set of lines. Every record is
its own occurrence (`INV-H2-000054#0`, `INV-H2-000054#1`), with its lines taken
from the JSONL nesting, never from a join on `invoice_id` and never from digits
inside a line id. The two CSVs agree with the JSONL field for field (checked on
every run).

**2. The Markdown contract is canonical.** The `.txt` rendering is
cross-checked by test (every service name and rate appears in it). The two PDFs
were not parsed.

## Contract readings

**3. Every service clause is read completely or not at all.** The 76 service
clauses use a closed set of sentence templates. The parser recognises each
sentence explicitly and raises on any it does not recognise. Quantities stated
twice ("twelve (12)") must agree in words and digits. Each bundle is stated in
both partners' clauses, and the two statements must agree. The rule counts
(76 / 8 / 9 / 8 / 8 / 6 / 3) are derived from the text, not asserted into it.

**4. Clause 4.2's "per hour, per item" is a basis of its own.** It is kept as
`per_hour_per_item`, the token invoices use for it (163 lines), and is not
collapsed into `per_hour`. A 4.2 line billed `per_hour` is a `wrong_unit_basis`,
and so is any other service billed `per_hour_per_item`. The shared `UnitBasis`
enum does not carry this token, so Hospital 2 reads the raw token. The shared
enum was deliberately left alone: changing it would alter a file Hospital 1's
freeze depends on.

**5. Service Day = the calendar date on the line.** Clause 2.2 defines a
Service Day as 07:00 to 06:59, but invoices record dates only. Its second
sentence assigns a service delivered within one calendar day to that date, and
the data offers nothing finer. Business Day (2.4) is read from the same date,
with Saturday and Sunday as non-business days.

**6. Daily aggregates, bundles, exclusions and cumulative utilisation span
invoices.** Clause 3.4 aggregates "the Patient on that Service Day", the bundle
clauses speak of "the same Patient on the same Service Day", 3.6 is about the
patient's history, and 3.5 counts across "the whole term" and "all Patients".
None of these is about an invoice, so every index is hospital-wide.

**7. Cumulative utilisation counts every billed line, prior-exclusive, and a
line is never split.** Lines are ordered by Service Date, then line id (3.5). A
line's own units are excluded from its count (2.7). If the count before the line
exceeds a threshold, the whole line takes the discount. Where two thresholds are
exceeded, the deeper discount applies, uncompounded (3.5). Lines with a
malformed date cannot be placed in the sequence; they are treated as possibly
earlier than every other line, which widens the uncertainty rather than being
dropped. Lines on invoices quoting the wrong contract number still count, since
they were billed.

**8. "Within N days" is inclusive, and measured in either direction.** An
excluded service is caught when its trigger falls `|Δ| ≤ N` days away, before or
after (3.6). The contract does not say whether day N itself counts. The
inclusive reading matches Hospital 1's labelled convention and is recorded here
as a reading, not a derivation. An excluded line is not payable. Only certain
trigger dates can prove a violation; a date that only *might* carry the trigger
(an unresolved description) is recorded as uncertainty.

**9. A cap breach is detected, not reconstructed.** Eight units against a cap
of six prove the invoice wrong without revealing what was delivered. The row
carries an empty `expected_total_cents` and the capped ceiling in
`maximum_contractually_payable_total_cents`.

**10. Bundle and uplift kinds never collide in this contract.** The contract
never says how a daily-aggregate uplift and a non-business-day uplift would
combine; in this contract no service has both. The parser warns if that ever
changes.

## Invoice-level readings (Article XIII)

**11. `invoice_date` is the submission date.** Clause 13.1 counts 60 days from
discharge to *submission*, and the data records only an invoice date. No
invoice in the data is late (the invoice-to-discharge gap is 0–10 days). The
check is implemented anyway, with an evidence basis of *inferred*.

**12. Reused invoice numbers: the first occurrence establishes the number,
and the submitted row represents the later one alone.** Each later occurrence
carries `duplicate_invoice_id` (13.6). Internally every occurrence is audited
separately and never merged.

The submission has one row per number (its format), and that row represents the
**later** occurrence: the record whose submission breaks 13.6. Everything on the
row is that occurrence's:

- the billed total;
- the expected total;
- `pricing_complete` and `correction_reconstructable`;
- the confidence;
- the findings, including `duplicate_invoice_id`.

*Revised policy.* An earlier version also copied the earlier occurrence's
findings onto the row. That produced rows such as
`unit_price_mismatch|duplicate_invoice_id` whose billed total belonged to a
different invoice than the price error. The earlier occurrence's findings are
now **not** copied onto the row. They remain in `findings.csv` (with their own
`occurrence_id`), in the audit report's "Reused invoice numbers" table, and in
the internal occurrence results. No evidence is discarded.

The change affected 3 of the 7 reused numbers. Their rows lost categories that
belonged to the earlier record:
- `INV-H2-000104` lost `service_date_out_of_contract`;
- `INV-H2-000147` lost `unit_price_mismatch`;
- `INV-H2-000549` lost `bundle_not_applied` and `volume_discount_omitted`.

No row changed flag status, and no occurrence-level finding changed. This is
Hospital 1's development convention (its labels described the later record),
used as supporting precedent, not as a Hospital 2 label.
`physical_occurrence_ids` in `predictions.csv` names every record.

**13. A service date after the invoice date is flagged as *inferred*.** Unlike
Hospital 1, this contract has no clause saying so outright. It follows from 2.3
(the date the service was delivered) together with 13.1 (invoices follow
discharge). Nine lines are dated after their invoice. Seven are flagged with
that basis. The other two also fall outside the contract term, and a date
outside the term is reported once, as out of term, not twice.

**14. Not carried over from Hospital 1: same-service-same-day duplicates.**
Hospital 1's clause 11.4 has no counterpart here, and clause 3.4 expressly
contemplates several lines per Service Day. Services dated outside the episode
of care (29 lines) are likewise not flagged: no clause makes that a billing
error.

## Service identity

**15. Text decides identity; price never does.** The matcher and the semantic
stage take a description (and, for one narrow tie-break, a billed unit-basis
token). They never see a price, a total, an invoice id or a patient id. Tests
check the function signatures, and that the identity code contains no reference
to those fields.

**16. `/SA-####` suffixes are billing references, not service codes.** They are
stripped before matching and clustering. 506 raw descriptions become 441
normalised clusters.

**17. A match is "clear" only when the qualifier and specialty are both in the
text.** Every H2 service name is qualifier + specialty + type; the parser checks
that the three vocabularies are disjoint. `ADV ENDOSCOPIC PROC` has one
plausible candidate, but without its specialty it cannot be told apart from an
uncontracted "Advanced <other> Endoscopic Procedure". A wide score margin does
not help: every alternative loses the same missing word. This rule is the fix
for Hospital 1's one known defect, applied from the start here. Result: 358
clusters matched deterministically, 14 UNKNOWN on a contradicting word, and 69
unresolved (52 missing a qualifier or specialty, 17 genuine ties).

**18. The unit-basis tie-break, and the evidence it consumes.** Only where the
text leaves a genuine tie (17 clusters) and exactly one tied candidate is
contracted on the billed basis does the basis choose. That happened on **384
lines**, all recorded with `used_unit_basis_for_identity = true`. None of them
can carry `wrong_unit_basis`: the basis cannot both identify the service and
accuse it.

**19. The semantic stage ran once, and nothing it did not verify is used.**
The classifier (OpenRouter, `z-ai/glm-5.3-flash`, prompt
`001_service_classifier@2b8f543469fb`) was run on 2026-09-22 from 00:43Z to
01:20Z. Classification was attempted for all 69 semantic clusters. With bounded
retries, that took 75 OpenRouter HTTP attempts in total: 65 clusters finished
in 1 attempt, 2 in 2, and 2 in 3.

- 67 returned a valid decision: 64 AMBIGUOUS and 3 MATCHED.
- 2 failed after 3 attempts each, with HTTP 402 (credit exhausted; see
  *Stopping decision*): `ent rehab prog` (27 lines) and `std isol rm occ`
  (28 lines).

Jev (`jev-1.13.0`, prompt `003_jev_verifier@7a2a82a900a9`, direct API) judged
all 67 decisions at 02:19Z:

- It accepted all 64 AMBIGUOUS decisions. Those clusters stay AMBIGUOUS
  (item 21).
- It did not accept any of the 3 MATCHED proposals. None reached the 0.90
  gate in either direction, so all three stay unresolved:

| cluster | proposed service | P(ACCEPT) | P(REJECT) |
|---|---|---|---|
| `asst diag imaging` (30 lines) | Assisted Musculoskeletal Diagnostic Imaging | 0.41 | 0.59 |
| `extended hm vst` (22 lines) | Extended Vascular Home Visit | 0.35 | 0.65 |
| `vasc imaging interp` (28 lines) | Preoperative Vascular Imaging Interpretation | 0.41 | 0.59 |

Counts after the run (clusters and line occurrences are different units):

| count | value |
|---|---|
| `total_normalized_clusters` | 441 |
| `deterministic_matched_clusters` | 358 |
| `deterministic_unknown_clusters` | 14 (decided from text; not semantic work) |
| `semantic_required_clusters` | 69 (2,093 line occurrences) |
| `semantic_verified_matched_clusters` | **0** |
| `semantic_verified_ambiguous_clusters` | 64 |
| `semantic_pending_clusters` | 5 (135 line occurrences: 2 classifier failures + 3 unaccepted matches) |
| `unit_basis_tiebreak_line_occurrences` | 384 (27 of them in pending clusters, so still provisional) |
| `currently_ambiguous_line_occurrences` | 1,709 |
| `currently_unknown_line_occurrences` | 14 |
| `total_unresolved_or_unknown_clusters` | 83 (`unresolved.json` lists them by group) |

**The semantic stage did NOT increase verified service-mapping coverage.**
Before and after the run:

- the same 74 rows are flagged;
- every submitted value in `submission.csv` is byte-identical;
- the numeric columns of `predictions.csv` (`pricing_complete`,
  `correction_reconstructable`, confidence, totals) are identical.

What changed is evidence only. 1,601 `ambiguous_service_description`
advisories (which never flag on their own) now appear in `findings.csv`, and
the uncertainty text of 841 prediction rows now says "semantic_verified"
instead of "unresolved". The semantic run was still informative: Jev, used as
a separate verifier, accepted the classifier's 64 AMBIGUOUS decisions and did
not accept any of the 3 MATCHED proposals. There are no H2 labels, so this is
not a measure of accuracy.

**20. Unresolved lines still exist.** Each is carried as the set of services it
might be:
- It widens cumulative utilisation, Service Day aggregates and bundle detection
  for every candidate.
- Where that straddles a threshold, the affected lines accept any rate the
  plausible readings allow.
- The line itself is flagged only if its price is wrong under *every* reading.
- No invoice containing an unresolved line gets a submitted corrected total
  (item 24).

**21. Verified ambiguity is advisory.** If the classifier says AMBIGUOUS and
Jev accepts it, the line gets an `ambiguous_service_description` advisory
(23.1). This never flags an invoice on its own: our inability to identify a
service is not proof that the provider's description is insufficient. Jev
accepting AMBIGUOUS or UNKNOWN never becomes a match.

**22. An unknown service is not priced.** A description that contradicts every
candidate (for example `ONC WD BD OCC`, an oncology ward bed this contract does
not carry) is flagged `unknown_service`, with an *inferred* evidence basis. It
gets no expected amount, so the invoice's `expected_total_cents` is empty.
Hospital 1 carried such a line's billed amount through; this brief asked that
unreconstructable amounts not be invented, and Hospital 2 follows that.

**23. Jev: two modes, one interpretation.** (The run in item 19 used the direct API.)
- *Direct API* (`semantic h2 jev`) calls TypeSafe SystemOne,
  `POST https://api.typesafe.ai/v1/systemone`.
  - It uses a bearer key: `TYPESAFE_API_KEY`, falling back to `JEV_API_KEY`.
  - The body is `{"state", "model": "jev-1.13.0", "questions"}`.
  - `JEV_API_URL` is an optional override only.
  - The transport is one isolated function, `jev_http_transport`.
- *Playground* (`jev-export` / `jev-import`) writes `jev_state.json`
  (`{"cases": {...}}`) and `jev_questions.json`. The questions use the
  Playground's `{"type": "choice", "instructions", "criteria"}` format, loaded
  from `prompts/hospital_2/003_jev_verifier.md`. That prompt supersedes 002,
  which used an invented `question`/`options` shape and was never put to Jev.

Both modes produce `{"choice", "probabilities", "confidence", "model"}` through
the same validator. The validator is all-or-nothing, and each case and question
id is derived from the cluster id. A verdict is bound to the exact classifier
answer, and the identity evidence, that it judged. Both modes pass through the
same 0.90 gate. `jev_results.json` is this project's own input format, because
the Playground's native export format is not documented here.

*Corrected after a live call.* The TypeSafe API returns its verdicts under
`answers`, with `usage` metadata beside them. The first parser expected
`results` and read every top-level key as a question id, so it refused real
responses.

It also assumed Jev's `confidence` equals the top probability. It does not: a
live answer had P(ACCEPT) = 0.92 with confidence 0.87. Confidence is now
validated only as a number in [0, 1] and stored as reported.

The gate was always, and still is, decided by the ACCEPT and REJECT
probabilities alone.

*Unit-basis evidence.* One canonical builder (`jev_case`) makes every case for
both modes. The billed unit basis is withheld unless the decision under
verification consumed it as a tie-break. Then, and only then, the case carries
`identity_evidence = {"used_unit_basis_for_identity": true, "billed_unit_basis": ...}`,
because a judge cannot verify a decision without the evidence behind it.

The classifier never receives the billed basis, so today every case is
`false`. The deterministic per-line tie-break in the audit is not a semantic
decision and is never sent to Jev.

If a verified semantic decision does consume a basis, it identifies only the
lines billed on that basis, and those lines cannot carry `wrong_unit_basis`.
Lines billed on another basis get no identity from it. Prices, totals, ids and
rate-derived hints are never sent in either case.

**24. `expected_total_cents` is submitted only when the correction is
defensible.** A number in that column is a claim to know what the invoice
should have totalled. It is filled only when `correction_reconstructable` is
true, and otherwise left as an empty field: never the billed total, zero, a
partial sum or a cap-adjusted guess. The three states:

- `pricing_complete`: every line was priced exactly, from resolved identities
  and determinate inputs.
- `correction_reconstructable`: additionally, no finding blocks reconstruction.
- `expected_total_cents`: filled if and only if reconstructable.

*Revised.* An earlier version counted an invoice as `pricing_complete`
whenever a total could be computed, including one that priced an unresolved
line at the reading its billed rate matched. It also submitted that number.
That figure is now `provisional_expected_total_cents`. It is kept as a
diagnostic column in `predictions.csv` and never submitted.

Current state: 238 invoices are `pricing_complete`, 237 are reconstructable,
and 888 submitted rows have a blank expected total. The one complete but not
reconstructable invoice carries a reconstruction-blocking finding.

This updates automatically. When a semantic mapping is verified, the next
`audit h2` prices those lines exactly, and any invoice with no other blocker
becomes reconstructable with its total filled in. A test takes one invoice
through exactly that transition.

## Monetary reconstruction is impossible when

- a daily cap is breached (the delivered quantity is unknown);
- a line names no contracted service;
- a line's service identity is unresolved, or a threshold it depends on is
  straddled because of an unresolved line elsewhere;
- a service date is malformed.

## Evidence bases

Every finding in `findings.csv` names what it rests on:

- *contract*: a clause applied to fully known inputs.
- *arithmetic*: the invoice disagrees with itself.
- *data*: the field is not valid data at all.
- *inferred*: rests on a reading above (items 11, 13, 17 and 22).

## Genuinely ambiguous wording

- Clause 2.2's 07:00–06:59 Service Day cannot be applied to date-only invoices (item 5).
- "Within N days": inclusive or exclusive (item 8).
- Clause 4.2's "per hour, per item": is a unit one hour *or* one item, or an
  hour of one item? Only the basis label is checked, not the arithmetic
  between the two.
- A `wrong_unit_basis` line "shall be returned": the corrected amount is taken
  as the contract rate times the billed quantity, which assumes the quantity is
  right and only the basis label is wrong.

## Stopping decision

Algorithmic work on Hospital 2 stopped on 2026-09-22, at the state described
in item 19.

**Why the two clusters failed.** OpenRouter answered both with HTTP 402
Payment Required: *"This request requires more credits, or fewer max_tokens.
You requested up to 131072 tokens, but can only afford 79721."* The failure
was about quota and credit, not about the model's output. The request set no
`max_tokens`, so each call asked for the model's maximum. The code now sends a
bounded, configurable `max_tokens` (`H2_CLASSIFIER_MAX_TOKENS`, default 4096,
unit-tested). **The classifier was not rerun after that change.**

**Why no more credit was bought.**

- The semantic stage had just shown what it contributes: 0 verified matches
  from 67 answered clusters.
- The 2 missing clusters cover 55 of 14,360 lines.
- Even at the best outcome — both classified MATCHED *and* accepted by Jev —
  those lines would only become priceable. Their invoices would still need
  every other line resolved before a total could be submitted.
- The expected gain did not justify further spend inside the time budget.

**Why no replacement model was used.**

- A different model for 2 of 69 clusters would mix two classifiers in one
  mapping set, which Jev and the provenance record were not designed to
  compare.
- It would also be tuning towards coverage after seeing results.
- The pending clusters remain AMBIGUOUS instead. Their lines are carried
  as sets of possible services, and their invoices get no submitted corrected
  total.

**What would come next**, in order:

1. With credit restored, run `semantic h2 classify --retry-failed` (only the
   2 failed clusters) and then `semantic h2 jev`. No code change is needed.
2. Put the 3 unaccepted MATCHED proposals to a human reviewer rather than a
   model. Jev's REJECT probabilities (0.59–0.65) are not decisive either way.
3. If more coverage is wanted, work on the 52 clusters that lack a qualifier
   or specialty. The fix is contract-side (a qualifier/specialty evidence
   table reviewed by a person), not another model pass.
4. Only after that, Hospitals 3–5. Hospital 1's contract parser and matcher
   patterns are the starting point.
