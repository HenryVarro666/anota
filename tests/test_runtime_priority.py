"""The packaged speech queue must not require offline evaluation scripts."""

import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def test_queue_works_without_scripts_and_evaluation_reuses_runtime_priority():
    # Use a fresh interpreter: other tests may already have imported scripts.
    script = dedent('''
        import importlib.abc
        import json
        import sys

        class NoScripts(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "scripts" or fullname.startswith("scripts."):
                    raise ModuleNotFoundError("offline scripts are absent from the runtime")

        blocker = NoScripts()
        sys.meta_path.insert(0, blocker)
        from app import speech_queue
        from speechqc import priority

        class Database:
            def query(self, _sql):
                return [{"task_id": "speech-1", "source": "sample.wav", "provenance": json.dumps({
                    "item_id": "sample-1", "audio_duration_s": 2,
                    "audio_analysis": {"status": "complete", "coverage": {"status": "ok"},
                                       "issues": [{"start": 0, "end": 1, "priority": "high"}]},
                })}]

        candidates = [{"id": "speech-1", "task_type": "speech", "show_suggestions": True}]
        ordered, records = speech_queue.prepare(Database(), candidates, sort="qc")
        assert ordered == candidates
        assert records["speech-1"]["ranking"]["qc_score"] == 1.5
        assert not any(name == "scripts" or name.startswith("scripts.") for name in sys.modules)
        assert speech_queue.priority_score is priority.priority_score
        assert speech_queue.TIE_SALT is priority.TIE_SALT

        sys.meta_path.remove(blocker)
        from scripts import qc_ranking_metrics
        assert qc_ranking_metrics.priority_score is priority.priority_score
        assert qc_ranking_metrics.PRIORITIES is priority.PRIORITIES
        assert qc_ranking_metrics.TIE_SALT is priority.TIE_SALT
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
