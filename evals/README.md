# Judge evaluation protocol

The runner calls the application's actual `OpenAIJudge`, including its parser and
failure behavior. It does not replace the application or modify annotation tables.
The workbench can consume the same JSON profile with `scripts/run_local.py --judge-profile`.

## Fixed pilot

`data/translation_probe_v1.jsonl` contains 20 synthetic EN→ES examples: four each
for clean, negation, number/unit, terminology and omission. They come from six
existing demo gold records and a generated 15-record perturbation probe. One
identical source/hypothesis pair is deduplicated; its alias is retained. There are
11 parent templates, so the examples are not all independent. `scripts/build_eval_set.py`
reconstructs the fixture without using a model. Labels are inherited demo and
perturbation labels, **not expert-adjudicated clinical truth**.

The dataset, rubric, profile, code and weight provenance were frozen before the
2026-10-17 run. The protocol is one unrelated warmup followed by two shuffled
passes (seed 20261017 + pass number), one request at a time. Model temperature is
zero, output budget 256 tokens, structured JSON enabled, and Qwen thinking disabled
via `/no_think`. Each pass reports its own quality denominators. Repeats measure
stability and timing; they do not double the sample size. The runner retries no
requests and stops after three consecutive failures.

Quality measures: valid output, exact error-type set agreement, clean/error agreement,
severity agreement, critical-severity recall, false alarms on four clean controls,
and per-category recall. Category recall permits additional predicted tags; exact
set agreement does not. A failed request is incorrect in quality denominators.
Confidence values are model self-reports and are not used as ground truth.

Latency is non-streaming client wall time including request, prefill, generation,
response parsing and validation. One warmup and model loading are excluded.
P95 uses linear interpolation. Server-reported completion tokens divided by this
time are **effective output tokens/s**, not decode-only throughput. Prompt-prefix
caching is left at server defaults; this is a warm, short-input workload. It does
not establish cold performance, streaming TTFT or capacity under load.

## Reproduce on Windows

Use Python 3.12 and install `requirements.txt` with `constraints-runtime.txt`.
With the LM Studio CLI initialized:

```powershell
lms get "https://huggingface.co/Qwen/Qwen3-14B-GGUF@Q4_K_M" --gguf --yes
lms load qwen3-14b --gpu max --context-length 4096 --parallel 1 --identifier anota-qwen3-14b-q4km --yes
lms server start --bind 127.0.0.1 --port 1234
python scripts/evaluate_judge.py --profile evals/profiles/local-lmstudio.json --metadata provenance.json --out evals/runs/YOUR-NEW-RUN
python -m pip install -r requirements-figures.txt
python scripts/render_evaluation.py --run evals/runs/YOUR-NEW-RUN
python scripts/verify_evaluation.py --run evals/runs/YOUR-NEW-RUN
python scripts/run_local.py --judge-profile evals/profiles/local-lmstudio.json
```

`provenance.json` is a user-created JSON object recording the actual weight SHA256,
revision, runtime, context, quantization and hardware. The committed run's
`manifest.json` contains the observed fields as an example; do not copy its values
unless they match your installation. Verify weights with `Get-FileHash -Algorithm SHA256`.
`--gpu max` is a requested setting; GPU utilization/offloaded layers need separate telemetry.

Use a new output directory for any setting or code change. `--resume` skips already
recorded attempts only if all identity fields match. It does not retry failed
attempts. A resumed timing run may have different cache/thermal conditions; prefer
a fresh full run for comparisons. Never run two writers against one output directory.
Raw evaluation traces intentionally include the synthetic source, hypothesis and
response. Ordinary application requests do not enable that trace observer.

The run retains a `source-snapshot/` of the executed judge, runner, metric code and
rubric. These are evidence copies, not imported application modules. Their hashes
match the manifest even if a future checkout normalizes ordinary source line endings.
`.gitattributes` preserves dataset, run and figure bytes for cross-platform verification.
One historical pytest bytecode file in the paired-v2 source snapshot is retained
because that run's original hash manifest explicitly includes it. It is frozen
evidence, not an application dependency; ordinary bytecode caches remain ignored.
`scripts/verify_evaluation.py` checks those hashes and recomputes the metrics without
a model or plotting dependencies. The published `validation.json` separately records
the application regression suite and a real HTTP API → Qwen → SQLite smoke check.

## Evidence and next experiments

- [Unified severity policy study](runs/2026-10-17-severity-v3/report.md): 24 new primary texts, four predeclared disputed texts, two counterbalanced passes and four historical regression checks. The [protocol](severity-v3-protocol.md), [rule](../data/guidelines/severity-v3.md) and [dispute register](reviews/disputed-labels-v3.md) were frozen before inference. Earlier evidence is retained.
- For a fresh v3 run, use `python scripts/run_severity_study.py --metadata YOUR-PROVENANCE.json --out evals/runs/NEW-V3-RUN`, then `scripts/render_severity_study.py --run ...` and `scripts/verify_severity_study.py --run ...`. Both arms share the same profile; only the system rubric differs. The candidate remains evaluation-only.
- Prepare independent reference review with `python scripts/prepare_severity_review.py --out runtime/NEW-REVIEW-ROUND`. Distribute only `reviewer-packet/`; keep `coordinator-only/` separate. The packet contains 28 shuffled inputs and the rule, with no provisional gold, predictions or primary/disputed indicators. Prior exposure to this public report prevents a blinded first pass.
- [Paired prompt comparison](runs/2026-10-17-paired-rubric-v2/report.md): 24 new source parents, two counterbalanced passes, a candidate that failed the critical-recall gate, and unchanged baseline evidence.
- [Predeclared comparison protocol](comparison-v2.md), [development-label audit](reviews/pilot-v1-audit.md), and [post-run interpretation](reviews/paired-v2-interpretation.md).
- [Prepare a blind reviewer batch](reviews/blind-review.md) without model suggestions or provisional labels on screen.
- [Observed local run](runs/2026-10-17-lmstudio-qwen3-14b-q4km/report.md), with raw traces and hashes.
- [Deployment and experiment matrix](../docs/runtime-matrix.md): H100 and AWS templates, redundancy and acceptance checks.
- Keep this pilot unchanged. Create a separate expert-reviewed held-out set, split
  by source conversation / parent template, for language and domain claims. Resolve
  severity guideline ambiguity before model ranking; blind reviewers to predictions.
- Compare Q4_K_M and BF16 as different artifacts. To isolate hardware, run the same
  artifact and serving stack on both devices. Never attribute a mixed precision/runtime
  comparison solely to the GPU. Keep a third untouched test set after prompt tuning.

Sources: [official Qwen GGUF](https://huggingface.co/Qwen/Qwen3-14B-GGUF),
[LM Studio load CLI](https://lmstudio.ai/docs/cli/local-models/load),
[structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output).
