from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS brain_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_brain_audit_kind ON brain_audit(kind);
"""


class AuditLog:
    """
    Append-only record of everything the brain perceives, proposes,
    decides, and does. Written to SQLite (queryable) and mirrored to a
    JSONL file next to it (tailable, greppable, survives a corrupt DB).

    Thread-safe: the orchestrator writes from a worker thread while the
    event loop may read.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._jsonl_path = self._path.with_suffix(".jsonl")

        self._lock = threading.RLock()

        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ----------------------------------------------------------------

    def record(
        self,
        kind: str,
        payload: dict[str, Any],
        session_id: str | None = None,
    ) -> int:
        """
        kind is one of: "tick", "notable", "proposal", "decision",
        "action", "action_result", "spoken", "blocked", "error".
        """

        timestamp = _now()

        entry = {
            "ts": timestamp,
            "session_id": session_id,
            "kind": kind,
            **payload,
        }

        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO brain_audit
                    (session_id, kind, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    kind,
                    json.dumps(payload, default=str),
                    timestamp,
                ),
            )
            self._conn.commit()

            row_id = int(cursor.lastrowid)

            try:
                with open(self._jsonl_path, "a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps({"id": row_id, **entry}, default=str)
                        + "\n"
                    )
            except OSError as exc:
                print(f"[Brain/Audit] JSONL mirror write failed: {exc}")

        return row_id

    def prune(self, keep_days: float = 21.0, jsonl_max_mb: float = 25.0) -> int:
        """Drop audit rows older than keep_days and truncate the JSONL
        mirror if it has grown past jsonl_max_mb. Called on brain startup
        so months of ticks and task steps don't accumulate forever."""
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat()
        with self._lock:
            try:
                cur = self._conn.execute(
                    "DELETE FROM brain_audit WHERE created_at < ?", (cutoff,)
                )
                self._conn.commit()
                removed = cur.rowcount
                self._conn.execute("VACUUM")
            except Exception as exc:  # noqa: BLE001
                print(f"[Brain/Audit] prune failed: {exc}")
                removed = 0

            try:
                if (
                    self._jsonl_path.exists()
                    and self._jsonl_path.stat().st_size > jsonl_max_mb * 1_000_000
                ):
                    lines = self._jsonl_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    self._jsonl_path.write_text(
                        "\n".join(lines[-20_000:]) + "\n", encoding="utf-8"
                    )
            except OSError as exc:
                print(f"[Brain/Audit] JSONL trim failed: {exc}")

        return removed

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM brain_audit
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        out: list[dict[str, Any]] = []

        for row in rows:
            item = dict(row)

            try:
                item["payload"] = json.loads(item["payload"])
            except (json.JSONDecodeError, TypeError):
                pass

            out.append(item)

        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
