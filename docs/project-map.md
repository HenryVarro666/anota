# Anota project map

The `anota` repository is one product repository with four cooperating modules
and six frozen research runs. The parent `TTS` directory is a local workspace,
not a second repository or a separately packaged application.

## Four modules in one repository

| Module | Source | Responsibility | Runtime/dependency boundary |
| --- | --- | --- | --- |
| Anota workbench | `app/`, `static/`, `run.py` | Translation annotation, speech listening/revision, review, audit and content-hashed exports | One CPU FastAPI process; three core direct dependencies |
| SpeechPrep | `speechprep/` | Audio manifests, offline ASR, state, resumable success cache | Optional speech environment; ASR runs outside the web process |
| SpeechQC | `speechqc/` | Signal/VAD/timing analysis, validated reports and QC priority | Analysis is offline; schema/priority utilities support model-free web review |
| SpeechRank | `speechrank/` | Frozen ASR/QC features, model training helpers and verified inference | Optional inference dependencies + reviewed bundle; no training in the web process |

These modules share task/provenance contracts and are not four independently
released packages. Keep them together while their interfaces evolve.
`scripts/` contains launchers, preparation/evaluation tools and verification;
shared web runtime logic belongs in a module, not in an experiment script.

The annotation database has **13 application tables**: nine original core tables,
`schema_migrations`, and `audio_assets`, `speech_tasks`, `speech_annotations`.
The frontend has four JavaScript entry points (`app.js`, `review.js`, `dash.js`,
`speech.js`) with no build step. See [README](../README.md) for startup and
[Windows setup](local-windows.md) for direct virtual-environment commands.

## Six frozen studies

| Run | Question and evidence | Interpretation boundary |
| --- | --- | --- |
| [Local Qwen judge pilot](../evals/runs/2026-10-17-lmstudio-qwen3-14b-q4km/report.md) | 20 unique synthetic EN→ES examples, repeated twice | Synthetic references; not clinical or independent expert validation |
| [Paired rubric v2](../evals/runs/2026-10-17-paired-rubric-v2/report.md) | 24 new source parents, two counterbalanced passes | Candidate missed a critical-severity gate; baseline remains default |
| [Unified severity v3](../evals/runs/2026-10-17-severity-v3/report.md) | 24 new primary texts and four disputed texts | Severity pilot gates passed; category tradeoff and independent reference review remain |
| [Audio QC v1](../evals/runs/2026-10-17-audio-qc-v1/RESULTS.md) | 66 frozen engineering cases | Advisory signal checks; no human accuracy/time-savings claim |
| [LibriSpeech QC ranking v1](../evals/runs/2026-10-17-librispeech-qc-ranking-v1/README.md) | Full 5,559-recording ASR-error capture comparison | Frozen QC rule did not establish useful ranking gain |
| [LibriSpeech ML ranking v2](../evals/runs/2026-10-17-librispeech-ml-ranking-v2/README.md) | Legacy/confidence models and speaker-isolated holdout | Arms evaluated separately on fixed English ASR; mixed-arm queue and human efficiency not established |

These directories contain frozen inputs, protocols, outputs and source snapshots
used for reproducibility. Keep their original bytes and conclusions. New release
checks belong in tests, CI or a separate release record, not in rewritten historic
run manifests. Integration reviews under `evals/reviews/` describe application
behavior and do not replace the research evaluation.

Additional local ASR smoke/benchmark outputs live under ignored `runtime/`; they
are not extra published run directories. See the [speech smoke guide](speech-smoke.md),
[LibriSpeech ASR benchmark](librispeech-asr-benchmark.md), and
[public datasets and experiment options](public-datasets-and-paper-experiments-2026-10-17.md).
Ignored audio, caches, state databases and preview databases are local artifacts,
not downloadable GitHub links. Versioned verification summaries remain linked
from the relevant integration review.

## Related GitHub repositories

| Repository | Keep its current scope | Potential Anota boundary; not yet integrated |
| --- | --- | --- |
| [anota](https://github.com/HenryVarro666/anota) | Annotation, quality review and reproducible evidence | Human labels, revisions, provenance and hashed exports |
| [dots-tts-preference-alignment](https://github.com/HenryVarro666/dots-tts-preference-alignment) | TTS preference/reward/training research | An explicit audio A/B preference-review schema and export adapter |
| [kokoro-aws-demo](https://github.com/HenryVarro666/kokoro-aws-demo) | TTS/ASR service and AWS deployment | Persisted ASR adapter with timestamps, model/configuration provenance and real transcription confidence |
| [tts-inference-engine-bench](https://github.com/HenryVarro666/tts-inference-engine-bench) | Inference engine performance experiments | Shared result/provenance conventions, without merging benchmark runtime into the web app |

No cross-repository service or training integration is implemented or validated
by this release. Preference rewards and expected ASR edit errors per second are
different targets; one cannot stand in for the other. Language-identification
probability is not transcription confidence. An ASR service integration must
retain genuine word probability or segment average log probability where available,
plus timestamps and the model/configuration provenance required by the importer.

The reviewed SpeechRank runtime supports English LibriSpeech with a fixed
`Systran/faster-whisper-base` revision and decoding configuration. Chinese and
other ASR models fall back to QC; other English corpora with that exact provenance
are exploratory. See the [exact domain contract](audio-ml-review-queue.md#supported-asr-domain).

For future comparisons, record audio SHA256, model/weight revision, configuration,
dataset/split identity, failures and timing definitions consistently in each
repository. Anota exports can provide reviewed evidence; a downstream adapter
still needs explicit contract tests and its own evaluation.

## Release and source provenance

Use **Python 3.12** as the verified full-project baseline. The core application
targets ≥3.10; some speech preparation/evaluation commands require ≥3.11 through
`datetime.UTC`. Full-project support on 3.10 has not been established.

The default Docker image is a CPU annotation image and installs no ASR/VAD/ML
inference dependencies. It excludes local `runtime/` data and frozen studies.
Optional ML ordering uses a separate inference environment and a five-file
external bundle; see [portable deployment](audio-ml-review-queue.md#portable-deployment).
Windows tests and source-level package checks do not establish a successful
Docker build/run; inspect the pushed commit's actual GitHub checks.

Anota has a project [MIT license](../LICENSE). Selected speech utilities came
from user-provided source; [SOURCES.json](../speechprep/vendor/SOURCES.json) records
paths, hashes and modifications. No original license file was found for those
utilities, and the provenance record does not assert a third-party license.
Dataset/model terms and their provenance are separate from the project license.
