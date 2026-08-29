from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Fact:
    """A single durable piece of knowledge Alfred has learned."""

    id: int
    content: str
    category: str
    confidence: float
    times_reinforced: int
    source: str
    created_at: str
    updated_at: str
    embedding: list[float] | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'general',
    confidence      REAL NOT NULL DEFAULT 0.7,
    times_reinforced INTEGER NOT NULL DEFAULT 1,
    source          TEXT NOT NULL DEFAULT 'unknown',
    embedding       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    arguments   TEXT NOT NULL,
    result      TEXT NOT NULL,
    success     INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_events_session ON tool_events(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_events_name ON tool_events(tool_name);
"""


class MemoryStore:
    """
    Alfred's long-term memory.

    Thread-safe (guarded by a single lock) because it is written to
    both from the asyncio event loop and from background threads
    (tool execution runs via asyncio.to_thread).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()

        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ----------------------------------------------------------------
    # Facts
    # ----------------------------------------------------------------

    def add_fact(
        self,
        content: str,
        category: str = "general",
        confidence: float = 0.7,
        source: str = "unknown",
        embedding: list[float] | None = None,
    ) -> int:
        now = _now()

        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO facts
                    (content, category, confidence, times_reinforced,
                     source, embedding, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    content,
                    category,
                    confidence,
                    source,
                    json.dumps(embedding) if embedding else None,
                    now,
                    now,
                ),
            )
            self._conn.commit()

            return int(cursor.lastrowid)

    def reinforce_fact(
        self,
        fact_id: int,
        confidence: float | None = None,
    ) -> None:
        with self._lock:
            if confidence is None:
                self._conn.execute(
                    """
                    UPDATE facts
                    SET times_reinforced = times_reinforced + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (_now(), fact_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE facts
                    SET times_reinforced = times_reinforced + 1,
                        confidence = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (confidence, _now(), fact_id),
                )

            self._conn.commit()

    def all_facts(self, category: str | None = None) -> list[Fact]:
        with self._lock:
            if category is None:
                rows = self._conn.execute(
                    "SELECT * FROM facts ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT * FROM facts
                    WHERE category = ?
                    ORDER BY updated_at DESC
                    """,
                    (category,),
                ).fetchall()

        return [self._row_to_fact(row) for row in rows]

    def delete_fact(self, fact_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM facts WHERE id = ?", (fact_id,)
            )
            self._conn.commit()

    def update_fact(self, fact_id: int, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE facts SET content = ?, updated_at = ? WHERE id = ?",
                (content, _now(), fact_id),
            )
            self._conn.commit()

    def search_facts(self, text: str) -> list[Fact]:
        like = f"%{text.strip()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE content LIKE ? "
                "ORDER BY times_reinforced DESC, updated_at DESC",
                (like,),
            ).fetchall()
        return [self._row_to_fact(row) for row in rows]

    def merge_facts(self, keep_id: int, drop_id: int) -> None:
        """Fold drop_id into keep_id: sum reinforcement, delete the dup."""
        with self._lock:
            self._conn.execute(
                "UPDATE facts SET times_reinforced = times_reinforced + "
                "(SELECT times_reinforced FROM facts WHERE id = ?), "
                "updated_at = ? WHERE id = ?",
                (drop_id, _now(), keep_id),
            )
            self._conn.execute("DELETE FROM facts WHERE id = ?", (drop_id,))
            self._conn.commit()

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> Fact:
        embedding_raw = row["embedding"]

        return Fact(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            confidence=row["confidence"],
            times_reinforced=row["times_reinforced"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            embedding=json.loads(embedding_raw) if embedding_raw else None,
        )

    # ----------------------------------------------------------------
    # Conversation turns
    # ----------------------------------------------------------------

    def add_turn(self, session_id: str, role: str, text: str) -> None:
        if not text.strip():
            return

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO turns (session_id, role, text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, text, _now()),
            )
            self._conn.commit()

    def session_turns(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT role, text, created_at FROM turns
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    # ----------------------------------------------------------------
    # Tool events
    # ----------------------------------------------------------------

    def add_tool_event(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        success: bool,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tool_events
                    (session_id, tool_name, arguments, result, success, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    tool_name,
                    json.dumps(arguments, default=str),
                    json.dumps(result, default=str),
                    1 if success else 0,
                    _now(),
                ),
            )
            self._conn.commit()

    def recent_tool_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tool_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def tool_failure_rate(self, tool_name: str) -> float:
        """Used to let Alfred notice a tool is unreliable over time."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT success FROM tool_events WHERE tool_name = ?",
                (tool_name,),
            ).fetchall()

        if not rows:
            return 0.0

        failures = sum(1 for row in rows if row["success"] == 0)

        return failures / len(rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
