# What the unified severity policy changed

The severity policy improves agreement on this constructed set, but it does not
justify replacing the default judge. Exact category agreement declines, one
administrative case remains overgraded, and independent reference review is pending.
The two earlier experiments and all their original labels remain unchanged.

## Primary evidence

There are **24 new primary source texts**, evaluated twice under each policy.
The repeats are dependent measurements of those same 24 texts. They are not 96
independent test cases, and their references were written by the same agent that
authored the policy, before inference.

| Endpoint | Baseline, passes 1 / 2 | Candidate, passes 1 / 2 |
|---|---:|---:|
| Severity agreement | 19/24 / 19/24 | 22/24 / 23/24 |
| Critical recall | 12/12 / 12/12 | 12/12 / 12/12 |
| Clinical-condition critical recall | 6/6 / 6/6 | 6/6 / 6/6 |
| Error / clean agreement | 23/24 / 23/24 | 24/24 / 24/24 |
| Exact category set | 15/24 / 15/24 | 13/24 / 11/24 |
| Critical predictions on major/minor errors | 2/8 / 2/8 | 1/8 / 1/8 |
| False alarms on clean controls | 0/4 / 0/4 | 0/4 / 0/4 |

Severity agreement rises from **79.2% to 93.8%** as a two-pass mean. Category
agreement falls from **62.5% to 50.0%**. Paired severity wins/losses are 3/0 in the
first pass and 4/0 in the second; category wins/losses are 1/3 and 1/5. Do not
combine these endpoints into a single success claim.

Both policies already achieve 12/12 critical recall on each new pass. This is a
ceiling result on a small, targeted set; it does **not** demonstrate better critical
recall over baseline on unseen traffic. Six condition cases are still only six
unique examples. The separate historical checks below are development evidence.

## Specific changes and residual errors

- **s14, administrative counter number:** baseline grades the changed counter
  critical in both passes; the candidate correctly applies the provisional major
  policy in both. This supports separating the type `number_unit` from severity.
- **s15, missing required envelope:** baseline detects the omission but calls it
  minor; the candidate calls it major twice, matching the ordered policy.
- **s18, invented Friday commitment:** baseline misses the added date twice and
  reports `confidence=1.0`; the candidate detects addition/major twice. Confidence
  is not calibrated evidence of correctness.
- **s13, public orientation ticket requirement:** both policies call the negation
  reversal critical twice, although the text states no clinical action. It remains
  a major-reference disagreement in primary scoring. The candidate has not reliably
  learned that administrative negation is not automatically critical.
- **s16, precise closing time omitted:** the candidate changes from omission/minor
  in pass 1 to omission+number_unit/major in pass 2. Severity improves on the second
  pass while the category set regresses. Temperature zero did not make this run
  fully repeatable: joint label agreement is 22/24 versus baseline 24/24.

The category failures are not just severity failures under a different name. The
candidate repeatedly adds generic `mistranslation` to a more specific tag, despite
the minimum-set rule. The route substitution in **s09** receives `number_unit` and
misses the provisional `terminology` reference. The form-only **s19** adds
`terminology` to `grammar`. These are reasons to retain category evaluation as a
separate endpoint and a future promotion gate.

The first-pass baseline rationale for s19 also incorrectly describes *mapas* as feminine
while reaching the expected grammar label. Label agreement therefore does not
establish explanation accuracy. No rationale-quality score was predeclared here.

## Policy, model and reference labels

The served model artifact and all request settings are held fixed; only the system
rubric text differs. Changes on paired inputs support an effect of the complete
policy revision on this run. They do not isolate instruction priority, the Qwen
weights, Q4 quantization, or a specific hardware mechanism. The unchanged wrapper
still describes a medical-translation judge; whether that framing contributes to
administrative overgrading is an **untested hypothesis**, not an established cause.

