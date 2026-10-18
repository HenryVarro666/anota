"""Cross-platform process locking and durable per-item/cache state."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .manifest import canonical_json, digest_json


class RunLockedError(RuntimeError):
    pass


@contextmanager
def exclusive_run(path: str | Path):
    """OS releases this byte/flock lock even when the worker is hard-killed."""
    lock_path = Path(str(Path(path).resolve()) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if lock_path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RunLockedError(f"another speechprep process holds {lock_path}") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class State:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                item_id TEXT NOT NULL, cache_key TEXT NOT NULL,
                status TEXT NOT NULL, run_id TEXT NOT NULL,
                error TEXT, result_json TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(item_id, cache_key)
            );
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL,
                result_sha256 TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def recover(self) -> int:
        with self.db:
            cur = self.db.execute("UPDATE items SET status='interrupted', error='previous worker interrupted', "
                                  "updated_at=CURRENT_TIMESTAMP WHERE status='running'")
        return cur.rowcount

    def cached(self, key: str) -> dict | None:
        row = self.db.execute("SELECT * FROM cache WHERE cache_key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            result = json.loads(row["result_json"])
            if digest_json(result) != row["result_sha256"]:
                raise ValueError("cache checksum mismatch")
            if result.get("cache_key") != key or result.get("status") != "succeeded":
                raise ValueError("invalid cache identity")
            return result
        except (ValueError, TypeError, AttributeError):
            with self.db:
                self.db.execute("DELETE FROM cache WHERE cache_key=?", (key,))
            return None

    def mark(self, item_id: str, key: str, status: str, run_id: str,
             result: dict | None = None, error: str | None = None):
        encoded = canonical_json(result) if result is not None else None
        with self.db:
            self.db.execute("""INSERT INTO items(item_id,cache_key,status,run_id,error,result_json)
                VALUES(?,?,?,?,?,?) ON CONFLICT(item_id,cache_key) DO UPDATE SET
                status=excluded.status,run_id=excluded.run_id,error=excluded.error,
                result_json=excluded.result_json,updated_at=CURRENT_TIMESTAMP""",
                (item_id, key, status, run_id, error, encoded))
            if status == "succeeded" and result is not None:
                self.db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)",
                                (key, encoded, digest_json(result)))
