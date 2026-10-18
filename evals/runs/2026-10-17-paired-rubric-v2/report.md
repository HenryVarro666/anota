# Paired Qwen rubric experiment

![Paired quality and latency](figures/comparison.png)

Two prompts, identical Qwen3-14B Q4_K_M weights and runtime. 24 new synthetic source parents; two passes.

The reference labels are provisional and agent-authored before inference. They are not independently expert-adjudicated. This study measures adherence to an explicit label policy, not clinical certification.

## Holdout results

| Metric | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |
|---|---:|---:|---:|---:|
| Valid output | 24/24 (100.0%) | 24/24 (100.0%) | 24/24 (100.0%) | 24/24 (100.0%) |
| Error/clean agreement | 24/24 (100.0%) | 24/24 (100.0%) | 24/24 (100.0%) | 24/24 (100.0%) |
| Exact type set | 19/24 (79.2%) | 21/24 (87.5%) | 18/24 (75.0%) | 22/24 (91.7%) |
| Severity agreement | 15/24 (62.5%) | 14/24 (58.3%) | 15/24 (62.5%) | 15/24 (62.5%) |
| Critical severity recall | 9/9 (100.0%) | 8/9 (88.9%) | 9/9 (100.0%) | 8/9 (88.9%) |
| False alarms on clean controls | 0/4 (0.0%) | 0/4 (0.0%) | 0/4 (0.0%) | 0/4 (0.0%) |
| Request P50 (s) | 12.365 | 16.219 | 14.804 | 18.739 |
| Request P95 (s) | 16.954 | 26.744 | 18.794 | 25.745 |

Each column has 24 examples, not 48 independent observations. Binary detection and exact taxonomy are different endpoints.

## Predeclared gates

**HOLD: at least one pilot gate failed. Keep the baseline default.**

| Gate | Pass 1 | Pass 2 |
|---|---|---|
| validity no worse | PASS | PASS |
| binary no worse | PASS | PASS |
| critical recall no worse | FAIL | FAIL |
| clean false alarm no worse | PASS | PASS |
| p95 within 1 5x | FAIL | PASS |

These are small-pilot gates, not production acceptance. No automatic deployment or model switch occurred.

## Paired wins and losses

Each comparison is the same source item under both prompts; unavailable outputs count as incorrect.

| Pass | Endpoint | Candidate wins | Candidate losses | Ties |
|---|---|---:|---:|---:|
| 1 | binary | 0 | 0 | 24 |
| 1 | types | 3 | 1 | 20 |
| 1 | severity | 1 | 2 | 21 |
| 2 | binary | 0 | 0 | 24 |
| 2 | types | 4 | 0 | 20 |
| 2 | severity | 1 | 1 | 22 |

## Timing workload

| Arm | Mean prompt tokens | Mean completion tokens | Pooled request P50 | Pooled request P95 |
|---|---:|---:|---:|---:|
| baseline | 610.3 | 63.9 | 14.019 s | 18.779 s |
| candidate | 841.3 | 85.2 | 18.143 s | 26.578 s |

The candidate changes both instruction content and input length. This is not an equal-token-budget prompt ablation, despite equal maximum output tokens. Pooled timing describes requests, not independent accuracy samples.

Label repeatability (both exact types and severity, valid pairs): baseline 23/24; candidate 23/24.

## Severity direction (exploratory diagnostic)

These post-run diagnostics use the unchanged provisional labels; they were not additional promotion gates.

| Pass | Arm | Critical predictions on noncritical error items | Noncritical predictions on critical items |
|---|---|---:|---:|
| 1 | baseline | 6/11 | 0/9 |
| 1 | candidate | 5/11 | 1/9 |
| 2 | baseline | 6/11 | 0/9 |
| 2 | candidate | 4/11 | 1/9 |

Unavailable outputs are reported in validity and critical recall, not counted as a specific severity prediction here. Clean false alarms in the main table are a different measure from overgrading an already erroneous translation.

No significance test treats repeated outputs as independent samples. This small, purpose-built set is descriptive.

## Complete holdout predictions

![Every item and both passes](figures/case-map.png)

Source and reference rationales are in [the frozen fixture](source-snapshot/evals/data/holdout_v2.jsonl).

