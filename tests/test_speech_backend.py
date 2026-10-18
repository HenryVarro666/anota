"""Speech data integrity and lifecycle tests; generated audio is a unit fixture."""
import hashlib
import json
import sqlite3
import wave

import pytest
from pydantic import ValidationError

from app import export, speech, tasks
from app.db import DDL, Database
from app.models import AnnotationPayload, SpeechSubmitRequest


def manifest(tmp_path, **changes):
    audio = tmp_path / "fixture.wav"
    if not audio.exists():
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\x00\x00" * 16000)
    row = {"schema_version": "speech-v1", "item_id": "zh_001", "language": "zh",
           "audio_path": audio.name, "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
           "corpus": "unit-test", "corpus_revision": "fixed-revision", "split": "train",
           "source_id": "001", "reference_id": "protected-001", "license": "unit-fixture",
           "status": "succeeded", "asr_text": "你好", "segments": [{"start": 0, "end": 1, "text": "你好"}],
           "audio_duration_s": 1, "sample_rate": 16000, "channels": 1,
           "model": {"name": "unit-fixture", "revision": "v1", "device": "cpu", "compute_type": "int8"},
           "config": {"beam_size": 1}, "cache_key": "test-cache", "run_id": "run-001",
           "processing_ms": 10, "cache_hit": False, "error": None}
    row.update(changes)
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, row


def prepared(tmp_path):
    db = Database(":memory:")
    path, row = manifest(tmp_path)
    result = speech.import_speech_manifest(db, path, "speech")
    return db, result["batch_id"], row


def request(claim, text="你好。", **changes):
    data = {"assignment_id": claim["assignment_id"], "annotator": "tester",
            "corrected_transcript": text, "elapsed_ms": 1000, "active_ms": 800}
    data.update(changes)
    return SpeechSubmitRequest(**data)


