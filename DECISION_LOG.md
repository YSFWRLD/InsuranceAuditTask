# Decision log — Hospital 1

Assumptions made, ambiguities found, and what was done about each. The four
marked **[POLICY]** are encoded in `AuditPolicy` in `src/hospital_1/audit.py`, so the
code and this document cannot drift apart.

---

## Contract readings

### 1. The exclusion window is per patient **[POLICY]**

Section 10 says a service is "not billable within N days of" another. It never
says "to the same Patient". Read per patient: an exclusion window between two
clinical services is a statement about one course of treatment, and a
cross-patient reading would make whether a biopsy is payable depend on an
unrelated person's care.

The rejected reading is not implemented. Setting the flag to `False` raises
`NotImplementedError` rather than silently doing something else.

### 2. "Within N days" includes day N **[POLICY]**

Also Section 10. "Not billable within 7 days" could exclude a service 7 days
later, or only 1–6 days later. Read inclusively. The two readings differ only
on the boundary day; both are tested (`tests/hospital_1/test_audit.py` asserts day 7 is
inside and day 8 is outside), so switching the reading is a one-line change
with an explicit test.

### 3. A daily-cap breach is detected but not reconstructable **[POLICY]**

Section 8 caps "maximum billable units per Patient per Service Day". Two
readings: the excess is simply not payable (so the corrected total prices the
cap), or the quantity on the invoice is untrustworthy and the correction is
unknown.

Taken the second. 11 units against a cap of 8 proves the number is wrong; it
does not reveal what was delivered. The development labels settle it: the three
cap-breach invoices have corrected quantities of 3, 3 and 9 against caps of 8,
12 and 12 and billed quantities of 11, 14 and 15. No function of (billed, cap)
produces that. So `expected_total_cents` is left empty and the ceiling is
reported separately in `maximum_contractually_payable_total_cents`.

This costs exact-match score on 4 of 913 invoices. It is the right trade: a
confident wrong number propagates silently, an admitted gap costs a reviewer a
few minutes.

### 4. An unknown service keeps its billed amount **[POLICY]**

A description matching nothing in Section 4 cannot be priced. Two readings: the
service is not payable under this agreement (zero), or we simply cannot price
it (carry the billed figure and say so).

Taken the second. The contract is silent about services it does not schedule;
silence is not a price of zero. The line's amount is carried through,
`correction_reconstructable` is set to 0, and the invoice drops to the low
confidence band. Confirmed against development labels, where the corrected
total for such invoices keeps the unknown line intact.

### 5. Sections 5 and 6 are both "stage (d)" and never overlap

Clause 3.2 puts "any premium or uplift" at stage (d) without saying how a
threshold premium and a non-business-day uplift would combine if a service
carried both. In this contract the two lists are disjoint, so the question does
not arise. The parser emits a warning if a future contract makes them overlap,
rather than letting whichever branch the code reaches first decide.

### 6. A date outside the term is one defect, not two

Clause 11.3 states both requirements in one sentence: every service date must
fall within the term **and** may not fall after the invoice date. A date pushed
past the end of the term is usually also after the invoice date. Reported as
`service_date_out_of_window` only — the term breach is the more specific
statement of the same defect.

### 7. Facility and plan-tier multipliers are 1.0 — but the stages stay

Clauses 1.2 and 1.3 remove both differentials. The stages are still implemented
and still round, because they are the stages most likely to differ under
another hospital's agreement and because the trace should say what the contract
says is happening. The parser refuses to *assume* 1.0: if it cannot recognise
the clause, it raises rather than defaulting.

### 8. Section 4's cap column and Section 8 must agree

The two tables restate one rule. The parser checks them against each other and
raises on any disagreement rather than picking a winner. This caught nothing in
Hospital 1 — which is the point; it would have caught a misread table.

## Data readings

### 9. The JSONL is canonical; the CSVs are a cross-check

Five invoice identifiers are used by two physically distinct invoice records,
with different patients, dates, totals and line items. Only the JSONL expresses
that: `hospital_1_line_items.csv` keys lines to an `invoice_id`, which for a
reused id is ambiguous.

The line-to-invoice association is therefore taken from the file structure and
**never** from the digits inside a line identifier — the second occurrence of
`INV-H1-000068` carries lines `H1-L00155-*`, so any positional assumption would
have been wrong. `cross_check_against_csv` compares all three files field by
field and reports zero discrepancies, which is what licenses using JSONL alone.

### 10. A reused identifier is reported once, subject = the later record

Predictions are per invoice id, so the five reused ids get one row each. The
reusing record is the subject: it is the invoice whose submission breaks clause
11.2, and its money is the money in question. Findings on the superseded record
are still reported — they are findings about that identifier — but its total is
not what gets reconstructed. Confirmed against development labels, where the
corrected total for all five matches the later record.

### 11. Malformed dates are a finding, not a missing field

`2025-06-31`, `2025-02-30`, `2024-00-17`, `31/02/2024` and `not-a-date` all
parse to `None`. The first three are the dangerous ones: they look like dates.
A `None` service date removes the line from the cumulative sequence, which is
recorded as an uncertainty rather than quietly treated as a zero contribution.

## Matcher readings

### 12. Ambiguity is preserved, not resolved

Four descriptions (120 lines) are consistent with more than one contracted
service — `Procedure Immun Endosc` fits both *Ambulatory* and *Preoperative
Immunologic Endoscopic Procedure*. The information needed to choose is not in
the string.

Rather than guess, the engine asks a weaker question the evidence can answer:
is the billed rate correct under *any* plausible reading? If yes, no finding
and the amount stands. If no, the line is wrong under every reading, which is a
sound finding even though we still cannot say what it should be.

The same interval logic runs through the cumulative index: where ambiguous
lines make a running total uncertain, the engine prices the *set* of plausible
rates. This removed 19 false positives during development.

### 13. The billed unit basis may break a tie, but only once

The basis is consulted only when the text leaves a genuine tie and exactly one
tied candidate supports it (218 lines, 4 descriptions). Where it fires,
`used_unit_basis_for_matching` is recorded and `wrong_unit_basis` is suppressed
for that line: the basis cannot both identify the service and be evidence
against it.

### 14. The billed price never identifies a service

`ServiceMatcher.match` takes a description and an optional unit basis. There is
no price parameter and a test asserts the signature stays that way. Matching on
price would make the audit circular — the price is the thing under audit.

### 15. A contract word the candidate cannot explain counts against it

`CONTRADICTION_DECAY = 0.75` per unexplained contract-vocabulary token.
"Advanced **Orthopaedic** Recovery Room" is not "Advanced **Cardiac** Recovery
Room Occupancy" with a typo; it is a service this contract does not carry.
Without this, an invented service gets billed at a real service's rate and the
engine calls it fine.

The mirror case — a description *missing* its discriminating word — is **not**
handled, and it is the one defect the holdout found. See §7 of the
generalization report.

## Ambiguities left unresolved

- **Clause 5.1 vs clause 11.4.** 5.1 says a threshold premium is assessed on
  the aggregate daily quantity "not against the quantity on any one line item",
  which implies a patient can have two lines of one service on one day. 11.4
  forbids exactly that. Both are implemented as written; in Hospital 1 the
  situation never arises (zero same-invoice duplicates), so nothing turns on it.
- **Whether `daily_cap_exceeded` should suppress the unit-price check on the
  same line.** Currently both can fire. No development or holdout invoice
  distinguishes the readings.
- **What `ambiguity_sensitive` in the label schema is for.** It is 0 for every
  labelled row, so it carries no signal here. The engine produces its own
  ambiguity flags rather than trying to reproduce it.
