"""What Alfred keeps running into, and what got past it.

A lesson written after a single failed task is a guess: the run might
have been unlucky, the app might have been mid-update, the model might
have picked badly once. A lesson written after the same wall twice, with
a route that actually worked recorded beside it, is knowledge.

So failures are counted rather than reacted to, and what a failure is
worth is decided by whether it happens again - and by whether anything
was seen to get around it.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS limitations (
    signature   TEXT PRIMARY KEY,
    tool        TEXT NOT NULL DEFAULT '',
    app         TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    hits        INTEGER NOT NULL DEFAULT 0,
    workaround  TEXT NOT NULL DEFAULT '',
    worked      INTEGER NOT NULL DEFAULT 0,
    taught      INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
"""

# Bits of an error that describe THIS run rather than the failure:
# names, paths, numbers, quoted specifics.
_NOISE = [
    (re.compile(r"'[^']*'"), "X"),
    (re.compile(r'"[^"]*"'), "X"),
    (re.compile(r"\b[A-Za-z]:\[^\s]*"), "PATH"),
    (re.compile(r"\bref=\S+"), "ref"),
    (re.compile(r"\bname=\S+"), "name"),
    (re.compile(r"\b\d+\b"), "N"),
    (re.compile(r"\s+"), " "),
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def shape_of(tool: str, error: str, app: str = "") -> str:
    """The reusable shape of a failure, with this run's details removed.

    "no control matches ref=None name='Deji'" and "...name='Launch'" are
    the same wall; counting them separately would mean never noticing it.
    """
    text = (error or "").strip().lower()
    for pattern, replacement in _NOISE:
        text = pattern.sub(replacement, text)

    text = text.strip()[:120]
    app = (app or "").strip().lower()[:40]
    return f"{(tool or '?').strip().lower()}|{app}|{text}"


class LimitationStore:
    """Walls Alfred has hit, how often, and what got past them."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------------- write

    def hit(self, tool: str, error: str, app: str = "") -> str:
        """Record running into something. Returns the failure's shape."""
        signature = shape_of(tool, error, app)
        now = _now()

        with self._lock:
            self._conn.execute(
                "INSERT INTO limitations (signature, tool, app, detail, "
                "hits, first_seen, last_seen) VALUES (?, ?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(signature) DO UPDATE SET "
                "hits = hits + 1, last_seen = excluded.last_seen",
                (signature, tool or "", app or "", (error or "")[:200],
                 now, now),
            )
            self._conn.commit()
        return signature

    def got_past(self, signature: str, how: str) -> None:
        """Record what worked after that wall, in the same piece of work."""
        how = " ".join((how or "").split())[:120]
        if not how:
            return

        with self._lock:
            self._conn.execute(
                "UPDATE limitations SET workaround = ?, worked = worked + 1, "
                "last_seen = ? WHERE signature = ?",
                (how, _now(), signature),
            )
            self._conn.commit()

    def mark_taught(self, signature: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE limitations SET taught = 1 WHERE signature = ?",
                (signature,),
            )
            self._conn.commit()

    # ----------------------------------------------------------------- read

    def ready_to_teach(self, min_hits: int = 2) -> list[dict[str, Any]]:
        """Walls hit more than once that something is known to get past.

        Both halves matter. Without the repeat it is bad luck; without a
        route that worked there is nothing to say except "this fails".
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM limitations WHERE taught = 0 AND hits >= ? "
                "AND workaround != '' ORDER BY hits DESC",
                (min_hits,),
            ).fetchall()
        return [dict(r) for r in rows]

    def unsolved(self, min_hits: int = 3) -> list[dict[str, Any]]:
        """Walls hit repeatedly that nothing has got past yet."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM limitations WHERE workaround = '' AND hits >= ? "
                "ORDER BY hits DESC",
                (min_hits,),
            ).fetchall()
        return [dict(r) for r in rows]

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM limitations ORDER BY hits DESC, last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
