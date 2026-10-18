# Experiment 03 — unified severity policy

![Severity comparison](figures/severity-comparison.png)

**PASS: all predeclared pilot gates passed in both primary passes.** No automatic default change. Independent bilingual/domain review remains pending.

The baseline retains rubric v1. The candidate uses one ordered severity-v3 system rubric; weights, endpoint, schema, output limit, temperature and user suffix are identical. This tests the complete policy revision, not instruction priority or quantization in isolation.

## Primary results

24 new synthetic source parents, two counterbalanced passes: 12 critical (six clinical-condition cases), six major, two minor and four clean. Reference labels are provisional and authored by the same agent as the rule before inference.

| Metric | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |
|---|---:|---:|---:|---:|
| Valid output | 24/24 (100.0%) | 24/24 (100.0%) | 24/24 (100.0%) | 24/24 (100.0%) |
| Error / clean agreement | 23/24 (95.8%) | 24/24 (100.0%) | 23/24 (95.8%) | 24/24 (100.0%) |
| Exact category set | 15/24 (62.5%) | 13/24 (54.2%) | 15/24 (62.5%) | 11/24 (45.8%) |
| Severity agreement | 19/24 (79.2%) | 22/24 (91.7%) | 19/24 (79.2%) | 23/24 (95.8%) |
| Critical recall | 12/12 (100.0%) | 12/12 (100.0%) | 12/12 (100.0%) | 12/12 (100.0%) |
| Clinical-condition critical recall | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) |
| Critical precision | 12/14 (85.7%) | 12/13 (92.3%) | 12/14 (85.7%) | 12/13 (92.3%) |
| Critical predictions on major/minor errors | 2/8 (25.0%) | 1/8 (12.5%) | 2/8 (25.0%) | 1/8 (12.5%) |
| False alarms on clean controls | 0/4 (0.0%) | 0/4 (0.0%) | 0/4 (0.0%) | 0/4 (0.0%) |
| Request P50 (s) | 10.261 | 11.548 | 23.654 | 21.638 |
| Request P95 (s) | 18.934 | 21.820 | 51.207 | 59.786 |

Every denominator counts observed primary attempts, including failed outputs. Repeats are dependent; 96 calls are not 96 independent examples. Disputed and historical cases never enter this table.

## Predeclared gates

| Gate | Pass 1 | Pass 2 |
|---|---|---|
| validity 100 percent | PASS | PASS |
| critical recall 100 percent | PASS | PASS |
| condition recall 100 percent | PASS | PASS |
| binary no worse | PASS | PASS |
| severity no worse | PASS | PASS |
| clean false alarm no worse | PASS | PASS |
| noncritical false critical no worse | PASS | PASS |
| p95 within 1 5x | PASS | PASS |

## Paired wins and losses

Each comparison pairs the same source under the two policies. Failed outputs count as incorrect. Ties include both-correct and both-incorrect cases.

| Pass | Endpoint | Candidate wins | Candidate losses | Ties |
|---|---|---:|---:|---:|
| 1 | binary | 1 | 0 | 23 |
| 1 | types | 1 | 3 | 20 |
| 1 | severity | 3 | 0 | 21 |
| 2 | binary | 1 | 0 | 23 |
| 2 | types | 1 | 5 | 18 |
| 2 | severity | 4 | 0 | 20 |

## The single severity policy

![Ordered severity policy](figures/severity-policy.png)

[Executed rule](source-snapshot/data/guidelines/severity-v3.md). The source-stated prerequisite or stopping condition is part of the clinical action. Administrative meaning changes remain major; form-only faults remain minor. This is an operational research policy, not full MQM or clinical certification.

## Complete primary predictions

![Case map](figures/case-map.png)

