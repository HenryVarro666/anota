"""Synthetic feature, confidence-provenance and train-only preprocessing checks."""

import json
import math
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from speechrank.confidence_asr import (
    ADAPTER_VERSION,
    ConfidenceAdapter,
    transcribe_with_confidence,
)
from speechrank.features import (
    FEATURE_NAMES,
    audit_confidence_availability,
    extract_features,
)
from speechrank.models import (
    build_estimator,
    fit_model,
    get_candidates,
    model_card,
    predict_density,
)


def asr(**changes):
    result = {
        "status": "succeeded", "audio_duration_s": 4.0, "asr_text": "one two two",
        "words": [{"w": "one", "start": 0.5, "end": 1.0},
                  {"w": "two", "start": 1.5, "end": 2.0},
                  {"w": "two", "start": 2.0, "end": 2.5}],
        "segments": [{"text": "one two two", "start": 0.5, "end": 2.5}],
        "language_probability": 1.0,
    }
    result.update(changes)
    return result


def qc():
    return {
        "status": "complete",
        "metrics": {"peak_dbfs": -2, "rms_dbfs": -20, "near_full_scale_ratio": 0.0,
                    "low_energy_ratio": 0.1, "dc_offset": 0.01},
        "vad": {"status": "ok", "intervals": [{"start": 0.5, "end": 3.0}]},
        "coverage": {"status": "ok", "basis": "words", "speech_s": 2.5, "covered_s": 2,
                     "uncovered_s": 0.5, "uncovered_ratio": 0.2, "text_outside_s": 0.0},
        "issues": [{"code": "low_level", "start": 0.0, "end": 4.0, "priority": "low"},
                   {"code": "speech_without_transcript", "start": 2.5, "end": 3.0, "priority": "high"}],
    }


class NoGold(dict):
    def get(self, key, default=None):
        if key in {"item_id", "speaker_id", "source_split", "audio_path", "reference_text", "errors", "reference_units", "wer"}:
            raise AssertionError(f"forbidden feature access: {key}")
        return super().get(key, default)

    def __getitem__(self, key):
        if key in {"item_id", "speaker_id", "source_split", "audio_path", "reference_text", "errors", "reference_units", "wer"}:
            raise AssertionError(f"forbidden feature access: {key}")
        return super().__getitem__(key)


def test_fixed_allowlist_is_gold_identity_and_vocabulary_independent():
    original = asr(item_id="a", speaker_id="someone", source_split="test", reference_text="gold", errors=4)
    changed = deepcopy(original)
    changed.update(item_id="b", speaker_id="another", source_split="train", reference_text="different", errors=999)
    for arm in FEATURE_NAMES:
        features = extract_features(NoGold(original), qc(), arm)
        assert tuple(features) == FEATURE_NAMES[arm]
        assert extract_features(NoGold(changed), qc(), arm) == features
        # Changing token identities while preserving aggregate structure leaves features unchanged.
        assert extract_features(asr(asr_text="red cat cat"), qc(), arm) == features
    assert audit_confidence_availability([NoGold(original)])["rows"] == 1


def test_timing_union_overlap_zero_invalid_outside_and_order_are_distinct():
    rows = asr(words=[{"start": 0, "end": 2}, {"start": 1, "end": 3},
                     {"start": 3, "end": 3}, {"start": 3.5, "end": 5},
                     {"start": -1, "end": 0}, {"start": 0.5, "end": 1}])
    features = extract_features(rows)
    assert features["word_invalid_fraction"] == pytest.approx(1 / 6)
    assert features["word_zero_fraction"] == pytest.approx(1 / 6)
    assert features["word_outside_fraction"] == pytest.approx(1 / 6)
    assert features["word_out_of_order_fraction"] == 1 / 5
    assert features["word_union_fraction"] == 3.5 / 4
    assert features["word_overlap_fraction"] == pytest.approx(1.5 / 5)
    assert features["word_internal_gap_max_fraction"] == 0.5 / 4


def test_qc_priority_does_not_double_count_overlap_and_missing_is_explicit():
    report = qc()
    report["issues"].append(dict(report["issues"][1]))
    features = extract_features(asr(), report)
    assert features["qc_priority_score"] == 1.25
    assert features["qc_speech_without_transcript_count_per_s"] == 0.5
    assert features["qc_speech_without_transcript_union_fraction"] == 0.125
    assert features["qc_vad_speech_fraction"] == 0.625
    absent = extract_features(asr())
    assert absent["qc_missing"] == 1.0 and absent["qc_priority_score"] is None
    assert absent["qc_vad_speech_fraction"] is None
    assert features["qc_coverage_uncovered_ratio"] == 0.2