The old reference disagreements and four new ambiguous texts were registered
[before inference](source-snapshot/evals/reviews/disputed-labels-v3.md). The four
new cases have no single gold value and never enter primary quality, latency or
gates. Their model outputs do not resolve the missing context. Keep original
labels and record any later human adjudication as a new, versioned dataset.

One additional **post-run category question** is s08: does left/right anatomical
site substitution belong to `mistranslation`, `terminology`, or a more explicit
laterality convention? Its original critical severity and category reference stay
in all reported primary scores. This observation is not a retrospective exclusion.

The prepared 28-item reviewer packet hides reference labels, model suggestions and
the primary/disputed split. It is a handoff artifact, not completed independent
review. Reviewers who have read these results cannot provide a blinded first pass.

## Historical regressions and disputed cases

These four inputs helped design the policy. Each receives one candidate follow-up;
the previous outputs are historical, not a simultaneous comparator. Original gold
values are retained even when they conflict with the new operational policy.

| ID | Original severity | Previous candidate | Severity-v3 candidate |
|---|---|---|---|
| h19, missing identity-check prerequisite | critical | major, both passes | critical |
| t012, missing next-dose exception | major | minor | critical |
| t003::p2-omis1, missing “throughout the day” | major | minor | minor |
| t008::p2-term2, medication → supplement | critical | minor | minor |

**h19 is repaired in this single follow-up:** the candidate explicitly identifies
the missing prerequisite. **t012 now follows the v3 condition rule**, but still
disagrees with its unchanged historical major reference. Do not quietly relabel it
to manufacture a higher historical score. The two earlier detection misses remain
detected but graded minor; they are not fully resolved. In particular, describing
a changed entity while assigning the form-only minor tier exposes a remaining
policy-following problem, separate from whether the old critical reference is valid.

For the predeclared disputed set, d01 receives baseline minor / candidate critical;
d02 and d03 receive neutral from both; d04 receives major from both, with different
categories. Agreement on d02/d03/d04 is not adjudication. All eight raw outputs and
their rationales are published in the full report, without an accuracy denominator.

## Timing under concurrent load

The user [confirmed other computation was running](validation/user-load-context.json).
Its type, intensity and start/end were not measured. A separately recorded CPU
test run also took place late in the primary evaluation. Do not assume the first
pass was idle or attribute individual outliers to one application.

| Request wall time | Baseline P50 / P95 | Candidate P50 / P95 |
|---|---:|---:|
| Pass 1 | 10.26 / 18.93 s | 11.55 / 21.82 s |
| Pass 2 | 23.65 / 51.21 s | 21.64 / 59.79 s |
| All primary calls | 15.74 / 48.90 s | 16.44 / 56.70 s |

All outliers remain. The numerical P95 ratio gate passes in each pass, but a passing
gate under uncontrolled load is not a causal speed comparison or an AWS capacity
estimate. There is no measured decode-only throughput or TTFT decomposition. The
single CPU/RAM observation is not a time series. Preserve this run, then use a new
directory for any dedicated, controlled-load benchmark.

## Next bounded experiment

1. Obtain independent bilingual/domain review of the policy and references,
   including clinical conditions, administrative negation, and the taxonomy
   boundary cases. Keep original reviewer submissions and separate adjudication.
2. Freeze a fresh test set before trying a category-focused change. Include
   clinically equivalent paraphrases and ordinary administrative controls so that
   higher critical recall cannot be achieved by overgrading everything. Add exact
   categories and rationale correctness to the acceptance criteria for that study.
3. Re-run the fixed workload under documented load with utilization and memory
   telemetry. Treat the published cases as regressions after any further tuning.
4. Exercise the same endpoint contract on H100 and then the target AWS service.
   Record model precision/runtime separately; test recovery and redundancy in
   separate runs. Neither environment was executed by this desktop experiment.

The workbench default remains the original rubric. Promotion also requires an
explicit batch-level guideline version migration, so existing annotations retain
their original meaning.
