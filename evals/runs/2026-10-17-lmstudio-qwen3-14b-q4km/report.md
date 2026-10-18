# Local Qwen pilot: observed results

Generated from `attempts.jsonl`; no model-generated scores are substituted for reference labels.

20 unique synthetic EN→ES examples, derived from 11 parent templates. Two passes measure repeatability, not 40 independent examples.

| Metric | Pass 1 | Pass 2 |
|---|---:|---:|
| Valid output | 20/20 (100%) | 20/20 (100%) |
| Exact error-type set | 11/20 (55%) | 12/20 (60%) |
| Error vs clean agreement | 18/20 (90%) | 18/20 (90%) |
| Severity agreement | 14/20 (70%) | 14/20 (70%) |
| Critical severity recall | 9/12 (75%) | 9/12 (75%) |
| False alarms on clean controls | 0/4 (0%) | 0/4 (0%) |
| Request latency P50 (s) | 10.032 | 10.245 |
| Request latency P95 (s) | 13.803 | 13.443 |
| Effective output tokens/s | 6.102 | 6.039 |

Effective output tokens/s = reported completion tokens / request wall time, including prefill. This is not decoder throughput or streaming TTFT.

Label stability: **19/20** valid pairs match on both error types and severity.

Pooled P50: **10.239 s**; P95: **13.803 s**.

## Every disagreement with the frozen references

These are disagreements, not proof the model is always wrong: the reference labels are synthetic and require expert review. No label was changed after this run.

| Pass | Example | Reference types / severity | Observed types / severity | Model rationale |
|---|---|---|---|---|
| 1 | `t003::p2-omis1` | omission / major | no_error / neutral | The hypothesis accurately translates the source text without any errors. |
| 1 | `t003::p2-nega1` | negation_polarity / critical | negation_polarity, grammar / minor | The hypothesis contains a grammatical error and an incorrect negation. |
| 1 | `t001::p2-term0` | terminology / critical | mistranslation, terminology / critical | The hypothesis incorrectly translates 'tablet' as 'cucharada' (spoonful), which is a mistranslation of the medication form. |
| 1 | `t004::p2-omis2` | omission / major | omission, mistranslation / major | The hypothesis omits the temperature threshold ('above 38 degrees') and changes 'fever' to 'fiebre de más', which is imprecise. |
| 1 | `t004::p2-numb0` | number_unit / critical | number_unit, mistranslation / critical | The hypothesis incorrectly translates 'above 38 degrees' as 'more than 380 degrees', introducing a critical error in the numerical value. |
| 1 | `t002` | negation_polarity / critical | negation_polarity, mistranslation / critical | The hypothesis incorrectly translates 'Do not stop' as 'Deje de tomar', which flips the negation and changes the meaning. |
| 1 | `t001::p2-omis0` | omission / major | omission / minor | The hypothesis omits 'with breakfast' from the source. |
| 1 | `t004::p2-term1` | terminology / critical | mistranslation, terminology / critical | The hypothesis incorrectly translates 'fever' as 'cough' and retains the temperature threshold but changes the symptom. |
| 1 | `t012` | omission / major | omission / minor | The hypothesis omits the condition 'unless it is almost time for your next dose' from the source text. |
| 1 | `t008::p2-term2` | terminology / critical | no_error / neutral | The hypothesis correctly translates the source text with appropriate terminology and no errors. |
| 1 | `t006::p2-numb1` | number_unit / critical | number_unit / minor | The hypothesis contains an error in the time format (100 instead of 10). |
| 1 | `t001::p2-nega0` | negation_polarity / critical | mistranslation, negation_polarity / critical | The hypothesis contains a mistranslation of 'take' as 'no tome', which incorrectly adds a negation. |
| 2 | `t003::p2-nega1` | negation_polarity / critical | negation_polarity, grammar / minor | The hypothesis contains a grammatical error and an incorrect negation. |
| 2 | `t003::p2-omis1` | omission / major | no_error / neutral | The hypothesis accurately translates the source text without any errors. |
| 2 | `t004::p2-omis2` | omission / major | omission, mistranslation / major | The hypothesis omits the temperature threshold ('above 38 degrees') and changes 'fever' to 'fiebre de más', which is imprecise. |
| 2 | `t008::p2-term2` | terminology / critical | no_error / neutral | The hypothesis correctly translates the source text with appropriate terminology and no errors. |
| 2 | `t001::p2-term0` | terminology / critical | mistranslation, terminology / critical | The hypothesis incorrectly translates 'tablet' as 'cucharada' (spoonful), which is a mistranslation of the medication form. |
| 2 | `t002` | negation_polarity / critical | negation_polarity, mistranslation / critical | The hypothesis incorrectly translates 'Do not stop' as 'Deje de tomar', which flips the negation and changes the meaning. |
| 2 | `t004::p2-term1` | terminology / critical | mistranslation, terminology / critical | The hypothesis incorrectly translates 'fever' as 'cough' and retains the temperature threshold but changes the symptom. |
| 2 | `t001::p2-omis0` | omission / major | omission / minor | The hypothesis omits 'with breakfast' from the source. |
| 2 | `t001::p2-nega0` | negation_polarity / critical | mistranslation, negation_polarity / critical | The hypothesis contains a mistranslation of 'take' as 'no tome', which incorrectly adds a negation. |
| 2 | `t012` | omission / major | omission / minor | The hypothesis omits the condition 'unless it is almost time for your next dose' from the source. |
| 2 | `t006::p2-numb1` | number_unit / critical | number_unit / minor | The hypothesis contains an error in the time format (100 instead of 10). |

## Reproducibility and limits

- [Manifest](manifest.json): actual model hash, upstream revision, runtime, request contract, dataset and code hashes.
- [Raw attempts](attempts.jsonl): warmup and all timed requests, returned usage, raw response and elapsed time. Credentials are excluded.
- [Metrics](summary.json): denominators, per-category recall and latency. Unavailable attempts count as failures in quality denominators.
- One request at a time, temperature 0, 256 output tokens, JSON schema, `/no_think`, 4096 loaded context. No automatic retries.
- Prefix caching uses the server default; repeated prompt prefixes may accelerate these warm requests. No cold-start, thermal, power or concurrent capacity benchmark.
- EN→ES text only. No ASR, TTS, diarization, audio, low-resource languages, live PHI or expert clinical validation is included.
- H100 BF16 and AWS profiles are unexecuted templates; hardware and precision comparisons need new runs.
