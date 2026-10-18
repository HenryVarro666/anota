# Anota speech ML queue verification

[Release validation](release-validation.json) records the original integration checkpoint's 462-test pass, frozen-study integrity checks, browser evidence and the updated local service, including normal expiry of old leases. [User guide](../../../docs/audio-ml-review-queue.md).

Real persisted ASR samples: 10 confidence dev rows and 10 legacy test rows (or the configured count per arm), merged with frozen QC by item ID. No gold/reference transcript files were opened and no ASR was run. One explicitly marked automated lifecycle submission is not a human label.

The [verification report](verification.json) records input file SHA256, sample IDs, current application/runtime/JavaScript source SHA256, and actual HTTP queue results. It checks both ML arms, sorting, claim/lease, submit/undo/export/restart, confidence privacy, clean batches, translation, and missing/tampered model QC fallback. Confidence and QC export exactly.

Reproduce: `.venv/Scripts/python.exe scripts/verify_speech_ml_queue.py`. Verification uses a temporary database and never writes frozen study inputs.

Local-only outputs are written to `runtime/speech-ml-queue-check/verification.json`
and the optional `runtime/speech-ml-queue-check/preview.db`. The `runtime/` directory
is Git-ignored, so those paths are not downloadable repository artifacts. The
preview database contains automated probes only. The versioned
[verification report](verification.json) remains the published verification evidence.

Browser verification: actual ML/QC switching, clean evidence hiding, Resume restoring the current waveform, and Play selection starting audio playback all passed automated UI checks; no human labels were created. [Queue screenshot](queue.png).
