from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    summary    TEXT NOT NULL,
    outcome    TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_at ON episodes (at);
"""


class EpisodeStore:
    """
    Alfred's episodic memory: a timestamped log of things that actually
    happened - tasks finished, skills replayed, notable proactive events.
    Distinct from the semantic MemoryStore (durable facts): this answers
    "what have you done today?" and "did you already move those files?".
    Thread-safe.
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

    def record(self, kind: str, summary: str, outcome: str = "") -> int:
        summary = summary.strip()
        if not summary:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO episodes (kind, summary, outcome, at) "
                "VALUES (?, ?, ?, ?)",
                (kind.strip() or "note", summary, outcome.strip(),
                 _now().isoformat()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent(self, hours: float = 24.0, limit: int = 40) -> list[dict[str, Any]]:
        cutoff = (_now() - timedelta(hours=hours)).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM episodes WHERE at >= ? ORDER BY at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        text = text.strip()
        if not text:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM episodes WHERE summary LIKE ? OR outcome LIKE ? "
                "ORDER BY at DESC LIMIT ?",
                (f"%{text}%", f"%{text}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune(self, keep_days: float = 30.0) -> int:
        cutoff = (_now() - timedelta(days=keep_days)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM episodes WHERE at < ?", (cutoff,)
            )
            self._conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
