# Local → lab → AWS: runtime and experiment boundaries

## Application and optional speech runtimes

Python **3.12** is the verified full-project baseline and configured CI target.
The core annotation application targets ≥3.10; some speech preparation and
experiment scripts require ≥3.11 because they import `datetime.UTC`. Full
speech/ML compatibility on 3.10 has not been established.

| Component | Runtime boundary | Release scope |
| --- | --- | --- |
| Anota (`app/`, `static/`) | One CPU web process; translation and speech review | Three core dependencies; no ASR model on startup |
| SpeechPrep (`speechprep/`) | Offline ASR preparation and resumable cache | Separate optional speech dependencies and model download |
| SpeechQC (`speechqc/`) | Offline DSP/VAD analysis; stdlib report/priority use in web | Separate analysis dependencies; persisted reports can be reviewed without loading VAD |
| SpeechRank (`speechrank/`) | Optional verified model inference in the review queue | Exact reviewed dependencies and five-file bundle; unavailable ML falls back to QC |

The [project map](project-map.md) documents the six frozen experiments and related
repositories. `dots-tts-preference-alignment` training, `kokoro-aws-demo` service
deployment and `tts-inference-engine-bench` benchmarking remain separate projects.
Their adapters into Anota have not been implemented or validated.

The Anota web process remains a CPU application. GPU text judges live in a separate
service behind the OpenAI-compatible HTTP adapter; offline SpeechPrep ASR can run
in an independent CPU/CUDA worker. No CUDA, Vulkan or ROCm import is added to the
workbench. The same application code, synthetic dataset, rubric and request schema
can be exercised against each endpoint; compatibility still requires a real run.

| Environment | Model service | Configuration | Evidence |
|---|---|---|---|
| Windows / HX 370 / 890M | LM Studio, Vulkan, Qwen3-14B Q4_K_M | `evals/profiles/local-lmstudio.json` | Real two-pass pilot committed |
| Lab / 2 × H100 | Independently deployed OpenAI-compatible servers | `evals/profiles/lab-h100.json` | Template only; no H100 measurement |
| AWS | Private OpenAI-compatible model endpoint behind authenticated networking | `evals/profiles/aws-service.json` | Template only; no deployment or load test |

For offline ASR, the [RTX 5080 preflight](5080-environment-and-portability-2026-10-18.md)
and [deployment acceptance](5080-deployment-2026-10-18.md) (Chinese) record a separate
2026-10-18 working-tree snapshot: 20 real FLEURS train clips, CPU/int8 and
CUDA/float16 inference, CPU QC, cache resume and HTTP audio/queue checks.
The snapshot is identified by its release package hash and source manifest, rather
than a later `main` commit. This is an engineering smoke check, with no cluster,
production-capacity, GPU-speedup or new SpeechRank quality claim.

## Lab design with spare capacity

Start with one GPU per model replica if the chosen precision/context fits; verify
the actual H100 SKU and memory first. Use GPU 0 for the frozen baseline and GPU 1
for a candidate, or keep GPU 1 idle for a separately tested failover exercise.
Record separate endpoint/run IDs. Run each quality pilot at concurrency one;
increase concurrency only in a separate load experiment. Do not silently mix
replicas of different weights or precision into one result directory.

Tensor parallelism across both GPUs is an alternative for a model that requires
it, but provides one logical replica and consumes the spare. Two GPUs in one
machine share a host failure domain. Cross-host/AZ redundancy needs separate
infrastructure; none is provided by these profile files.

For each capacity step, measure P50/P95/P99, failures, queue depth, actual device
memory, host memory and utilization. As an initial planning target, leave at least
20% memory headroom and keep operating load below the measured saturation knee;
these are design targets, not validated limits for this hardware. Test cancellation,
model restart and draining before increasing traffic. Human annotation must remain
usable if inference fails. Never fall back to mock labels in production.

## Same protocol against another endpoint

```bash
export ANOTA_EVAL_BASE_URL="http://your-private-model-endpoint/v1"
export ANOTA_EVAL_MODEL="your-explicit-served-model-id"
export ANOTA_JUDGE_API_KEY="your-runtime-secret"
python scripts/evaluate_judge.py --profile evals/profiles/lab-h100.json --metadata provenance.json --out evals/runs/NEW-LAB-RUN
```

On Windows use `$env:NAME = "value"`. For AWS replace the profile with
`aws-service.json`. No credentials are committed. A native SageMaker InvokeEndpoint
URL is not automatically OpenAI-compatible: use a compatible serving container or
gateway and verify `/v1/models`, `/v1/chat/completions`, `response_format` and token
limits. Record model/weight revision, image digest, driver, server version, context,
precision and device details. The scripts neither provision nor bill cloud resources.

