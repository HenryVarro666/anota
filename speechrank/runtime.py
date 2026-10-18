"""Optional, fail-closed inference for the reviewed SpeechRank models.

Confidence means at least one finite numeric word probability in [0, 1], or
segment avg_logprob <= 0. Booleans, strings, nulls, language probability and
other segment metadata do not qualify. Missing placeholders therefore select
legacy. Invalid timestamp container shapes are rejected by feature extraction.
Scores are expected edit errors/second, never probabilities or gold labels.

A bundle is a trusted local deployment artifact, not an upload format: joblib
can execute code. The reviewed artifact hashes below are the trust anchor;
replacing both a model and its manifest cannot authorize new pickle bytes.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import math
import os
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import RLock
from typing import Any

from .features import FEATURE_NAMES, FEATURE_VERSION, extract_features

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = _ROOT / 'evals/runs/2026-10-17-librispeech-ml-ranking-v2/outputs'
BUNDLE_SCHEMA = 'speechrank-runtime-bundle-v1'
_PROVENANCE = {
    'run_id': '2026-10-17-librispeech-ml-ranking-v2',
    'evaluation_seal_sha256': 'a8258314c9fadc9841b6406ffb6824b40077b7919587a45fd1dd1cacec12f6c9',
    'protocol_sha256': 'ef9447be74c3ed02dc9dc21193f5b87626145ddfe8dba75cbda05e529a7b2b36',
    'config_sha256': 'cb728bb0474965274d382a91acc4ee0ce87e2acdd86540f7ec351c3d21979bba',
}
_SOURCE_HASHES = {
    'features.py': '22c2a0d25ed900f42ec417dbf091837e6621ab366be49d20a3f526ac25e86497',
    'models.py': '49a9903202559b191a66bfd85693979d3cc799230dc7053f3dbf551685a6b003',
}
_APPROVED = {
    'model-legacy.joblib': 'af35e81e35ea36899b22d19b06aeca0f2befbbcf4101672e41e1d119429f95dd',
    'model-legacy.json': '4d165febfe201d67661223214190b51c513a1631b64368212703240c0dc35af7',
    'model-confidence.joblib': '5c8a4f7170dedee5d6bb0e51908f11f53892ed2f78efe5de56f9a97767250434',
    'model-confidence.json': 'e848ca9f810b5044eba3bdf5dcdeafcd3570559b097d67ec15e7aecf439ea4cb',
}
SUPPORTED_DOMAIN = {
    'language': 'en',
    'model': {'name': 'base', 'repo_id': 'Systran/faster-whisper-base',
              'revision': 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'},
    'config': {'beam_size': 5, 'vad_filter': True, 'word_timestamps': True,
               'condition_on_previous_text': False, 'task': 'transcribe',
               'compute_type': 'int8', 'preprocessing': 'decode-original-audio-v1'},
    'validation_corpus': 'LibriSpeech',
    'other_english_corpora': 'exploratory; effectiveness not validated',
}
_LOCK = RLock()
_MODELS: dict[tuple[str, str], Any] = {}
_PREDICTIONS: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_CACHE_LIMIT = 4096


class RuntimeUnavailable(Exception):
    """Fixed, client-safe reason code; never includes paths or exception text."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle_manifest() -> dict[str, Any]:
    """Manifest for the reviewed release; portable copies need only five files."""
    return {'schema_version': BUNDLE_SCHEMA, 'feature_version': FEATURE_VERSION,
            'sklearn_version': '1.9.1', 'files': dict(_APPROVED),
            'source_hashes': dict(_SOURCE_HASHES), 'provenance': dict(_PROVENANCE),
            'supported_domain': SUPPORTED_DOMAIN}


def has_real_confidence(row: Mapping[str, Any]) -> bool:
    for kind, key, low, high in [('words', 'probability', 0, 1),
                                  ('segments', 'avg_logprob', None, 0)]:
        entries = row.get(kind, [])
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            value = entry.get(key) if isinstance(entry, Mapping) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            try:
                if math.isfinite(value) and (low is None or value >= low) and value <= high:
                    return True
            except (OverflowError, ValueError):
                continue
    return False