| ID | Reference | Baseline 1 | Candidate 1 | Baseline 2 | Candidate 2 |
|---|---|---|---|---|---|
| s01 | omission / critical | omission / critical | omission / critical | omission / critical | omission / critical |
| s02 | omission / critical | omission / critical | omission, mistranslation / critical | omission / critical | omission, mistranslation / critical |
| s03 | omission / critical | omission / critical | omission / critical | omission / critical | omission, mistranslation / critical |
| s04 | mistranslation / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical |
| s05 | negation_polarity / critical | negation_polarity, mistranslation / critical | mistranslation, negation_polarity / critical | negation_polarity, mistranslation / critical | mistranslation, negation_polarity / critical |
| s06 | mistranslation / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical |
| s07 | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical |
| s08 | mistranslation / critical | omission, mistranslation / critical | mistranslation, terminology / critical | mistranslation, omission / critical | mistranslation, terminology / critical |
| s09 | terminology / critical | mistranslation, terminology / critical | mistranslation, number_unit / critical | mistranslation, terminology / critical | mistranslation, number_unit / critical |
| s10 | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical | number_unit / critical |
| s11 | negation_polarity / critical | negation_polarity / critical | mistranslation, negation_polarity / critical | negation_polarity / critical | mistranslation, negation_polarity / critical |
| s12 | mistranslation / critical | mistranslation, grammar / critical | mistranslation, number_unit / critical | mistranslation, grammar / critical | mistranslation, number_unit / critical |
| s13 | negation_polarity / major | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical | mistranslation, negation_polarity / critical |
| s14 | number_unit / major | number_unit / critical | number_unit / major | number_unit / critical | number_unit / major |
| s15 | omission / major | omission / minor | omission / major | omission / minor | omission / major |
| s16 | omission / major | omission / minor | omission / minor | omission / minor | omission, number_unit / major |
| s17 | terminology / major | mistranslation, terminology / major | terminology, mistranslation / major | mistranslation, terminology / major | terminology, mistranslation / major |
| s18 | addition / major | no_error / neutral | addition / major | no_error / neutral | addition / major |
| s19 | grammar / minor | grammar / minor | terminology, grammar / minor | grammar / minor | terminology, grammar / minor |
| s20 | grammar / minor | grammar / minor | grammar / minor | grammar / minor | grammar / minor |
| s21 | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |
| s22 | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |
| s23 | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |
| s24 | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral | no_error / neutral |

## Every critical undergrade or unavailable critical output

Neither arm undergraded or failed a critical primary item in this run. This does not establish zero risk on unseen data.

## Candidate regressions on any primary endpoint

Includes losing a correct binary, exact-category or severity decision, even if another endpoint improves.

### s02 / pass 1: loses types

- Source: Before starting dialysis, confirm that the access line has been checked.
- Hypothesis: Comience la diálisis.
- Reference: omission / critical. A required check before a clinical procedure is removed.
- Baseline: omission / critical. The hypothesis omits the critical instruction to confirm the access line has been checked before starting dialysis.
- Candidate: omission, mistranslation / critical. The hypothesis omits the critical instruction to confirm that the access line has been checked before starting dialysis.

### s11 / pass 1: loses types

- Source: Do not restart the anticoagulant today.
- Hypothesis: Reinicie el anticoagulante hoy.
- Reference: negation_polarity / critical. A prohibition on restarting medication becomes an instruction to restart.
- Baseline: negation_polarity / critical. The hypothesis incorrectly translates 'Do not restart' as 'Restart', flipping the negation.
- Candidate: mistranslation, negation_polarity / critical. The hypothesis incorrectly instructs to restart the anticoagulant, changing the clinical action from 'do not restart' to 'restart', which is a critical error.

### s19 / pass 1: loses types

- Source: The printed maps are on the table.
- Hypothesis: Los mapas impreso están sobre la mesa.
- Reference: grammar / minor. Adjective agreement is faulty; location, objects and proposition remain intact.
- Baseline: grammar / minor. The hypothesis contains a grammatical error with 'impreso' (masculine singular) mismatching 'mapas' (plural feminine).
- Candidate: terminology, grammar / minor. The hypothesis incorrectly uses 'impreso' (masculine singular) instead of 'impresos' (plural) to agree with 'mapas', which is a plural noun.

### s02 / pass 2: loses types

- Source: Before starting dialysis, confirm that the access line has been checked.
- Hypothesis: Comience la diálisis.
- Reference: omission / critical. A required check before a clinical procedure is removed.
- Baseline: omission / critical. The hypothesis omits the critical instruction to confirm the access line has been checked before starting dialysis.
- Candidate: omission, mistranslation / critical. The hypothesis omits the critical instruction to confirm that the access line has been checked before starting dialysis.

### s03 / pass 2: loses types

