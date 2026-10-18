"""Real bundled Silero VAD, applied independently to each original channel."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from .schema import file_sha256


def vad_provenance():
    """Resolve local model identity; never downloads a model or instantiates ASR."""
    import faster_whisper.vad as vad
    path = Path(vad.get_assets_path()) / "silero_vad_v6.onnx"
    if not path.is_file():
        raise RuntimeError("bundled Silero v6 ONNX model is unavailable")
    return [
        {"name": "faster-whisper-silero", "version": importlib.metadata.version("faster-whisper"), "model_sha256": file_sha256(path)},
        {"name": "onnxruntime", "version": importlib.metadata.version("onnxruntime"), "model_sha256": None},
        {"name": "pyav", "version": importlib.metadata.version("av"), "model_sha256": None},
    ]


def run_vad(samples, sample_rate, config):
    """Return intervals and provenance; PyAV resamples without channel cancellation."""
    import av
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    provenance = vad_provenance()
    options = VadOptions(
        threshold=config["vad_threshold"],
        min_speech_duration_ms=config["vad_min_speech_ms"],
        min_silence_duration_ms=config["vad_min_silence_ms"],
        speech_pad_ms=config["vad_speech_pad_ms"],
        max_speech_duration_s=config["max_duration_s"],
    )
    intervals = []
    for channel in range(samples.shape[1]):
        pcm = np.ascontiguousarray(samples[:, channel], dtype=np.float32)
        if sample_rate != 16000:
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            frames = []
            for offset in range(0, len(pcm), 65536):
                frame = av.AudioFrame.from_ndarray(pcm[offset:offset + 65536][None, :], format="fltp", layout="mono")
                frame.sample_rate = sample_rate
                frames.extend(resampler.resample(frame))
            frames.extend(resampler.resample(None))
            pcm = np.concatenate([frame.to_ndarray()[0] for frame in frames])
        for span in get_speech_timestamps(pcm, options, sampling_rate=16000):
            intervals.append((span["start"] / 16000, span["end"] / 16000))
    return intervals, provenance