## Repeating the unified-severity study

For experiment 03, copy `evals/studies/severity-v3.json` to a new study file inside
`evals/studies/`. Set **both** `arms.baseline.profile` and `arms.candidate.profile`
to the same lab or AWS profile. Keep the two rubric paths and datasets unchanged.
The runner rejects different resolved profiles: this comparison changes only the
rubric, so both policies must use the same served model and endpoint. To measure
the other H100 replica, run the complete paired study again against its endpoint,
with separate provenance and a new output directory. Test failover separately.

```bash
python scripts/run_severity_study.py --study evals/studies/YOUR-H100-STUDY.json --metadata YOUR-H100-PROVENANCE.json --out evals/runs/NEW-H100-SEVERITY-RUN
python scripts/render_severity_study.py --run evals/runs/NEW-H100-SEVERITY-RUN
python scripts/verify_severity_study.py --run evals/runs/NEW-H100-SEVERITY-RUN
```

Record the actual artifact and serving settings; do not reuse the Windows GPU
metadata. The original desktop run had user-confirmed concurrent computation.
A speed comparison needs a fresh, controlled-load run with utilization telemetry;
its existing request times remain observations from that session. These 24 published
texts can serve as regressions, but further tuning needs a new untouched test set
and independently adjudicated labels. A completed `--resume` requires the original
source bytes, profiles and metadata; later speech-module edits correctly make the
live checkout a different identity. Preserve evidence and use a frozen checkout
for recovery validation instead of editing the original manifest.

## What the application currently guarantees

- One application-wide semaphore caps simultaneous judge calls across batches
  within **one process**. The local profile allows one. Multiple batches can still
  create queued worker threads; this is not a distributed queue or admission control.
- Duplicate runs of a batch return 409. Invalid/truncated responses fail validation;
  they cannot silently become `no_error`. Human annotation does not require a judge.
- Backend failures release the batch's running state; completed results remain
  append-only. The pilot has zero automatic retries. HTTP I/O timeout is not a hard
  end-to-end batch deadline.
- Production requests do not opt into raw trace capture. The committed evaluation
  traces contain synthetic text only.

## Before AWS production

The current SQLite connection/RLock design requires one Anota process and a durable
single-writer volume. Do not scale multiple application workers against the same
SQLite file. For multiple replicas, first implement shared transactional storage,
durable jobs, idempotent result writes and explicit distributed admission control.
This change does **not** implement those components or claim high availability.

Put real identity, reviewer RBAC and authenticated ingress in front of the app;
the existing annotator-ID field is self-reported. Separate app readiness from model
readiness and test real generation before promotion. Require endpoint contract tests,
held-out quality gates, restart/recovery, backup restore, schema migration and
rollback drills. Store versioned artifacts outside the container and retain exact
image/model/configuration provenance. Define access, retention and redaction for
real customer data separately from this public synthetic pilot.

`constraints-runtime.txt` pins the observed CPU dependency versions. Docker applies
them; the base image still uses a moving Python tag, so a release must also record
or pin its image digest. CI is configured for Windows, Linux x86_64, Linux ARM64 and
a Docker smoke test. [PR #1 checks](https://github.com/HenryVarro666/anota/pull/1/checks)
passed all three operating-system jobs and the actual CPU container lifecycle.
Docker was not run on the local Windows machine; later releases must check the
results attached to their own commit.
Compatibility of optional figure dependencies is separate from the application.

## Container release boundary

The default Docker image includes the web application and speech package source
but installs only `requirements.txt` constrained by `constraints-runtime.txt`.
It includes no ASR/VAD/ML dependencies, downloaded models, local `runtime/` data
or frozen experiment directories. Speech queue behavior must therefore remain
usable with ML unavailable. An inference-enabled derivative image installs
`requirements-speechrank-runtime.txt` and mounts a reviewed external bundle via
`ANOTA_SPEECHRANK_BUNDLE`; see the [deployment example](audio-ml-review-queue.md#portable-deployment).

`.gitattributes` preserves LF bytes for hash-bound source and frozen evidence,
including on Windows with `core.autocrlf=true`. A release must verify a new
checkout as well as the working copy. Do not rewrite frozen provenance hashes
to accept a platform-induced byte change. Container build and speech-route
checks are configured in CI; local source/package tests do not substitute for
a real Docker run. Remote results are attached to the pushed commit.