- Source: Remove the catheter only when the physician has confirmed that it is no longer needed.
- Hypothesis: Retire el catéter.
- Reference: omission / critical. The condition authorizing catheter removal is missing.
- Baseline: omission / critical. The hypothesis omits critical information about confirming with a physician and the condition of the catheter.
- Candidate: omission, mistranslation / critical. The hypothesis omits the critical condition that the physician must confirm the catheter is no longer needed before removal.

### s11 / pass 2: loses types

- Source: Do not restart the anticoagulant today.
- Hypothesis: Reinicie el anticoagulante hoy.
- Reference: negation_polarity / critical. A prohibition on restarting medication becomes an instruction to restart.
- Baseline: negation_polarity / critical. The hypothesis incorrectly translates 'Do not restart' as 'Restart', flipping the negation.
- Candidate: mistranslation, negation_polarity / critical. The hypothesis incorrectly instructs to restart the anticoagulant, changing the clinical action from 'do not restart' to 'restart', which is a critical error.

### s16 / pass 2: loses types

- Source: The registration desk closes at three in the afternoon.
- Hypothesis: El mostrador de registro cierra por la tarde.
- Reference: omission / major. The specific administrative closing time is lost.
- Baseline: omission / minor. The hypothesis omits the specific time 'three in the afternoon' and only mentions 'por la tarde' (in the afternoon).
- Candidate: omission, number_unit / major. The hypothesis omits the specific time 'three' and only mentions 'por la tarde', changing the quantity/time from precise to vague.

### s19 / pass 2: loses types

- Source: The printed maps are on the table.
- Hypothesis: Los mapas impreso están sobre la mesa.
- Reference: grammar / minor. Adjective agreement is faulty; location, objects and proposition remain intact.
- Baseline: grammar / minor. The hypothesis contains a grammatical error in the adjective placement and agreement.
- Candidate: terminology, grammar / minor. The hypothesis incorrectly uses 'impreso' (masculine singular) instead of 'impresos' (plural) to agree with 'mapas', which is a plural noun.

## Disputed references — descriptive, never scored

These four cases were separated before model calls and have no single gold field. Plausible severities below are review alternatives, not model-validated answers. Their raw calls and rationales are retained.

### d01

- Source: Take the tablet with a snack.
- Hypothesis: Tome la pastilla.
- Review alternatives: major / critical. Is a food qualifier part of the clinical administration schedule under this policy? Drug-specific context is unstated; reviewer agreement is needed.
- Baseline: omission / minor. The hypothesis omits the instruction to take the tablet with a snack.
- Candidate: omission / critical. The hypothesis omits the instruction to take the tablet with a snack, changing the clinical action's condition.

### d02

- Source: Let us know if you have any new concerns.
- Hypothesis: Avísenos si tiene alguna inquietud.
- Review alternatives: neutral / minor / major. The loss of 'new' may or may not restrict relevant concerns in the intended conversational context.
- Baseline: no_error / neutral. The hypothesis accurately translates the source message without errors.
- Candidate: no_error / neutral. The hypothesis accurately translates the source without changing meaning or introducing errors.

### d03

- Source: The specialist will discuss the result with you soon.
- Hypothesis: El médico hablará pronto con usted sobre el resultado.
- Review alternatives: neutral / major. Specialist versus physician depends on the unstated professional role and intended specificity.
- Baseline: no_error / neutral. The hypothesis accurately translates the source without errors.
- Candidate: no_error / neutral. The hypothesis accurately translates the source without changing meaning or introducing errors.

### d04

- Source: Return next week for the follow-up.
- Hypothesis: Regrese dentro de dos semanas para el seguimiento.
- Review alternatives: major / critical. A follow-up interval changes, but the text does not establish whether a time-sensitive clinical action is affected.
- Baseline: mistranslation / major. The hypothesis incorrectly translates 'next week' as 'within two weeks', altering the time frame.
- Candidate: number_unit / major. The hypothesis changes 'next week' to 'within two weeks', altering the time condition.

## Known regressions — candidate follow-up only

These cases helped design the rule; they are not held-out evidence. Original labels remain unchanged, including disputed ones. The old candidate output is historical rather than a contemporaneous arm.

| ID | Historical reference | Previous candidate | Severity-v3 candidate |
|---|---|---|---|
| h19 | omission / critical | omission / major | omission / critical |
| t012 | omission / major | omission / minor | omission / critical |
| t003::p2-omis1 | omission / major | omission / minor | omission / minor |
| t008::p2-term2 | terminology / critical | terminology / minor | terminology / minor |