def _domain_reason(row: Mapping[str, Any]) -> str | None:
    if row.get('language') != 'en' or row.get('detected_language', 'en') != 'en':
        return 'unsupported_language'
    for section in ('model', 'config'):
        actual = row.get(section)
        if not isinstance(actual, Mapping):
            return 'asr_provenance_missing'
        for key, expected in SUPPORTED_DOMAIN[section].items():
            value = actual.get(key)
            if type(value) is not type(expected) or value != expected:
                return 'asr_provenance_mismatch'
    return None


def _snapshot(path: Path) -> tuple[dict, dict[str, bytes], str]:
    """Read and hash immutable byte snapshots on every request (no stat shortcut)."""
    try:
        manifest_path = path / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes()) if manifest_path.exists() else bundle_manifest()
        if manifest != bundle_manifest():
            raise RuntimeUnavailable('bundle_manifest_mismatch')
        for name, expected in _SOURCE_HASHES.items():
            if _hash((Path(__file__).parent / name).read_bytes()) != expected:
                raise RuntimeUnavailable('source_schema_mismatch')
        blobs = {name: (path / name).read_bytes() for name in _APPROVED}
        if any(_hash(data) != _APPROVED[name] for name, data in blobs.items()):
            raise RuntimeUnavailable('artifact_hash_mismatch')
        canonical = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
        return manifest, blobs, _hash(canonical)
    except RuntimeUnavailable:
        raise
    except FileNotFoundError:
        raise RuntimeUnavailable('bundle_missing') from None
    except Exception:  # noqa: BLE001 - fail closed for malformed local bundle metadata
        raise RuntimeUnavailable('bundle_invalid') from None


def _dependencies():
    try:
        joblib = importlib.import_module('joblib')
        sklearn = importlib.import_module('sklearn')
        importlib.import_module('threadpoolctl')
        return joblib, sklearn
    except Exception:  # noqa: BLE001 - optional native dependencies may raise non-import errors
        raise RuntimeUnavailable('optional_dependencies_unavailable') from None


def _load(arm: str, manifest: dict, blobs: dict[str, bytes], fingerprint: str):
    joblib, sklearn = _dependencies()
    if sklearn.__version__ != manifest['sklearn_version']:
        raise RuntimeUnavailable('sklearn_version_mismatch')
    key = (fingerprint, arm)
    if key in _MODELS:
        return _MODELS[key]
    try:
        card = json.loads(blobs[f'model-{arm}.json'])
        if (card['schema_version'] != 'speechrank-model-card-v1'
                or card['feature_version'] != FEATURE_VERSION or card['feature_arm'] != arm
                or card['feature_names'] != list(FEATURE_NAMES[arm])
                or card['sklearn_version'] != sklearn.__version__
                or card['sha256'] != _APPROVED[f'model-{arm}.joblib']
                or card['target'] != 'gold_edit_errors / audio_duration_s'
                or card['sample_weight'] != 'audio_duration_s'):
            raise RuntimeUnavailable('model_schema_mismatch')
        # Only deserialize the exact bytes already verified, avoiding check/load races.
        model = joblib.load(io.BytesIO(blobs[f'model-{arm}.joblib']))
        if (getattr(model, 'speechrank_feature_arm_', None) != arm
                or getattr(model, 'speechrank_feature_version_', None) != FEATURE_VERSION
                or getattr(model, 'speechrank_feature_names_', None) != list(FEATURE_NAMES[arm])
                or getattr(model, 'n_features_in_', None) != len(FEATURE_NAMES[arm])
                or getattr(model, 'speechrank_candidate_', None) != card['candidate']):
            raise RuntimeUnavailable('model_schema_mismatch')
        _MODELS[key] = model
        # Bound memory even if future approved releases are hot-swapped.
        while len(_MODELS) > 4:
            del _MODELS[next(iter(_MODELS))]
        return model
    except RuntimeUnavailable:
        raise
    except Exception:  # noqa: BLE001 - deserialize failures must preserve the QC fallback
        raise RuntimeUnavailable('model_load_failed') from None


