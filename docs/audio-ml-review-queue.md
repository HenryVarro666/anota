# Speech ML review queue

The Review tab includes a **Speech review** queue for audio tasks awaiting transcription review. Opening a row claims an annotation lease and opens the speech annotation workspace. Listening, editing the transcript, submitting, skipping and undoing use the existing speech lifecycle. The **Translation adjudication** section remains a separate workflow for adjudicating translation annotations.

## Ordering and batch policy

Choose **ML priority**, **QC priority**, or **Original order**. For hinted speech tasks, ML chooses the confidence model when persisted ASR contains at least one finite numeric word `probability` in `[0, 1]` or segment `avg_logprob <= 0`. Missing/null placeholders, booleans, strings and invalid numeric values do not count. Language-identification probability, no-speech probability and compression ratio alone never select the confidence model. Rows without usable transcription confidence use the legacy model.

Both models predict nonnegative expected edit errors per audio second. Higher density comes first within the availability tier; QC/coverage unavailable tasks receive the higher tier. Ties use the frozen study's deterministic item hash. Density is not a confidence probability. The two arms share units, but a queue combining confidence and legacy scores has **not** been independently evaluated as a combined ranking strategy. The frozen study evaluated the arms separately.

If any eligible hinted speech row cannot use ML, **all hinted speech rows in that selected queue fall back to QC**. This avoids interleaving QC priority scores and ML error densities as though they were comparable. Causes include missing or changed bundle bytes, dependency/version problems, unsupported ASR provenance, invalid feature input and prediction failures. The UI identifies QC fallback; the API supplies a fixed safe reason code. Original order bypasses ML ordering.

Blind batches retain their original order and queue slots for every sort choice. They expose neither ranking evidence nor QC hints. The policy is enforced by the server for queue responses and speech claims. Existing open assignments resume; a targeted queue selection cannot replace another open lease.

Speech claim audit-log records retain the requested sort, effective method, model/arm/bundle fingerprint when ML is used, and whether selection was manual (`targeted`) or automatic (`next`). This is server-side audit evidence and is not added to annotator-facing claim payloads. Blind claims retain only their safe original-order selection evidence.

## Supported ASR domain

The models were validated on English LibriSpeech using this fixed ASR configuration:

| Field | Required value |
| --- | --- |
| `language` | `en` |
| `detected_language`, if present | `en` |
| `model.name` | `base` |
| `model.repo_id` | `Systran/faster-whisper-base` |
| `model.revision` | `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66` |
| `config.beam_size` | `5` |
| `config.vad_filter` | `true` |
| `config.word_timestamps` | `true` |
| `config.condition_on_previous_text` | `false` |
| `config.task` | `transcribe` |
| `config.compute_type` | `int8` |
| `config.preprocessing` | `decode-original-audio-v1` |

Missing or mismatched fields cause QC fallback. Chinese and other ASR models are outside this runtime's ML domain. Other English corpora with the exact supported ASR provenance are allowed as **exploratory use**; effectiveness on those corpora has not been validated.

## Portable deployment

Use Python 3.12 for the verified full-project environment. ML dependencies are optional and loaded when scoring is requested. Starting the annotation server does not require loading a model. The default bundle directory is `evals/runs/2026-10-17-librispeech-ml-ranking-v2/outputs`.

