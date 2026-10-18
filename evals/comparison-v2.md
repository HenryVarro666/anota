# Paired rubric clarification experiment

## Question and intervention

Does clarifying the existing rubric improve judge labels on **new source texts**,
at the same model/precision/runtime, without worsening false alarms, critical-error
recall or request latency beyond the declared tolerance?

Baseline: the original `local-lmstudio.json` request. Candidate:
`local-lmstudio-v2.json`, changing only the user-prompt suffix. It adds a proposition
check, minimal-specific-category convention, consequence-based severity and short
evidence quotes. Both use Qwen3-14B Q4_K_M, the same 4096-token loaded context,
256 output tokens, temperature zero, JSON schema and `/no_think`. The shared system
rubric and parser are unchanged. This is a **prompt plus label-policy clarification**,
not a new model, fine-tune or quantization comparison.

[Development diagnosis](reviews/pilot-v1-audit.md) distinguishes semantic misses,
tagging policy and questionable reference severity. Original artifacts and labels
stay intact. Reference proposals are not applied to either headline score.

## Holdout and leakage boundary

`data/holdout_v2.jsonl` has 24 newly authored EN→ES source parents: four clean, four
negation, four number, four terminology, four omission, two other meaning errors,
one addition and one grammar error. The parents do not reuse development source
sentences. Administrative contrasts test whether the judge overuses critical.
Each item has a prewritten reference rationale. No gold label or rationale is sent
to the judge: only source and hypothesis.

The same agent wrote the candidate and these provisional synthetic labels before
inference. **Independent texts are not independent expert annotations.** These are
not randomly sampled production calls, an external corpus or evidence of absence
from model pretraining. The 24 items cannot establish clinical deployment quality.

## Frozen order and gates

`scripts/compare_judges.py` writes a manifest and exact source snapshots before its
first request. The manifest hashes the texts, both profiles, code, rubric and
development audit, and enumerates the entire call order. Before this run, the model
file's SHA256 was rechecked against the preserved baseline; the supplied provenance
records that check. The runner compares provenance on resume but does not itself
inspect a remote model server's weight file.

There is one warmup per arm. Each holdout item is judged by both arms in each of
two shuffled passes. First-arm assignment alternates by item and reverses on pass
two, balancing immediate cache/order effects. Requests are sequential; no other
generation workload is scheduled. Then the candidate evaluates all 20 old
development items once, for exploratory diagnosis only. The old development
baseline is historical and not a contemporaneous randomized control.

Total: **96 paired holdout calls + 20 development calls + 2 warmups**. No automatic
retries. Three consecutive failures stop the run. Partial and failed attempts stay
in raw evidence; failed requests are incorrect in quality denominators. Two passes
do not turn 24 examples into 48 independent samples. Warmups and development calls
are excluded from holdout timing and quality.

Before inference, the following pilot gates were specified **for each pass**:

- Valid output and binary error/clean agreement must not decrease.
- Critical-severity recall must not decrease; clean false alarms must not increase.
- Candidate request P95 must be no more than 1.5× baseline P95.

Exact category and severity agreement, paired wins/losses and per-category recall
are reported even if a gate fails. There is no post-holdout prompt or label tuning,
no cherry-picking a best pass, and no automatic production promotion. The baseline
remains the default profile. Any next candidate needs a new version and a new
unseen test set for confirmatory claims.

Latency is client request wall time including prefill, generation and validation.
Input length and output tokens can differ because they are effects of the prompt.
Prefix caching remains at server defaults; thermal, power and host load are not
controlled. This is an interactive desktop workload, not an isolated GPU benchmark.

## Reproduce

```powershell
python scripts/compare_judges.py --metadata evals/provenance/890m-qwen.json --out evals/runs/YOUR-NEW-RUN
python scripts/render_comparison.py --run evals/runs/YOUR-NEW-RUN
python scripts/verify_comparison.py --run evals/runs/YOUR-NEW-RUN
```

First replace provenance with measurements of your own machine; do not copy weight,
driver or runtime claims without checking them. The committed profile files and
fixture are already frozen; the builder refuses to overwrite them. Use a new output
directory. `--resume` requires identical hashes and skips completed attempts; use
a fresh uninterrupted run for timing comparisons. Only one writer may own a run.

H100/AWS experiments should use the same texts, prompts and request budgets behind
an OpenAI-compatible endpoint, with the actual hardware/model provenance recorded.
The paired runner currently loads the two local profile files explicitly; create a
versioned protocol/profile pair for another endpoint rather than silently changing
this committed experiment. See [runtime boundaries](../docs/runtime-matrix.md).
