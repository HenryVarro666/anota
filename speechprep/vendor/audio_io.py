"""Header reader extracted unchanged from data_preprocess/utils/audio_io.py.

Source and SHA-256 are recorded in SOURCES.json. No private runtime imports.
"""

import wave
from pathlib import Path


def wav_info(path: str | Path) -> dict:
    """Header-only metadata (no full read)."""
    with wave.open(str(path), "rb") as w:
        n, sr = w.getnframes(), w.getframerate()
        return {"sample_rate": sr, "channels": w.getnchannels(),
                "bits": 8 * w.getsampwidth(), "frames": n,
                "duration_s": round(n / sr, 3) if sr else 0.0}
