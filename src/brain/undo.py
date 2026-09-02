"""Putting something back.

Alfred could act and could not reverse anything. Every account of what
it did was read-only - the diary, the audit, the task log all tell you
what happened and none of them offer to undo it.

Most of what it does needs no undo, and pretending otherwise would be
worse than useless: you cannot un-send a message, un-search a store, or
un-tell somebody something. What CAN be put back is a small, honest
list - a window it opened can be closed, an event it added to your diary
can be removed, a draft it wrote can be deleted, a file it moved can be
moved back.

So each action records how to reverse it AT THE TIME, when what it did
is actually known, rather than being reconstructed afterwards from a log
by something guessing. Anything that did not record a way back says so
plainly instead of trying.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reversible (
    id       TEXT PRIMARY KEY,
    what     TEXT NOT NULL,      -- what was done, in words
    tool     TEXT NOT NULL,      -- how to put it back
    args     TEXT NOT NULL,
    task     TEXT NOT NULL DEFAULT '',
    at       TEXT NOT NULL,
    undone   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reversible_at ON reversible(undone, at);
"""

# What can be put back, and how. Deliberately short: a way back that
# only usually works is worse than admitting there is none.
_HOW_BACK = {
    ("open_app", "app"): lambda args: (
        "ui_control", {"action": "close", "window": args.get("app", "")},
        "close " + str(args.get("app", "")),
    ),
    ("calendar", "add"): lambda args: (
        None, {},                        # Alfred cannot delete events
        "remove " + str(args.get("title", "")) + " from your calendar",
    ),
}


class Undo:
    """A short memory of things that could be put back."""

    def __init__(self, path: Path | str, keep_hours: float = 12.0) -> None:
        self._lock = threading.RLock()
        self._keep = keep_hours
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ writing

    def record(
        self,
        what: str,
        tool: str | None,
        args: dict[str, Any] | None = None,
        task: str = "",
        now: datetime | None = None,
    ) -> str:
        """Note that this could be put back, and how."""
        entry = uuid.uuid4().hex[:8]
        with self._lock:
            self._conn.execute(
                "INSERT INTO reversible (id, what, tool, args, task, at) "
                "VALUES (?,?,?,?,?,?)",
                (entry, what.strip(), tool or "", json.dumps(args or {}),
                 task, (now or datetime.now()).isoformat(timespec="seconds")),
            )
            self._conn.commit()
        return entry

    def note_tool(
        self, tool: str, args: dict[str, Any], task: str = "",
        now: datetime | None = None,
    ) -> str:
        """Work out whether this call left a way back, and keep it if so."""
        back = _reverse_of(tool, args or {})
        if back is None:
            return ""
        undo_tool, undo_args, what = back
        return self.record(what, undo_tool, undo_args, task, now)

    def mark(self, entry_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reversible SET undone = 1 WHERE id = ?", (entry_id,)
            )
            self._conn.commit()

    # ------------------------------------------------------------ reading

    def recent(self, limit: int = 8, now: datetime | None = None) -> list[dict]:
        """What could still be put back, newest first.

        Nothing older than a few hours: offering to undo yesterday is
        offering to break something whose reason you have forgotten.
        """
        since = (
            (now or datetime.now()) - timedelta(hours=self._keep)
        ).isoformat(timespec="seconds")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reversible WHERE undone = 0 AND at >= ? "
                "ORDER BY at DESC LIMIT ?",
                (since, max(1, limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def last(self, now: datetime | None = None) -> dict | None:
        found = self.recent(1, now)
        return found[0] if found else None

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


def _reverse_of(
    tool: str, args: dict[str, Any]
) -> tuple[str | None, dict[str, Any], str] | None:
    """How to put this call back, if it can be."""
    if tool == "open_app":
        from src.tools.open_app import app_name

        app = app_name(args)
        if not app:
            return None
        return (
            "ui_control",
            {"action": "close", "window": app},
            f"opened {app}",
        )

    if tool == "calendar" and str(args.get("action", "")) == "add":
        title = str(args.get("title") or "").strip()
        if not title:
            return None
        # Alfred holds no permission to delete an event, so the honest
        # way back is telling you which one to remove.
        return (
            None, {},
            f"added {title!r} to your calendar (you would have to remove "
            "it yourself - Alfred cannot delete events)",
        )

    if tool == "mail" and str(args.get("action", "")) == "draft":
        return (
            None, {},
            f"drafted a reply to {args.get('to', 'someone')} "
            "(it is in Drafts, unsent)",
        )

    if tool == "mail" and str(args.get("action", "")) == "archive":
        return (
            None, {},
            "archived a message (it is in All Mail, not deleted)",
        )

    return None
