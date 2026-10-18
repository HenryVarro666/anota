# What the second experiment establishes

**Decision: retain the original prompt as the default.** The candidate improves
exact category agreement on the new texts but fails the predeclared critical-recall
gate in both passes. Its first-pass P95 also exceeds the 1.5× latency allowance.
The second-pass latency gate passes; this does not override either failed gate.

This interpretation was written after observing the holdout. It does not change
the frozen labels, prompt, thresholds or metrics. Evidence is in the
[complete report](../runs/2026-10-17-paired-rubric-v2/report.md),
[raw traces](../runs/2026-10-17-paired-rubric-v2/attempts.jsonl), and
[pre-inference development audit](pilot-v1-audit.md).

## Separate three kinds of disagreement

| Question | Evidence | What we can conclude |
|---|---|---|
| Does the configuration notice a source/target discrepancy? | Both arms detect all 20 erroneous holdout texts and accept all four clean texts in both passes. The historical two misses are checked separately on the development set. | Detection is successful on this small constructed holdout; this is not an estimate for natural multilingual calls. |
| Does it use the expected category convention? | Exact sets rise from 19/24 and 18/24 to 21/24 and 22/24. Many changes remove redundant `mistranslation` from a more specific label. | Making the annotation convention explicit improves policy adherence. These cases were already detected, so the gain is not entirely new semantic capability. |
| Does it grade the consequences correctly? | Severity agreement is 15/24 in both baseline passes versus 14/24 and 15/24 for the candidate. Critical recall falls from 9/9 to 8/9 in both passes. | The candidate does not resolve severity calibration and introduces a consistent high-consequence undergrade. |

The reference labels are provisional, authored by the same agent as the candidate
before inference. The 24 source parents are new and do not overlap development
parents; **independent texts are not independent expert annotations**. In particular,
administrative major/minor boundaries should be settled by reviewers using the
intended workflow, rather than relabeled to agree with a model.

The candidate detects **both historical semantic misses** in its single development
follow-up: `t003::p2-omis1` becomes `omission / minor`, and `t008::p2-term2` becomes
`terminology / minor`. Its rationales identify the missing “throughout the day” and
medication→supplement substitution. Detection is repaired on these known cases, but
neither severity matches the original reference (major and critical respectively).
Those references were already flagged for adjudication before this run. These are
development cases used to design the prompt, not new evidence of generalization.

## The consequential regression is grading, not a missed discrepancy

In `h19`, the source says to start a transfusion **only after an identity check is
complete**. The hypothesis drops that prerequisite. Both prompts detect the omission;
the baseline calls it critical twice, while the candidate calls it major twice.
The candidate's own rationale identifies the missing condition. Under the frozen
explicit-action rubric this is a severity regression, not a clean/error miss.
The source itself states the prerequisite, so the interpretation does not require
inventing unstated drug facts or patient history.

There is also a less stable regression: on `h21`, the candidate first tags an
appointment moved earlier instead of later as `mistranslation + negation_polarity`
and critical; the second pass returns `mistranslation / major`, matching the reference.
The baseline matches both times. Temperature zero did not produce identical labels
for every repeated request: both arms match types and severity on 23/24 valid pairs.

## Why a prompt-only fix is insufficient so far

The system message includes the original guideline's broad “critical triggers.”
The added user-message suffix asks for consequence-based grading. These instructions
can pull in different directions. The raw outputs are consistent with that ambiguity:
parking signatures (`h06`), document visibility (`h08`), identifiers (`h12`) and
document types (`h14`) still receive critical labels under both prompts. Required
documents (`h18`), availability times (`h20`) and an invented date (`h23`) are detected
but graded minor under both prompts, contrary to their provisional major references.

This is a plausible explanation, **not an isolated causal result**. The experiment
changes a package of instructions, including both content and input length. It does
not separately randomize instruction priority, checklist, taxonomy and severity rules.
It also does not compare another model or precision. The evidence therefore cannot
show that Qwen3-14B itself is incapable, or that Q4 quantization caused the mistakes.

Severity did change in a useful direction for `h16` (interpreter replaced by
receptionist): critical → major in both passes. But `h10` (room 206 → 260) shifts
critical → minor, skipping its major reference. Lower severity alone is not better
calibration, and fewer critical predictions must be considered together with missed
critical items.

## Speed has a workload explanation, with limits

Pooled request P50 rises **14.02 → 18.14 seconds** and P95 **18.78 → 26.58 seconds**.
Mean input grows from **610.33 to 841.33 tokens**, and mean completion from **63.88
to 85.21 tokens** per holdout request, as reported by the server. These are end-to-end HTTP times,
including prefill, not decode-only tokens/second. Prefix caching is enabled by the
server's defaults. All outliers remain in the distribution, including the unusually
slow baseline request. Power, thermals and background host work were not isolated.

Two passes over the same 24 texts are repeated measurements, not 48 new cases per
arm. No significance claim or production latency guarantee follows from this pilot.

## Next experiment, without changing this benchmark retrospectively

1. Agree on one versioned operational rubric for humans and the model. Resolve
   single-specific versus multiple labels and major/critical boundaries explicitly.
   Review the known disagreement cases separately; preserve original labels and
   record each adjudication with reviewer identity and rationale.
2. Use the [blind review batch](blind-review.md) to obtain labels without model
   suggestions or provisional references on screen. An independent EN/ES reviewer
   is still needed. Agreement between one person and an LLM is not inter-reviewer
   agreement, and this synthetic set has no clinical certification.
3. Develop the next candidate only on development cases. Keep this now-inspected
   holdout for regression checks and create a new, conversation-disjoint test set
   before testing the next promotion claim. Include conditions, administrative
   contrasts, fluent omissions, accents/code-switching when audio is added, and
   naturally occurring examples with appropriate permissions.
4. Isolate one cause per ablation: unified rubric versus old rubric; then a concise
   rationale constraint; then another model/precision. Keep weights fixed for prompt
   comparisons, and prompts fixed for precision comparisons. Capture failed outputs
   and critical undergrades as well as exact-label scores.
5. Reuse the HTTP adapter on H100/AWS with a **new run identity**, endpoint contract
   check and weight/runtime provenance. Test quality at concurrency one first;
   capacity, restart/failover and sustained-load tests belong to separate runs. The
   [runtime matrix](../../docs/runtime-matrix.md) describes the two-H100 spare-capacity
   plan and the work still needed before AWS production. Neither environment was
   exercised by this Windows experiment.

Until those checks pass, the model's output remains a suggestion for human review.