- `h19`: The hypothesis omits the critical condition of completing the identity check before starting the transfusion.
- `t012`: The hypothesis omits the condition 'unless it is almost time for your next dose', which changes the clinical instruction's stopping condition.
- `t003::p2-omis1`: The hypothesis omits the instruction to drink water 'throughout the day', which is a key temporal condition in the source.
- `t008::p2-term2`: The source refers to 'medication' while the hypothesis uses 'supplement', changing the entity type.

## Latency and output workload

| Arm | Mean input tokens | Mean output tokens | Request P50 | Request P95 | Label repeatability |
|---|---:|---:|---:|---:|---:|
| baseline | 610.8 | 64.4 | 15.741 s | 48.900 s | 24/24 |
| candidate | 920.8 | 69.8 | 16.436 s | 56.703 s | 22/24 |

Latency is client wall time including prefill, generation and validation. All primary outliers stay included. Prefix caching follows server defaults; host load, power and thermals are uncontrolled. Repeatability covers exact type set and severity on valid repeated pairs, not independent samples.

## Reproducibility and limits

- [Manifest](manifest.json), [raw attempts](attempts.jsonl), [summary](summary.json), [validation](validation.json), [predeclared protocol](source-snapshot/evals/severity-v3-protocol.md).
- [Frozen dispute register](source-snapshot/evals/reviews/disputed-labels-v3.md), [primary fixture](source-snapshot/evals/data/severity_holdout_v3.jsonl), [disputed fixture](source-snapshot/evals/data/severity_disputed_v3.jsonl).
- Both previous rounds and their labels are hash-checked and retained. New metrics must not be compared numerically across different test sets as if they were matched observations.
- The same agent authored references and rule; the apparent gain may partly measure adherence to that policy. No independent reference adjudication or natural-call validation is claimed.
- Label agreement does not score rationale correctness. Explanations and confidence values require separate review; a correct label can accompany an incorrect grammatical or domain claim.
- No model/precision comparison, H100 run, AWS deployment or production capacity test occurred. Existing v1 annotation batches keep their original guideline and the workbench default stays unchanged.
- Source snapshots are pre-inference evidence. `analysis-snapshot/` contains post-run plotting and verification sources; it is not predeclared inference code.

## Host-load observation after latency drift

A later part of the run produced markedly longer requests. The [single CPU/RAM observation](validation/resource-observation.json) was collected after noticing that drift; it is not a time series, an isolated-load condition, or proof of the cause. No workload was stopped, no completed request was replayed, and no latency outlier was removed. Per-pass latency is reported above. Treat the latency gate as descriptive of this desktop session; a dedicated idle-host run is still needed for performance or capacity conclusions.

The user [confirmed concurrent computation](validation/user-load-context.json) while the study was running. The workload type and its start/end were not recorded. Do not assume the first pass was idle, attribute individual slow requests to a specific application, or interpret the latency gate as an isolated prompt-speed comparison. This context was recorded after observing drift; the frozen protocol and every observed request remain unchanged.

[Post-run interpretation and remaining adjudication questions](interpretation.md).

## Shared workspace and recovery validation

[Workspace drift record](validation/workspace-drift.json) distinguishes the frozen inference snapshot from concurrent speech-module edits. The live `app/models.py` acquired speech-review fields after the evaluation process had imported its modules; the judge constants and annotation schema are AST-equivalent. Actual request bodies are checked against the frozen contract. The existing edits are preserved. Recovery checks use a reconstructed frozen checkout, never overwrite the live workspace, and replay no inference calls.

The selected source snapshot is not a complete standalone checkout. The first isolated reconstruction [failed on an absent import](validation/recovery-initial-failure.json); supplying the unchanged Git `app/lf.py` dependency allowed all [recovery probes](validation/recovery-check.json) to pass. Its source and commit are recorded separately, not retroactively added to the frozen manifest. The LF mapping is used by the mock judge, not the real-model evaluation path.

The frozen checkpoint passed 173 Windows tests. The later [shared-workspace check](validation/current-workspace-tests.json) ran 230 tests with 0 failures and 0 errors; application files were unchanged during that test command. These CPU checks do not validate H100/AWS serving. Its start/end are also recorded as an additional local workload during evaluation.
