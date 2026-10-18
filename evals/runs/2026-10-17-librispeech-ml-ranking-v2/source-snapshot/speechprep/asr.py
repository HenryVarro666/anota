"""Lazy real faster-whisper inference with immutable model provenance."""

from __future__ import annotations

import importlib.metadata
import io
import re
import wave
from dataclasses import dataclass
from pathlib import Path

from .manifest import digest_json, file_sha256
from .vendor import asr_whisper


@dataclass(frozen=True)
class ModelSpec:
    name: str
    revision: str
    repo_id: str | None = None
    local_path: str | None = None


def resolve_model(name: str, revision: str | None = None) -> ModelSpec:
    path = Path(name)
    if path.is_dir():
        files = [(str(p.relative_to(path)), file_sha256(p))
                 for p in sorted(path.rglob("*")) if p.is_file()]
        return ModelSpec(name, "sha256:" + digest_json(files), local_path=str(path.resolve()))
    repo_id = name if "/" in name else "Systran/faster-whisper-" + name
    if revision and re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        resolved = revision.lower()
    else:
        from huggingface_hub import HfApi
        resolved = HfApi().model_info(repo_id, revision=revision or "main").sha
        if not resolved or not re.fullmatch(r"[a-fA-F0-9]{40}", resolved):
            raise ValueError("model repository did not resolve to an immutable commit")
    return ModelSpec(name, resolved, repo_id=repo_id)


def runtime_versions() -> dict:
    versions = {}
    for package in ("faster-whisper", "ctranslate2", "av"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def check_decoder_compatibility(decoder=None) -> None:
    """Exercise the installed decoder API before downloading/loading any model.

    Package resolver constraints alone do not guarantee API compatibility: PyAV
    19 removed metadata_errors, which faster-whisper 1.2.1 still passes to av.open.
    An in-memory PCM probe also works offline and needs no real dataset.
    """
    if decoder is None:
        from faster_whisper.audio import decode_audio
        decoder = decode_audio
    probe = io.BytesIO()
    with wave.open(probe, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 1600)
    probe.seek(0)
    try:
        samples = decoder(probe, sampling_rate=16000)
        if len(samples) == 0:
            raise ValueError("decoder returned no samples for a valid PCM WAV")
    except Exception as exc:
        versions = runtime_versions()
        raise RuntimeError(
            "Speech decoder compatibility preflight failed "
            f"(faster-whisper={versions['faster-whisper']}, av={versions['av']}). "
            "Install the tested dependencies with: python -m pip install -r requirements-speech.txt. "
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc


class FasterWhisperAdapter:
    def __init__(self, model: ModelSpec, config: dict):
        self.model, self.config = model, config
        self._snapshot = model.local_path
        check_decoder_compatibility()

    def transcribe(self, path: str, language: str) -> dict:
        if self._snapshot is None:
            from huggingface_hub import snapshot_download
            self._snapshot = snapshot_download(
                self.model.repo_id, revision=self.model.revision,
                allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"])
        return asr_whisper.transcribe(
            path, model_size=self._snapshot, device=self.config["device"],
            compute_type=self.config["compute_type"], language=language,
            vad_filter=self.config["vad_filter"], beam_size=self.config["beam_size"],
            cpu_threads=self.config["cpu_threads"],
            condition_on_previous_text=self.config["condition_on_previous_text"])
