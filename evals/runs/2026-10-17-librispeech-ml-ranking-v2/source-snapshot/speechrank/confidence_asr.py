"""Same Whisper decode as speechprep, additionally preserving engine confidence.

Use with speechprep.run_pipeline(adapter_factory=ConfidenceAdapter) and a copied
config whose adapter_version is ADAPTER_VERSION. Existing ASR output/caches are
not changed. No probability is inferred from timestamps, text, or reference data.
"""

from __future__ import annotations

import math

from speechprep.asr import FasterWhisperAdapter
from speechprep.vendor import asr_whisper

ADAPTER_VERSION = "speechrank-faster-whisper-confidence-v2"


def _persisted_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def transcribe_with_confidence(engine, audio_path: str, *, language: str | None = None,
                               vad_filter: bool = True, beam_size: int = 5,
                               condition_on_previous_text: bool = False,
                               model_name: str = "unknown") -> dict:
    """Testable decode boundary with the exact original decoding arguments."""
    segments, info = engine.transcribe(
        audio_path, word_timestamps=True, language=language,
        vad_filter=vad_filter, beam_size=beam_size,
        condition_on_previous_text=condition_on_previous_text,
    )
    words, texts, segment_rows = [], [], []
    for segment in segments:
        texts.append(segment.text.strip())
        segment_rows.append({
            "start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text.strip(),
            **{key: _persisted_number(getattr(segment, key, None)) for key in (
                "avg_logprob", "no_speech_prob", "compression_ratio", "temperature",
            )},
        })
        for word in segment.words or []:
            words.append({"w": word.word.strip(), "start": round(word.start, 3), "end": round(word.end, 3),
                          "probability": _persisted_number(getattr(word, "probability", None))})
    return {
        "text": " ".join(text for text in texts if text), "words": words, "segments": segment_rows,
        "language": info.language,
        "language_prob": round(getattr(info, "language_probability", 0.0), 3),
        "duration_s": round(getattr(info, "duration", 0.0), 3), "model": f"faster_whisper:{model_name}",
    }


class ConfidenceAdapter(FasterWhisperAdapter):
    def __init__(self, model, config):
        if config.get("adapter_version") != ADAPTER_VERSION:
            raise ValueError("ConfidenceAdapter requires its distinct adapter_version to isolate caches")
        super().__init__(model, config)

    def transcribe(self, path: str, language: str) -> dict:
        if self._snapshot is None:
            from huggingface_hub import snapshot_download
            self._snapshot = snapshot_download(
                self.model.repo_id, revision=self.model.revision,
                allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"],
            )
        engine = asr_whisper._get_model(
            self._snapshot, self.config["device"], self.config["compute_type"], self.config["cpu_threads"],
        )
        return transcribe_with_confidence(
            engine, path, language=language, vad_filter=self.config["vad_filter"],
            beam_size=self.config["beam_size"], condition_on_previous_text=self.config["condition_on_previous_text"],
            model_name=self._snapshot,
        )
