"""faster-whisper wrapper: transcript + word timestamps in one dict.

OPTIONAL DEPENDENCY:  pip install faster-whisper
The module imports lazily — importing it without the dependency is fine;
calling transcribe() without it raises a clear error.

    from utils.asr_whisper import transcribe, available
    if available():
        out = transcribe("utt.wav", model_size="large-v3")
        # {'text': ..., 'words': [{'w','start','end'}], 'language': 'en',
        #  'language_prob': 0.99, 'duration_s': 6.2, 'model': ...}

Note: models are cached per literal (size, device, compute_type) tuple;
"auto" and the concrete device it resolves to count as different keys.
"""

from __future__ import annotations

from typing import Any

_MODELS: dict[tuple, Any] = {}  # (size, device, compute_type) -> model cache


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model(model_size: str, device: str, compute_type: str, cpu_threads: int = 4):
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ImportError(
            "asr_whisper needs faster-whisper: pip install faster-whisper") from e
    key = (model_size, device, compute_type, cpu_threads)
    if key not in _MODELS:
        _MODELS[key] = WhisperModel(model_size, device=device,
                                    compute_type=compute_type, cpu_threads=cpu_threads)
    return _MODELS[key]


def transcribe(audio_path: str, model_size: str = "large-v3",
               device: str = "auto", compute_type: str = "auto",
               language: str | None = None, vad_filter: bool = True,
               beam_size: int = 5, cpu_threads: int = 4,
               condition_on_previous_text: bool = False) -> dict:
    """Transcribe one file -> {text, words, language, language_prob, duration_s}.

    language=None auto-detects; pass "en"/"es" to force (recommended for
    code-switched audio, where auto-detect causes translate-instead-of-
    transcribe failures).
    """
    model = _get_model(model_size, device, compute_type, cpu_threads)
    segments, info = model.transcribe(
        audio_path, word_timestamps=True, language=language,
        vad_filter=vad_filter, beam_size=beam_size,
        condition_on_previous_text=condition_on_previous_text)
    words, texts, segment_rows = [], [], []
    for seg in segments:
        texts.append(seg.text.strip())
        segment_rows.append({"start": round(seg.start, 3),
                             "end": round(seg.end, 3), "text": seg.text.strip()})
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "start": round(w.start, 3),
                          "end": round(w.end, 3)})
    # text always comes from segment texts: space-joining word tokens
    # corrupts no-space languages (zh/ja) and silently drops segments
    # that produced no word timestamps
    return {
        "text": " ".join(t for t in texts if t),
        "words": words,
        "segments": segment_rows,
        "language": info.language,
        "language_prob": round(getattr(info, "language_probability", 0.0), 3),
        "duration_s": round(getattr(info, "duration", 0.0), 3),
        "model": f"faster_whisper:{model_size}",
    }


def transcribe_many(paths: list[str], **kwargs) -> list[dict]:
    """Sequential batch (model stays hot). Parallelize across GPUs by
    sharding paths across processes, not threads."""
    return [transcribe(p, **kwargs) for p in paths]


if __name__ == "__main__":
    if available():
        print("asr_whisper: faster-whisper installed; "
              "transcribe('file.wav') is ready")
    else:
        try:
            transcribe("x.wav")
            raise AssertionError("expected ImportError")
        except ImportError as e:
            assert "pip install faster-whisper" in str(e)
        print("asr_whisper: dependency missing (expected on this machine); "
              "error message verified")
