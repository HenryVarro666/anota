"""Audit the experiment's denominators and controlled assertions, not VAD quality."""

import json

import pytest

from scripts.evaluate_audio_qc import (
    FROZEN_CONFIG,
    controlled_eligibility,
    detector_transcript,
    evaluate_expectation,
    read_manifest,
    summarize,
)


def _report(issues=(), **extra):
    return {"duration_s": 2.0, "status": "complete", "vad": {"status": "ok", "intervals": [{"start": .1, "end": 1.8}]},
            "coverage": {"status": "ok", "basis": "words"},
            "issues": [{"code": code} for code in issues], **extra}


def _record(case_id, condition, parent, report=None, **extra):
    return {"case_id": case_id, "condition": condition, "parent_item_id": parent,
            "status": "complete" if report else "failed", "report": report,
            "expectation": evaluate_expectation(condition, report), **extra}


def test_missing_and_failed_cases_remain_in_planned_denominators():
    records = [
        _record("a:original", "original", "a", _report(["low_energy", "low_energy"]),
                empty_cache_ms=100, cache_replay_ms=2, cache_semantically_equal=True),
        _record("b:original", "original", "b", empty_cache_ms=3),
        _record("a:removed", "transcript_removed", "a", eligible=True),
    ]
    summary = summarize(records, ["a", "b"])
    original = summary["conditions"]["original"]
    assert summary["parent_recordings"] == 2
    assert summary["planned_cases"] == 12  # 2 parents x 3 + 6 deterministic signals
    assert original["planned_cases"] == 2
    assert original["statuses"] == {"complete": 1, "failed": 1}
    assert original["issue_count_by_code"] == {"low_energy": 2}
    assert original["flagged_item_count_by_code"] == {"low_energy": 1}
    assert original["empty_cache"]["calls"] == 2
    assert original["empty_cache"]["sum_ms"] == 103
    assert original["cache_replay"]["calls"] == 1
    assert original["cache_replay"]["sum_ms"] == 2
    removed = summary["conditions"]["transcript_removed"]
    assert removed["planned_cases"] == 2
    assert removed["missing_cases"] == 1
    assert removed["eligible_controlled_cases"] == 1
    assert removed["expectations"] == {"failed": 1}


def test_controlled_failure_is_not_hidden_and_original_flags_are_not_accuracy():
    assert evaluate_expectation("original", _report(["low_energy"]))["status"] == "observational"
    assert evaluate_expectation("transcript_removed", _report(), True)["status"] == "failed"
    assert evaluate_expectation("transcript_removed", _report(["speech_without_transcript"]), True)["status"] == "passed"
    assert evaluate_expectation("audio_silenced", None, True)["status"] == "failed"
    assert evaluate_expectation("transcript_removed", _report(), False)["status"] == "ineligible"


def test_no_reference_or_human_decision_reaches_detector():
    row = {"asr_text": "ASR", "segments": [], "words": [],
           "reference": "SECRET", "reference_text": "SECRET", "final_text": "SECRET"}
    assert detector_transcript(row) == {"asr_text": "ASR", "segments": [], "words": []}


def test_controlled_eligibility_requires_real_parent_vad_or_valid_text_intervals():
    config = FROZEN_CONFIG
    transcript = {"words": [{"start": .1, "end": .8, "w": "hello"}], "segments": []}
    assert controlled_eligibility("transcript_removed", _report(), transcript, config)[0]
    unavailable = _report(vad={"status": "unavailable", "intervals": []}, coverage={"status": "unavailable", "basis": "none"})
    assert not controlled_eligibility("transcript_removed", unavailable, transcript, config)[0]
    assert not controlled_eligibility("transcript_removed", None, transcript, config)[0]
    assert not controlled_eligibility("audio_silenced", unavailable, transcript, config)[0]
    assert controlled_eligibility("audio_silenced", _report(), transcript, config)[0]
    assert not controlled_eligibility("audio_silenced", _report(), {"asr_text": "untimed"}, config)[0]


def test_invalid_words_fall_back_but_disjoint_short_words_do_not_become_eligible():
    transcript = {"words": [{"start": 0, "end": 0, "w": "invalid"}],
                  "segments": [{"start": .1, "end": 1, "text": "valid"}]}
    assert controlled_eligibility("audio_silenced", _report(coverage={"status": "ok", "basis": "segments"}), transcript, FROZEN_CONFIG)[0]
    transcript = {"words": [{"start": .1, "end": .3, "w": "a"}, {"start": .4, "end": .6, "w": "b"}],
                  "segments": [{"start": .1, "end": 1, "text": "must not override usable words"}]}
    assert not controlled_eligibility("audio_silenced", _report(), transcript, FROZEN_CONFIG)[0]


@pytest.mark.parametrize("parents,records,error", [
    (["a", "a"], [], "Duplicate parent"),
    (["a"], [_record("x", "original", "b")], "outside manifest"),
    (["a"], [_record("x", "original", "a"), _record("x", "original", "a")], "Duplicate case"),
])
def test_unaccountable_identity_is_rejected(parents, records, error):
    with pytest.raises(ValueError, match=error):
        summarize(records, parents)


def test_manifest_rejects_empty_and_duplicate_denominators(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        read_manifest(path)
    path.write_text(json.dumps({"item_id": "x"}) + "\n" + json.dumps({"item_id": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        read_manifest(path)


def test_antiphase_check_requires_actual_nonzero_rms_and_no_low_level():
    assert evaluate_expectation("antiphase", _report(metrics={"rms_dbfs": -17}))["status"] == "passed"
    assert evaluate_expectation("antiphase", _report(metrics={"rms_dbfs": None}))["status"] == "failed"
    assert evaluate_expectation("antiphase", _report(["low_level"], metrics={"rms_dbfs": -17}))["status"] == "failed"
