from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


class TaskStore:
    """
    Durable record of delegated tasks so a job survives an Alfred
    restart. Thread-safe (single lock).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def add(self, task_id: str, goal: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO tasks "
                "(id, goal, status, summary, created_at, updated_at) "
                "VALUES (?, ?, 'queued', '', ?, ?)",
                (task_id, goal, now, now),
            )
            self._conn.commit()

    def set_status(self, task_id: str, status: str, summary: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status = ?, summary = ?, updated_at = ? "
                "WHERE id = ?",
                (status, summary, _now(), task_id),
            )
            self._conn.commit()

    def unfinished(self, max_age_hours: float = 6.0) -> list[dict[str, str]]:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_hours * 3600
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, goal, status, created_at FROM tasks "
                "WHERE status IN ('queued', 'running') ORDER BY created_at ASC"
            ).fetchall()

        out = []
        for r in rows:
            try:
                ts = datetime.fromisoformat(r["created_at"]).timestamp()
            except ValueError:
                ts = cutoff + 1
            if ts >= cutoff:
                out.append(dict(r))
        return out

    def recent(self, limit: int = 10) -> list[dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