| Example | Reference | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |
|---|---|---|---|---|---|
| `h01` | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |
| `h02` | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |
| `h03` | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |
| `h04` | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |
| `h05` | negation_polarity / critical | negation_polarity / critical | negation_polarity / critical | negation_polarity / critical | negation_polarity / critical |
| `h06` | negation_polarity / major | mistranslation, negation_polarity / critical | negation_polarity / critical | mistranslation, negation_polarity / critical | negation_polarity / critical |
| `h07` | negation_polarity / critical | negation_polarity, mistranslation / critical | negation_polarity / critical | negation_polarity, mistranslation / critical | negation_polarity / critical |
| `h08` | negation_polarity / major | negation_polarity / critical | negation_polarity / critical | negation_polarity / critical | negation_polarity / critical |
| `h09` | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical |
| `h10` | number_unit / major | number_unit / critical | number_unit / minor | number_unit / critical | number_unit / minor |
| `h11` | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical |
| `h12` | number_unit / major | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical |
| `h13` | terminology / critical | omission, addition, terminology, number_unit / critical | omission, addition, terminology, negation_polarity / critical | omission, addition, terminology, number_unit / critical | omission, addition, terminology, negation_polarity / critical |
| `h14` | terminology / major | mistranslation, terminology / critical | terminology / critical | mistranslation, terminology / critical | terminology / critical |
| `h15` | terminology / critical | terminology / critical | terminology / critical | terminology / critical | terminology / critical |
| `h16` | terminology / major | terminology / critical | terminology / major | terminology / critical | terminology / major |
| `h17` | omission / critical | omission / critical | omission / critical | omission, number_unit / critical | omission / critical |
| `h18` | omission / major | omission / minor | omission / minor | omission / minor | omission / minor |
| `h19` | omission / critical | omission / critical | omission / major | omission / critical | omission / major |
| `h20` | omission / major | omission / minor | omission / minor | omission / minor | omission / minor |
| `h21` | mistranslation / major | mistranslation / major | mistranslation, negation_polarity / critical | mistranslation / major | mistranslation / major |
| `h22` | mistranslation / critical | mistranslation, number_unit, negation_polarity / critical | negation_polarity, mistranslation / critical | mistranslation, number_unit, negation_polarity / critical | negation_polarity, mistranslation / critical |
| `h23` | addition / major | addition / minor | addition / minor | addition / minor | addition / minor |
| `h24` | grammar / minor | grammar / minor | grammar / minor | grammar / minor | grammar / minor |

## Every candidate regression against the baseline

This includes any item that loses a correct type set, severity, or binary decision, even if another endpoint improves.

### h19 / pass 1: loses severity

- Source: Start the transfusion only after the identity check is complete.
- Hypothesis: Inicie la transfusión.
- Reference: omission / critical. Rationale: A prerequisite to a clinical action is removed.
- Baseline: omission / critical. The hypothesis omits the critical condition 'only after the identity check is complete'.
- Candidate: omission / major. The source specifies that the transfusion should start 'only after the identity check is complete,' but this condition is entirely omitted in the hypothesis.

### h21 / pass 1: loses types, severity

- Source: The appointment was postponed to a later date.
- Hypothesis: La cita se adelantó a una fecha anterior.
- Reference: mistranslation / major. Rationale: The direction of rescheduling is reversed without an explicit negation or number change.
- Baseline: mistranslation / major. The hypothesis incorrectly states that the appointment was moved forward instead of being postponed.
- Candidate: mistranslation, negation_polarity / critical. The SOURCE states that the appointment was 'postponed to a later date,' but the HYPOTHESIS claims it was 'advanced to an earlier date.' This is a clear contradiction in meaning regarding the time change.

### h19 / pass 2: loses severity

- Source: Start the transfusion only after the identity check is complete.
- Hypothesis: Inicie la transfusión.
- Reference: omission / critical. Rationale: A prerequisite to a clinical action is removed.
- Baseline: omission / critical. The hypothesis omits the critical condition 'only after the identity check is complete'.
- Candidate: omission / major. The source specifies that the transfusion should start 'only after the identity check is complete,' but this condition is entirely omitted in the hypothesis.

## Development follow-up (exploratory)

This revisits the old 20 texts after the new holdout. Original labels are unchanged. The baseline is historical, so this is not the paired holdout result.

| Metric | Original baseline pass 1 | Original baseline pass 2 | Candidate follow-up |
|---|---:|---:|---:|
| binary_accuracy | 18/20 (90.0%) | 18/20 (90.0%) | 20/20 (100.0%) |
| exact_error_types | 11/20 (55.0%) | 12/20 (60.0%) | 18/20 (90.0%) |
| severity_accuracy | 14/20 (70.0%) | 14/20 (70.0%) | 14/20 (70.0%) |
| critical_severity_recall | 9/12 (75.0%) | 9/12 (75.0%) | 10/12 (83.3%) |

The two original semantic misses:

- `t003::p2-omis1` → omission / minor. The source says 'Drink plenty of water throughout the day,' but the hypothesis only translates to 'Beba mucha agua,' omitting the time/duration component ('throughout the day').
- `t008::p2-term2` → terminology / minor. The source refers to 'medication' while the hypothesis uses 'suplemento' (supplement), which is a terminology discrepancy.

## Evidence and interpretation limits

- [Manifest](manifest.json): frozen inputs, full request order, model hash and runtime. Source snapshots retain executed bytes.
- [Raw requests/responses](attempts.jsonl), [machine-readable summary](summary.json), [protocol](../../comparison-v2.md), [development label audit](../../reviews/pilot-v1-audit.md).
- [Post-run interpretation](../../reviews/paired-v2-interpretation.md) separates semantic detection, category convention, severity and reference uncertainty. Post-run analysis sources are retained separately in `analysis-snapshot/`; they were not predeclared model inputs.
- Every arm uses the same model and quantization; this experiment cannot attribute residual errors to Q4 or model weights.
- Strict-category gains can come from enforcing a narrower annotation policy. They are not automatically semantic gains.
- Timing includes prefill and completion, defaults to server prefix caching, retains outliers, and has no thermal/power isolation.
- All clinical phrases are invented evaluation examples, not patient data or clinical instructions for use.
- Neither H100 nor AWS has been run. Candidate remains opt-in; preserve this holdout before designing another experiment.