def _result(arm: str | None, fingerprint: str | None = None, *, reason=None, density=None):
    return {'status': 'unavailable' if reason else 'ok', 'reason': reason,
            'arm': arm, 'density': density, 'units': 'expected_edit_errors_per_second',
            'model_id': f'speechrank-{arm}-{_APPROVED[f"model-{arm}.joblib"][:16]}' if arm else None,
            'feature_schema': FEATURE_VERSION, 'bundle_fingerprint': fingerprint,
            'applicability': 'English fixed-ASR; other corpora exploratory'}


def score_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Batch predict ASR provenance, preserving order; fixed safe failures enable QC.

    The only hashed/cached input is the allowlisted feature vector. Gold, IDs and
    local paths are neither read nor emitted. Cache keys bind the complete bundle.
    Failed ASR rows may score; the application owns their separate priority tier.
    """
    if not rows:
        return []
    with _LOCK:
        try:
            manifest, blobs, fingerprint = _snapshot(Path(os.environ.get('ANOTA_SPEECHRANK_BUNDLE', str(DEFAULT_BUNDLE))))
            # Dependency compatibility must hold even for a cached prediction.
            _, sklearn = _dependencies()
            if sklearn.__version__ != manifest['sklearn_version']:
                raise RuntimeUnavailable('sklearn_version_mismatch')
        except RuntimeUnavailable as exc:
            return [_result(None, reason=str(exc)) for _ in rows]
        results: list[Any] = [None] * len(rows)
        grouped: dict[str, dict[str, tuple[dict, list[int]]]] = {'legacy': {}, 'confidence': {}}
        for index, row in enumerate(rows):
            arm = None
            try:
                if not isinstance(row, Mapping):
                    raise TypeError('invalid row')
                arm = 'confidence' if has_real_confidence(row) else 'legacy'
                domain_reason = _domain_reason(row)
                if domain_reason:
                    results[index] = _result(arm, fingerprint, reason=domain_reason)
                    continue
                features = extract_features(row, arm=arm)
                content_hash = _hash(json.dumps(features, sort_keys=True, allow_nan=False).encode())
                cache_key = (fingerprint, arm, content_hash)
                if cache_key in _PREDICTIONS:
                    _PREDICTIONS.move_to_end(cache_key)
                    results[index] = _result(arm, fingerprint, density=_PREDICTIONS[cache_key])
                else:
                    grouped[arm].setdefault(content_hash, (features, []))[1].append(index)
            except Exception:  # noqa: BLE001 - isolate malformed provenance per row
                results[index] = _result(arm, fingerprint, reason='invalid_asr_features')
        for arm, pending in grouped.items():
            if not pending:
                continue
            try:
                model = _load(arm, manifest, blobs, fingerprint)
                from .models import predict_density
                values = predict_density(model, [entry[0] for entry in pending.values()], arm)
                if len(values) != len(pending) or any(not math.isfinite(v) or v < 0 for v in values):
                    raise RuntimeUnavailable('invalid_prediction')
                for (content_hash, (_, indices)), value in zip(pending.items(), values):
                    _PREDICTIONS[(fingerprint, arm, content_hash)] = value
                    for index in indices:
                        results[index] = _result(arm, fingerprint, density=value)
                while len(_PREDICTIONS) > _CACHE_LIMIT:
                    _PREDICTIONS.popitem(last=False)
            except Exception as exc:  # noqa: BLE001 - prediction failures must preserve the QC fallback
                reason = str(exc) if isinstance(exc, RuntimeUnavailable) else 'prediction_failed'
                for _, indices in pending.values():
                    for index in indices:
                        results[index] = _result(arm, fingerprint, reason=reason)
        return results