def test_old_confidence_missing_is_not_zero_probability_and_language_not_a_feature():
    features = extract_features(asr(), arm="confidence")
    assert features["word_probability_mean"] is None
    assert features["word_probability_missing"] == 1.0
    assert features["word_probability_available_fraction"] == 0.0
    assert features["segment_avg_logprob_mean"] is None
    assert "language_probability" not in features
    audit = audit_confidence_availability([asr(), asr()])
    assert audit["sources"]["word_probability"]["valid"] == 0
    assert audit["sources"]["word_probability"]["missing"] == 6
    assert audit["sources"]["language_probability"]["constant"] is True
    assert audit["sources"]["language_probability"]["used_as_model_feature"] is False


def test_real_zero_confidence_partial_missing_and_invalid_values_are_distinguished():
    value = asr()
    value["words"][0]["probability"] = 0.0
    value["words"][1]["probability"] = 0.9
    value["words"][2]["probability"] = math.nan
    value["segments"][0].update(avg_logprob=-0.4, no_speech_prob=0.2, compression_ratio=1.2, temperature=0.0)
    features = extract_features(value, arm="confidence")
    assert features["word_probability_mean"] == 0.45
    assert features["word_probability_available_fraction"] == 2 / 3
    assert features["word_probability_missing"] == 0.0
    assert features["word_probability_below_half_fraction"] == 0.5
    assert features["segment_temperature_mean"] == 0.0
    audit = audit_confidence_availability([value])
    assert audit["sources"]["word_probability"]["invalid"] == 1
    assert audit["sources"]["word_probability"]["present"] == 3
    json.dumps(features, allow_nan=False)
    json.dumps(audit, allow_nan=False)


def test_empty_transcript_and_silence_are_finite_with_missing_db_values():
    value = asr(asr_text="", words=[], segments=[])
    report = qc()
    report["metrics"].update(peak_dbfs=None, rms_dbfs=None)
    features = extract_features(value, report, "confidence")
    assert features["text_empty"] == 1.0
    assert features["word_duration_mean_s"] is None
    assert features["word_leading_gap_fraction"] == 1.0
    assert features["qc_rms_dbfs"] is None
    json.dumps(features, allow_nan=False)


@pytest.mark.parametrize("duration", [0, -1, math.nan, math.inf, True, None])
def test_invalid_audio_duration_rejected(duration):
    with pytest.raises(ValueError):
        extract_features(asr(audio_duration_s=duration))


def _engine():
    words = [SimpleNamespace(word=" hello ", start=0.1234, end=0.9876, probability=0.123456789)]
    segments = [SimpleNamespace(text=" hello ", start=0.1234, end=0.9876, words=words,
                                avg_logprob=-0.4321, no_speech_prob=0.0123, compression_ratio=1.2345, temperature=0.0),
                SimpleNamespace(text=" 中文 ", start=1.0, end=2.0, words=None)]
    info = SimpleNamespace(language="en", language_probability=1.0, duration=2.123456)

    class Engine:
        def transcribe(self, *args, **kwargs):
            self.call = (args, kwargs)
            return iter(segments), info
    return Engine()


def test_confidence_adapter_preserves_exact_decode_text_and_timestamp_conventions(monkeypatch):
    from speechprep.vendor import asr_whisper
    engine = _engine()
    monkeypatch.setattr(asr_whisper, "_get_model", lambda *args, **kwargs: engine)
    old = asr_whisper.transcribe("sample.wav", model_size="frozen", device="cpu", compute_type="int8", language="en")
    old_call = deepcopy(engine.call)
    result = transcribe_with_confidence(engine, "sample.wav", language="en", model_name="frozen")
    assert engine.call == old_call
    stripped = deepcopy(result)
    for segment in stripped["segments"]:
        for key in ("avg_logprob", "no_speech_prob", "compression_ratio", "temperature"):
            del segment[key]
    for word in stripped["words"]:
        del word["probability"]
    assert stripped == old
    assert result["words"][0]["probability"] == 0.123456789
    assert result["segments"][1]["avg_logprob"] is None
    assert result["text"] == "hello 中文"
    json.dumps(result, allow_nan=False)


