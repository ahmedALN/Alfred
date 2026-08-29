from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    template     TEXT NOT NULL DEFAULT '',
    keywords     TEXT NOT NULL DEFAULT '[]',
    params       TEXT NOT NULL DEFAULT '[]',
    steps        TEXT NOT NULL DEFAULT '[]',
    verify       TEXT NOT NULL DEFAULT '',
    app          TEXT NOT NULL DEFAULT '',
    tier         TEXT NOT NULL DEFAULT 'ordinary',
    danger_note  TEXT NOT NULL DEFAULT '',
    success      INTEGER NOT NULL DEFAULT 0,
    fail         INTEGER NOT NULL DEFAULT 0,
    confidence   REAL NOT NULL DEFAULT 0.5,
    unconfirmed  INTEGER NOT NULL DEFAULT 0,
    disabled     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    last_used    TEXT NOT NULL DEFAULT ''
);
"""

_JSON_COLS = ("keywords", "params", "steps")


class SkillStore:
    """
    Durable store of learned skills - replayable tool sequences distilled
    from tasks Alfred has already completed and verified. Thread-safe.
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

    # ----------------------------------------------------------------

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for col in _JSON_COLS:
            try:
                d[col] = json.loads(d.get(col) or "[]")
            except (TypeError, json.JSONDecodeError):
                d[col] = []
        d["unconfirmed"] = bool(d.get("unconfirmed"))
        d["disabled"] = bool(d.get("disabled"))
        return d

    def all(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM skills"
        if not include_disabled:
            sql += " WHERE disabled = 0"
        sql += " ORDER BY success DESC, last_used DESC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._decode(r) for r in rows]

    def get(self, skill_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM skills WHERE id = ?", (skill_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def upsert(self, skill: dict[str, Any]) -> None:
        row = {
            "id": skill["id"],
            "name": skill.get("name", skill["id"]),
            "template": skill.get("template", ""),
            "keywords": json.dumps(skill.get("keywords", [])),
            "params": json.dumps(skill.get("params", [])),
            "steps": json.dumps(skill.get("steps", [])),
            "verify": skill.get("verify", ""),
            "app": skill.get("app", ""),
            "tier": skill.get("tier", "ordinary"),
            "danger_note": skill.get("danger_note", ""),
            "success": int(skill.get("success", 0)),
            "fail": int(skill.get("fail", 0)),
            "confidence": float(skill.get("confidence", 0.5)),
            "unconfirmed": 1 if skill.get("unconfirmed") else 0,
            "disabled": 1 if skill.get("disabled") else 0,
            "created_at": skill.get("created_at") or _now(),
            "last_used": skill.get("last_used", ""),
        }
        cols = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in row if c != "id")
        with self._lock:
            self._conn.execute(
                f"INSERT INTO skills ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                tuple(row.values()),
            )
            self._conn.commit()

    def record_use(
        self, skill_id: str, *, ok: bool, confidence: float
    ) -> None:
        col = "success" if ok else "fail"
        with self._lock:
            self._conn.execute(
                f"UPDATE skills SET {col} = {col} + 1, confidence = ?, "
                f"last_used = ? WHERE id = ?",
                (round(confidence, 3), _now(), skill_id),
            )
            self._conn.commit()

    def set_disabled(self, skill_id: str, disabled: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE skills SET disabled = ? WHERE id = ?",
                (1 if disabled else 0, skill_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete(self, skill_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM skills WHERE id = ?", (skill_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
