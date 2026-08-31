"""Things Alfred is supposed to do later.

Until now nothing could survive the moment it was said. Alfred could be
asked to do something and would do it; it could not be asked to do
something at seven, because there was nowhere to write that down. The
brain has been ticking every ninety seconds this whole time with nothing
to check.

Two kinds live here, and the difference matters:

    notify   say something at a time. "remind me to take the bins out."
    do       run a job at a time. "every morning summarise my inbox."

A reminder that quietly ran a task would be alarming; a task that only
reminded you would be useless. Which one it is is decided when it is
written down, not when it fires.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.brain.when import When

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled (
    id          TEXT PRIMARY KEY,
    said        TEXT NOT NULL,      -- what the person actually asked for
    goal        TEXT NOT NULL,      -- what to do when it fires
    kind        TEXT NOT NULL,      -- notify | do
    due         TEXT NOT NULL,      -- ISO, next time it fires
    repeat      TEXT NOT NULL DEFAULT '',
    every       INTEGER NOT NULL DEFAULT 0,
    weekday     INTEGER NOT NULL DEFAULT -1,
    source      TEXT NOT NULL DEFAULT 'voice',
    created     TEXT NOT NULL,
    last_run    TEXT,
    runs        INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled(enabled, due);
"""


class ScheduleStore:
    """What is owed, and when."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ writing

    def add(
        self, when: When, goal: str, kind: str = "notify",
        source: str = "voice",
    ) -> dict[str, Any]:
        row = {
            "id": uuid.uuid4().hex[:8],
            "said": when.said or goal,
            "goal": goal.strip(),
            "kind": "do" if kind == "do" else "notify",
            "due": when.at.isoformat(timespec="seconds"),
            "repeat": when.repeat,
            "every": when.every,
            "weekday": when.weekday,
            "source": source,
            "created": datetime.now().isoformat(timespec="seconds"),
            "runs": 0,
            "enabled": 1,
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO scheduled (id, said, goal, kind, due, repeat, "
                "every, weekday, source, created, runs, enabled) VALUES "
                "(:id, :said, :goal, :kind, :due, :repeat, :every, :weekday, "
                ":source, :created, :runs, :enabled)",
                row,
            )
            self._conn.commit()
        return row

    def ran(self, entry_id: str, now: datetime | None = None) -> str:
        """Mark one as done and work out whether it comes round again.

        A one-off is finished and switches itself off. A repeat moves to
        its next occurrence - computed from now rather than from the due
        time, so a machine that was asleep for a week does not wake up
        owing seven breakfasts.
        """
        now = now or datetime.now()
        row = self.get(entry_id)
        if row is None:
            return ""

        nxt = _as_when(row).after(now)
        with self._lock:
            if nxt is None:
                self._conn.execute(
                    "UPDATE scheduled SET enabled = 0, last_run = ?, "
                    "runs = runs + 1 WHERE id = ?",
                    (now.isoformat(timespec="seconds"), entry_id),
                )
            else:
                self._conn.execute(
                    "UPDATE scheduled SET due = ?, last_run = ?, "
                    "runs = runs + 1 WHERE id = ?",
                    (nxt.isoformat(timespec="seconds"),
                     now.isoformat(timespec="seconds"), entry_id),
                )
            self._conn.commit()
        return nxt.isoformat(timespec="seconds") if nxt else ""

    def cancel(self, entry_id: str) -> bool:
        with self._lock:
            changed = self._conn.execute(
                "UPDATE scheduled SET enabled = 0 WHERE id = ? AND enabled = 1",
                (entry_id,),
            ).rowcount
            self._conn.commit()
        return bool(changed)

    # ------------------------------------------------------------ reading

    def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduled WHERE enabled = 1 AND due <= ? "
                "ORDER BY due",
                (now.isoformat(timespec="seconds"),),
            ).fetchall()
        return [dict(r) for r in rows]

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduled WHERE enabled = 1 ORDER BY due"
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduled WHERE id = ?", (entry_id,)
            ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


def _as_when(row: dict[str, Any]) -> When:
    return When(
        at=datetime.fromisoformat(row["due"]),
        repeat=row["repeat"] or "",
        every=int(row["every"] or 0),
        weekday=int(row["weekday"] if row["weekday"] is not None else -1),
        said=row["said"],
    )