def test_confidence_adapter_requires_new_cache_namespace_and_uses_frozen_engine(monkeypatch):
    from speechprep import asr as prep_asr
    from speechprep.asr import ModelSpec
    from speechprep.vendor import asr_whisper
    model = ModelSpec("frozen", "sha256:fixture", local_path="frozen-model")
    config = {"adapter_version": ADAPTER_VERSION, "device": "cpu", "compute_type": "int8", "cpu_threads": 4,
              "vad_filter": False, "beam_size": 3, "condition_on_previous_text": False}
    with pytest.raises(ValueError, match="adapter_version"):
        ConfidenceAdapter(model, dict(config, adapter_version="old"))
    monkeypatch.setattr(prep_asr, "check_decoder_compatibility", lambda: None)
    engine = _engine()
    calls = []
    monkeypatch.setattr(asr_whisper, "_get_model", lambda *args: calls.append(args) or engine)
    adapter = ConfidenceAdapter(model, config)
    adapter.transcribe("fixture.wav", "en")
    assert calls == [("frozen-model", "cpu", "int8", 4)]
    assert engine.call[1] == {"word_timestamps": True, "language": "en", "vad_filter": False,
                              "beam_size": 3, "condition_on_previous_text": False}


def test_package_import_does_not_load_ml_or_audio_dependencies():
    code = "import sys; import speechrank; assert not any(name in sys.modules for name in ['numpy','sklearn','speechrank.models','speechrank.features','faster_whisper'])"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_seven_candidates_are_frozen_and_preprocessing_is_unfitted():
    pytest.importorskip("sklearn")
    candidates = get_candidates()
    assert len(candidates) == len({candidate["candidate_id"] for candidate in candidates}) == 7
    for candidate in candidates:
        estimator = build_estimator(candidate)
        assert not hasattr(estimator.named_steps["imputer"], "statistics_")
        assert not hasattr(estimator.named_steps["scale"], "mean_")
        if candidate["kind"] == "hgb_poisson":
            assert estimator.named_steps["model"].loss == "poisson"
            assert estimator.named_steps["model"].early_stopping is False
            assert estimator.named_steps["model"].random_state == 20261017
    with pytest.raises(ValueError):
        build_estimator({"candidate_id": "unregistered", "kind": "poisson", "params": {"alpha": 0}})


def test_training_uses_duration_weighted_density_and_predict_does_not_refit():
    pytest.importorskip("sklearn")
    features = [extract_features(asr()) for _ in range(4)]
    model = fit_model(features, [1, 3, 5, 7], [1, 3, 5, 7], "poisson-alpha-1")
    assert predict_density(model, features) == pytest.approx([1.0] * 4)
    before_imputer = model.named_steps["imputer"].statistics_.copy()
    before_scaler = model.named_steps["scale"].mean_.copy()
    shifted = deepcopy(features)
    shifted[0]["log_duration_s"] = 9999.0
    predict_density(model, shifted)
    assert model.named_steps["imputer"].statistics_.tolist() == pytest.approx(before_imputer.tolist())
    assert model.named_steps["scale"].mean_.tolist() == pytest.approx(before_scaler.tolist())
    card = model_card(model)
    assert card["training_errors"] == 16 and card["training_audio_s"] == 16
    json.dumps(card, allow_nan=False)
    with pytest.raises(ValueError, match="arm/version"):
        predict_density(model, [extract_features(asr(), arm="confidence")], arm="confidence")


def test_duration_weighting_estimates_total_errors_over_total_seconds():
    pytest.importorskip("sklearn")
    identical = [extract_features(asr())] * 2
    model = fit_model(identical, [0, 10], [1, 9], "poisson-alpha-1")
    # The duration-weighted density is 10/10=1, not mean([0/1,10/9]).
    assert predict_density(model, identical) == pytest.approx([1, 1], rel=1e-3)


def test_confidence_pipeline_preserves_all_missing_features_and_hgb_is_deterministic():
    pytest.importorskip("sklearn")
    rows = [extract_features(asr(audio_duration_s=2 + index / 10), arm="confidence") for index in range(40)]
    errors = [index % 4 for index in range(40)]
    durations = [2 + index / 10 for index in range(40)]
    first = fit_model(rows, errors, durations, "hgb-leaves-3-l2-1", arm="confidence")
    second = fit_model(rows, errors, durations, "hgb-leaves-3-l2-1", arm="confidence")
    assert predict_density(first, rows, arm="confidence") == predict_density(second, rows, arm="confidence")
    assert all(value >= 0 for value in predict_density(first, rows, arm="confidence"))


@pytest.mark.parametrize("errors,durations", [([0], [1]), ([-1], [1]), ([True], [1]), ([1], [0]), ([1], [math.nan])])
def test_invalid_training_counts_or_durations_rejected(errors, durations):
    with pytest.raises(ValueError):
        fit_model([extract_features(asr())], errors, durations, "poisson-alpha-1")


def test_extra_gold_feature_is_rejected_by_training_allowlist():
    pytest.importorskip("sklearn")
    features = extract_features(asr())
    features["errors"] = 2
    with pytest.raises(ValueError, match="allowlist"):
        fit_model([features], [2], [4], "poisson-alpha-1")