def test_migrates_existing_translation_database_and_reopens(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.execute("INSERT INTO batches(name) VALUES('legacy')")
    conn.execute("INSERT INTO tasks(id,batch_id,source,hypothesis) VALUES('old',1,'s','h')")
    conn.commit()
    conn.close()
    for _ in range(2):
        db = Database(path)
        assert db.one("SELECT task_type FROM tasks WHERE id='old'")["task_type"] == "translation"
        assert db.one("SELECT COUNT(*) n FROM schema_migrations")["n"] == 2
        assert tasks.claim(db, "legacy")["hypothesis"] == "h"
        db.conn.close()


def test_import_idempotent_but_changed_asr_and_configuration_are_new_tasks(tmp_path):
    db, bid, _row = prepared(tmp_path)
    path, _ = manifest(tmp_path, run_id="new-run", cache_hit=True, processing_ms=0)
    again = speech.import_speech_manifest(db, path, "speech")
    assert again == {"batch_id": bid, "n": 0, "skipped": 1, "failed": 0, "invalid": 0, "errors": []}
    path, _ = manifest(tmp_path, config={"beam_size": 5})
    assert speech.import_speech_manifest(db, path, "speech")["n"] == 1
    path, _ = manifest(tmp_path, asr_text="您好")
    assert speech.import_speech_manifest(db, path, "speech")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM tasks")["n"] == 3
    assert db.one("SELECT COUNT(*) n FROM audio_assets")["n"] == 1
    assert db.one("SELECT hypothesis FROM tasks ORDER BY rowid LIMIT 1")["hypothesis"] == "你好"


def test_failed_invalid_rows_are_explicit_and_not_tasks(tmp_path):
    db = Database(":memory:")
    path, row = manifest(tmp_path)
    failed = {**row, "status": "failed", "error": "decode failed"}
    invalid = {**row, "audio_sha256": "0" * 64}
    path.write_text("\n".join([json.dumps(row), json.dumps(failed), json.dumps(invalid), "broken-json"]),
                    encoding="utf-8")
    result = speech.import_speech_manifest(db, path, "mixed")
    assert (result["n"], result["failed"], result["invalid"]) == (1, 1, 2)
    assert len(result["errors"]) == 3
    assert db.one("SELECT COUNT(*) n FROM tasks")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM speech_annotations")["n"] == 0


def test_claim_whitelist_never_exposes_reference_or_arbitrary_metadata(tmp_path):
    db = Database(":memory:")
    path, _ = manifest(tmp_path, reference="SECRET_REFERENCE", reference_text="SECRET_REFERENCE",
                       metadata={"answer": "SECRET_REFERENCE"},
                       segments=[{"start": 0, "end": 1, "text": "你好", "reference": "SECRET_REFERENCE"}])
    speech.import_speech_manifest(db, path, "speech")
    claim = tasks.claim(db, "tester")
    assert claim["task_type"] == "speech"
    assert "SECRET_REFERENCE" not in json.dumps(claim)
    assert "reference" not in claim and "metadata" not in claim
    assert "audio_path" not in json.dumps(claim)
    assert claim["speech"]["audio_url"].startswith("/api/audio/audio-")
    assert claim["speech"]["segments"] == [{"start": 0.0, "end": 1.0, "text": "你好"}]


def test_registered_audio_cannot_serve_arbitrary_or_replaced_files(tmp_path):
    db, _, _ = prepared(tmp_path)
    audio_id = tasks.claim(db, "tester")["speech"]["audio_id"]
    asset = speech.audio_asset(db, audio_id)
    assert asset["path"] == str((tmp_path / "fixture.wav").resolve())
    with pytest.raises(LookupError):
        speech.audio_asset(db, "../../private")
    (tmp_path / "fixture.wav").write_bytes(b"not audio")
    with pytest.raises(ValueError):
        speech.audio_asset(db, audio_id)


def test_submit_undo_resubmit_retains_history_and_exports_only_active_final(tmp_path):
    db, bid, _ = prepared(tmp_path)
    claim = tasks.claim(db, "tester")
    speech.submit_speech(db, "tester", claim["assignment_id"], request(claim))
    first = export.build_snapshot(db, {"batch_id": bid})["items"][0]
    assert first["final_label"]["corrected_transcript"] == "你好。"
    assert first["task"]["asr_text"] == "你好"
    assert first["task"]["provenance"]["model"]["revision"] == "v1"
    undone = tasks.undo(db, "tester")
    assert undone["speech"]["latest_annotation"]["corrected_transcript"] == "你好。"
    reopened = export.build_snapshot(db)["items"][0]
    assert len(reopened["annotations"]) == 1
    assert reopened["final_label"] is None and reopened["final_annotations"] == []
    speech.submit_speech(db, "tester", claim["assignment_id"], request(claim, text="您好。"))
    final = export.build_snapshot(db)["items"][0]
    assert len(final["annotations"]) == 2
    assert final["final_label"]["corrected_transcript"] == "您好。"
    assert final["task"]["asr_text"] == "你好"
    one = export.export_snapshot(db, tmp_path / "exports")
    two = export.export_snapshot(db, tmp_path / "exports")
    assert one["sha256"] == two["sha256"]


def test_empty_transcript_is_intentional_and_type_ownership_are_checked(tmp_path):
    db, _bid, _ = prepared(tmp_path)
    claim = tasks.claim(db, "tester")
    with pytest.raises(LookupError):
        speech.submit_speech(db, "other", claim["assignment_id"], request(claim, annotator="other"))
    translation = AnnotationPayload(error_types=["no_error"], worst_severity="neutral", adequacy=5, fluency=5)
    with pytest.raises(ValueError, match="speech"):
        tasks.submit(db, "tester", claim["assignment_id"], translation)
    speech.submit_speech(db, "tester", claim["assignment_id"], request(claim, text=""))
    with pytest.raises(ValueError, match="not open"):
        speech.submit_speech(db, "tester", claim["assignment_id"], request(claim))
    translation_batch = db.execute("INSERT INTO batches(name) VALUES('translation')")
    db.execute("INSERT INTO tasks(id,batch_id,source,hypothesis) VALUES('t',?,'s','h')", (translation_batch,))
    other = tasks.claim(db, "tester", translation_batch)
    with pytest.raises(ValueError, match="translation"):
        speech.submit_speech(db, "tester", other["assignment_id"], request(other))


def test_uncertainty_and_time_validation(tmp_path):
    db, _, _ = prepared(tmp_path)
    claim = tasks.claim(db, "tester")
    with pytest.raises(ValidationError):
        request(claim, uncertain_spans=[{"start": 1, "end": 0, "reason": "inaudible"}])
    with pytest.raises(ValidationError):
        request(claim, active_ms=1001)
    with pytest.raises(ValueError, match="duration"):
        speech.submit_speech(db, "tester", claim["assignment_id"], request(
            claim, uncertain_spans=[{"start": .5, "end": 1.5, "reason": "inaudible"}]))
    assert db.one("SELECT COUNT(*) n FROM speech_annotations")["n"] == 0


def test_submit_is_atomic_if_audit_insert_fails(tmp_path):
    db, _, _ = prepared(tmp_path)
    claim = tasks.claim(db, "tester")
    db.execute("CREATE TRIGGER reject_speech_audit BEFORE INSERT ON audit_log "
               "WHEN NEW.action='submit_speech' BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError):
        speech.submit_speech(db, "tester", claim["assignment_id"], request(claim))
    assert db.one("SELECT COUNT(*) n FROM speech_annotations")["n"] == 0
    assert db.one("SELECT status FROM assignments WHERE id=?", (claim["assignment_id"],))["status"] == "assigned"


def test_cached_import_retains_original_inference_provenance_without_exposing_it(tmp_path):
    db = Database(":memory:")
    path, _row = manifest(tmp_path, run_id="cache-run", cache_hit=True, processing_ms=2,
                         inference_run_id="original-inference-run", inference_processing_ms=300,
                         words=[{"w": "你好", "start": 0, "end": 1}],
                         model={"name": "base", "revision": "a" * 40, "device": "cpu",
                                "compute_type": "int8", "repo_id": "Systran/faster-whisper-base"})
    assert speech.import_speech_manifest(db, path, "speech")["n"] == 1
    claim = tasks.claim(db, "tester")
    assert "inference_run_id" not in json.dumps(claim)
    provenance = export.build_snapshot(db)["items"][0]["task"]["provenance"]
    assert provenance["inference_run_id"] == "original-inference-run"
    assert provenance["inference_processing_ms"] == 300
    assert provenance["processing_ms"] == 2
    assert provenance["words"][0]["w"] == "你好"
    assert provenance["model"]["repo_id"] == "Systran/faster-whisper-base"


def test_expired_undone_lease_recovers_own_revision_and_resubmits_with_new_assignment(tmp_path):
    db, bid, _ = prepared(tmp_path)
    original = tasks.claim(db, "tester")
    speech.submit_speech(db, "tester", original["assignment_id"], request(original, text="第一次修正"))
    tasks.undo(db, "tester")
    db.execute("UPDATE assignments SET lease_expires_at=datetime('now','-1 minute') WHERE id=?",
               (original["assignment_id"],))
    resumed = tasks.claim(db, "tester", bid)
    assert resumed["assignment_id"] != original["assignment_id"]
    assert resumed["speech"]["latest_annotation"]["corrected_transcript"] == "第一次修正"
    pending = export.build_snapshot(db)["items"][0]
    assert len(pending["annotations"]) == 1 and pending["final_label"] is None
    speech.submit_speech(db, "tester", resumed["assignment_id"], request(resumed, text="第二次修正"))
    final = export.build_snapshot(db)["items"][0]
    assert len(final["annotations"]) == 2
    assert final["final_label"]["corrected_transcript"] == "第二次修正"
    assert final["final_label"]["assignment_id"] == resumed["assignment_id"]


def test_reassigned_lease_never_exposes_another_annotators_revision(tmp_path):
    db, bid, _ = prepared(tmp_path)
    original = tasks.claim(db, "tester")
    speech.submit_speech(db, "tester", original["assignment_id"], request(original, text="PRIVATE_CORRECTION"))
    tasks.undo(db, "tester")
    db.execute("UPDATE assignments SET lease_expires_at=datetime('now','-1 minute') WHERE id=?",
               (original["assignment_id"],))
    other = tasks.claim(db, "independent-reviewer", bid)
    assert other["speech"]["latest_annotation"] is None
    assert "PRIVATE_CORRECTION" not in json.dumps(other)
