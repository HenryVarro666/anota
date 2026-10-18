# Reference disputes registered before severity-v3 inference

This register is an agent's audit, not expert adjudication. Historical gold is never
overwritten. A model's agreement or confidence cannot resolve a reference dispute.
The new primary set has provisional operational labels; it is not an expert golden set.

## Historical cases requiring adjudication

| Dataset / ID | Original severity | Proposed review | Reason |
|---|---|---|---|
| v1 / t003::p2-omis1 | major | minor / major | Loss of a general time span; intended force of the advice is not independently established. |
| v1 / t003::p2-nega1 | critical | major / critical | Water advice reversed, but the source does not establish a treatment-specific restriction. |
| v1 / t001::p2-omis0 | major | major / critical | Food qualifier for medication: the new rule must clarify the boundary between schedule and general advice. |
| v1 / t004::p2-omis2 | major | major / critical; extra type | Threshold deleted and remaining text malformed; both consequence and category need review. |
| v1 / t004::p2-term1 | critical | confirm severity and extra type | A swapped symptom leaves an unnatural temperature phrase; synthetic single-tag gold may be incomplete. |
| v1 / t012 | major | critical under v3 | Omitted exception governs whether another dose is taken; retain old gold while reviewing this policy discrepancy. |
| v1 / t008::p2-term2 | critical | major | Medication→supplement changes the description; an automatic critical label assumes an unstated clinical action. |
| v1 / t006::p2-numb1 | critical | major | Malformed appointment time is administrative; no explicit clinical action is stated. |
| v2 / h10, h12 | major | confirm major under v3 | Administrative room/identifier changes; model predictions do not justify relabeling them minor or critical. |
| v2 / h14, h16 | major | confirm major; document role context | Document/professional-role substitutions may have contextual implications not in the short texts. |
| v2 / h18, h20, h23 | major | confirm intended-use impact | Required document, availability interval and invented date: meaning changes, but independent reviewers should ratify the administrative boundary. |

`h19` remains a **known critical regression**, not an ambiguous case removed from
scoring: the text explicitly conditions a clinical procedure on a prerequisite.
It is included in the descriptive historical regression phase. Category-only
duplication disputes are documented in the earlier audits and are not severity fixes.

## New disputed set, separated before any prediction

The four records in [severity_disputed_v3.jsonl](../data/severity_disputed_v3.jsonl)
have **no single gold field**. They are called once under each arm after the primary
two-pass comparison; every output is published, with no accuracy or promotion score.

| ID | Ambiguity | Plausible severities to adjudicate |
|---|---|---|
| d01 | Food qualifier omitted from tablet instruction; schedule boundary unclear | major / critical |
| d02 | “new” lost from a broad invitation to raise concerns | neutral / minor / major |
| d03 | Specialist rendered as physician; actual professional role unstated | neutral / major |
| d04 | Follow-up moved by one week; time-sensitive clinical purpose unstated | major / critical |

These exclusions are based on ambiguity before inference, not on observed errors.
All 24 primary examples remain in scoring, including failures and disagreements.

## Reviewer procedure

Give independent EN/ES reviewers only source, hypothesis and the
[severity-v3 rule](../../data/guidelines/severity-v3.md), with model outputs and
provisional references hidden. Record first-pass labels independently, then record
the adjudicator's decision, rationale, rule version and timestamp. Keep each original
label and a separate resolution record. A new adjudicated dataset must have a new
version and fresh metrics; historical run scores must remain reproducible.

The workbench's existing v1 batches still use the v1 rubric. This research study
loads the new file explicitly; it does not silently relabel old batches or change
their annotation-guideline version. No independent human adjudication has occurred.