Install the optional inference dependencies and create a minimal portable bundle from the reviewed model release:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-speechrank-runtime.txt
.venv/Scripts/python.exe scripts/package_speechrank_models.py C:/anota-models/speechrank-v2
$env:ANOTA_SPEECHRANK_BUNDLE = 'C:/anota-models/speechrank-v2'
.venv/Scripts/python.exe run.py --db anota.db
```

The destination must be new. The generated bundle contains five files: `model-legacy.joblib`, `model-legacy.json`, `model-confidence.joblib`, `model-confidence.json` and `manifest.json` (about 297 KB in total). It contains no audio, reference transcripts or gold labels. Copying the reviewed outputs directory and pointing the environment variable to that copy also works; training/evaluation data are unnecessary for runtime inference.

The minimal optional inference environment is pinned in [`requirements-speechrank-runtime.txt`](../requirements-speechrank-runtime.txt): NumPy, SciPy, scikit-learn, joblib and threadpoolctl. It avoids installing ASR, training/evaluation and plotting tooling for web inference. The runtime requires the card's exact `scikit-learn==1.9.1` version. The existing `requirements-speechrank.txt` remains the full experiment environment. If inference dependencies are unavailable, QC review remains usable.

The default Docker image contains the speech package source but no optional
ML dependencies or `evals/` artifacts, so its default bundle path will not contain
the model files. To enable ML ordering, package the bundle on the host as above
and build a derivative image using this `Dockerfile.speechrank`:

```dockerfile
FROM anota
COPY requirements-speechrank-runtime.txt /tmp/requirements-speechrank-runtime.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements-speechrank-runtime.txt
```

Build the base and derivative images from the repository root, then mount the
external bundle (PowerShell example):

```powershell
docker build -t anota .
docker build -f Dockerfile.speechrank -t anota-speechrank .
docker run -p 8420:8420 -v anota-data:/data -v C:/anota-models/speechrank-v2:/models/speechrank:ro -e ANOTA_SPEECHRANK_BUNDLE=/models/speechrank anota-speechrank
```

Audio is separate from the model bundle. Mount its registered storage and import
a manifest with paths valid inside the container; a database containing Windows
host paths does not automatically translate those paths. The base image needs
no ASR dependencies to review already persisted ASR/QC. Actual Docker build/run
validation is delegated to the configured CI and remains unexecuted locally.

The runtime binds the bundle to the reviewed model/card hashes, frozen feature and model source hashes, feature order/version, sklearn version, and protocol/config/evaluation-seal provenance. It hashes actual artifact bytes on each scoring call, including cache hits, and deserializes the verified byte snapshot. A changed manifest cannot authorize arbitrary replacement joblib files. New model releases require a deliberately reviewed runtime update. Treat joblib files as trusted deployment artifacts.

Hash-bound feature/model source must retain LF bytes on Windows; the repository's
`.gitattributes` fixes their checkout format. Source byte mismatches intentionally
disable ML instead of authorizing a changed model schema.

Models and predictions are cached; prediction keys bind the bundle fingerprint and allowlisted feature content. Inference never fits a model. Neither gold fields nor local paths enter model features or queue responses.

## Import and confidence retention

Import persisted `speech-v1` ASR manifests through the existing speech importer, with optional validated `audio_analysis` reports. The importer retains raw word probability and segment avg-logprob/no-speech/compression/temperature fields, including explicit unavailable values, in stored provenance and speech exports. The annotator-facing payload remains a whitelist; raw confidence arrays, ASR configuration, references and local paths are excluded.

Older databases imported before confidence retention may already have discarded these fields. They cannot be reconstructed from text, language probability or model scores. Reimport the original persisted ASR manifest into a suitable new batch/database when its original confidence evidence is available. Do not synthesize confidence for old records.

## Verification

The [release validation record](../evals/reviews/2026-10-17-anota-ml-queue-v1/release-validation.json) binds the full test result, final HTTP evidence, browser screenshot, unchanged frozen study hashes and local-service backup/restart checks.

The runtime contract tests are in [`tests/test_speechrank_runtime.py`](../tests/test_speechrank_runtime.py). They cover confidence routing, malformed/missing input, domain rejection, hash verification before loading, dependency/version failure, schema drift, finite prediction validation, batch prediction, content caching and bundle invalidation.

The real-data HTTP verification entry point is [`scripts/verify_speech_ml_queue.py`](../scripts/verify_speech_ml_queue.py). It uses persisted confidence/legacy ASR rows and frozen QC reports with a temporary database; it performs no new ASR and opens no reference/gold transcript files. An automated submit/undo probe checks lifecycle mechanics and is not a human annotation or a ranking-effectiveness result.

At the original integration checkpoint, validation completed with **462 project tests passing** (35.33 seconds), including **98 focused tests**. Ruff checks for the new code, JavaScript syntax checks and diff whitespace checks passed. The [retained integration report](../evals/reviews/2026-10-17-anota-ml-queue-v1/README.md) and [machine-readable verification](../evals/reviews/2026-10-17-anota-ml-queue-v1/verification.json) record the final application checks.

The real HTTP probe used **10 confidence dev recordings and 10 legacy test recordings** together in a temporary review queue. It verified ML/QC/FIFO order, claim consistency, lease conflicts, submit/undo/export/restart, exact confidence/QC retention, blind evidence removal and whole-queue QC fallback for missing or tampered bundles. It made **zero new ASR calls**, collected **zero human labels** and read no gold/reference transcript files. Its explicit automated lifecycle submission is only a functional test.

At that checkpoint, the original 20-clip English/Chinese smoke batch on port `8422` correctly uses whole-queue QC fallback with `unsupported_language`, because Chinese recordings are outside the supported ML domain. A separate 20-recording English fixture on port `8423` demonstrates both confidence and legacy ML arms. The English-only integration fixture and the mixed-language smoke batch serve different checks; the runtime does not apply these models to Chinese.

Browser checks verified switching ML/QC order, a blind queue without ranking evidence, and **Resume current task** returning to the existing waveform with **Play selection** available. The [queue screenshot](../evals/reviews/2026-10-17-anota-ml-queue-v1/queue.png) preserves the inspected UI. These checks establish integration behavior; they do not measure human listening accuracy or time savings.

Frozen source and output hash checks also confirmed that the research artifacts were unchanged by the application integration.

The [frozen v2 learning study](../evals/runs/2026-10-17-librispeech-ml-ranking-v2/README.md) records the research protocol and arm-specific evaluation; application integration checks do not replace that evaluation.
