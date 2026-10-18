# Experiment 03: one ordered severity rubric

Frozen before the first model call. Compare original baseline against a candidate
whose **only request difference is the system rubric text**. Both arms use the same
Qwen3-14B Q4_K_M weights, endpoint, JSON schema, temperature zero, 256-token output
limit, 4096 context, concurrency one and short-rationale `/no_think` suffix. Input
length changes with the rubric; this is not an equal-input-token ablation.

## Policy and separation of references

The candidate loads [severity-v3](../data/guidelines/severity-v3.md) as the only
rubric in the system message. That same file is the reference-review policy. Its
ordered decision is: explicit changed clinical action or its condition → critical;
other changed meaning → major; real form error with meaning intact → minor; correct
translation → neutral. A clinical prerequisite omission cannot become minor merely
because the main action is still translated. Category names are not severity triggers.

The previous user-message clarification is not appended. The old baseline still
loads `data/guideline.md` without modification, as a historical policy comparator.
Any gain combines clearer policy content and its placement in the authoritative
rubric; it cannot independently attribute a result to instruction priority alone.

The [dispute register](reviews/disputed-labels-v3.md) lists historical reference
problems and four newly authored ambiguous cases. They have no single gold value and
never enter primary accuracy, severity, latency percentiles or promotion gates.
Historical references are not changed to make the candidate look better.

## Data, calls and independence

- 24 new primary EN→ES source parents: 12 critical (including six clinical-condition
  cases), six administrative major, two form-only minor, four clean controls.
- Four new disputed cases: no gold, both arms once, descriptive outputs only.
- Four known historical regressions: candidate once after the new cases; original
  labels retained, descriptive follow-up only, not a generalization claim.
- Two warmups, then two counterbalanced primary passes: deterministic shuffled order,
  alternating first arm per item and reversing it in pass two. Then disputed and
  historical follow-up. **110 total calls: 2 + 96 + 8 + 4.**

All new sources and parent IDs are distinct from both previous datasets. They share
targeted semantic mechanisms by design; they are synthetic texts, not real independent
conversations. The same agent writes the rubric, examples and provisional references
before inference. Independent bilingual/domain adjudication remains outstanding.

## Gates declared for each primary pass

1. Candidate valid output, critical-severity recall and clinical-condition critical
   recall must each be **100% on this pilot**. Missing/failed outputs are not successes.
2. Binary error/clean agreement and severity agreement must not decline versus baseline.
3. False alarms on clean texts and critical predictions on major/minor error texts
   must not increase. This prevents “everything is critical” from passing.
4. Candidate request P95 ≤ 1.5 × baseline P95.

All gates must pass in both repeats. Report counts, critical precision, category
agreement, all regressions, output validity, latency distribution and token workload
regardless of the decision. No post-holdout prompt changes, label changes, exclusions,
or best-pass selection. Passing is a pilot result, not authorization to replace the
default, clinical validation or an AWS production claim.

## Evidence and recovery

The runner freezes source snapshots, datasets, rubric, profiles, gates, prior-run
hashes and full request order before inference. Every attempt is flushed to disk.
There are zero automatic retries; three consecutive failures stop the run. A writer
lock prevents concurrent writers. Resume requires the same identity and an exact
saved prefix of the frozen request order; it never replays completed attempts.

```powershell
python scripts/run_severity_study.py --study evals/studies/severity-v3.json --metadata evals/provenance/890m-qwen-v3.json --out evals/runs/YOUR-NEW-V3-RUN
python scripts/render_severity_study.py --run evals/runs/YOUR-NEW-V3-RUN
python scripts/verify_severity_study.py --run evals/runs/YOUR-NEW-V3-RUN
```

Model provenance must describe the actual environment. A fresh model-file hash and
LM Studio state are recorded before this Windows run. Timing includes prefill and
generation, uses default prefix caching, and has no power/thermal/background-host
isolation. It is an interactive desktop benchmark. No generation workload is
deliberately scheduled in parallel by this experiment.

The model service stays outside Anota. To test H100 or AWS, create a new versioned
study using the existing endpoint/environment-variable profile convention and record
the new served weights/runtime. Both arms must resolve to identical profiles. Keep
quality, concurrent load and failover exercises separate; see the
[runtime matrix](../docs/runtime-matrix.md). Neither H100 nor AWS has been run here.

The candidate is evaluation-only for now. The workbench default and existing v1
annotation batches retain their original rubric/version. A future rollout needs an
explicit batch-level rule migration and independent human reference review.
