"""Everything Alfred knows, shaped for a screen.

This reads the stores directly rather than reaching into the running
Alfred. Two reasons, and both matter:

    the interface opens whether or not Alfred is up, so you can go and
    look at what it believes after it has crashed - which is exactly
    when you most want to

    sqlite connections are not shareable across threads, and borrowing
    the brain's own connections to paint a window is a good way to
    wedge the brain

Anything that genuinely has to be live - the log stream, the running
task, the microphone - comes from live.py instead.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent


def _db(name: str) -> Path:
    return _ROOT / f"alfred_{name}.sqlite3"


def _rows(name: str, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    """Read, and never let a missing table take the window down."""
    path = _db(name)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []


def _one(name: str, sql: str, args: tuple = ()) -> int:
    found = _rows(name, sql, args)
    return int(list(found[0].values())[0]) if found else 0


# ------------------------------------------------------------------ panels


def memory(limit: int = 300) -> list[dict[str, Any]]:
    """What Alfred has learned about you."""
    return _rows(
        "memory",
        "SELECT id, content, category, confidence, times_reinforced AS seen,"
        "       source, created_at, updated_at "
        "FROM facts ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    )


def episodes(limit: int = 100) -> list[dict[str, Any]]:
    """What it remembers happening."""
    return _rows(
        "episodes",
        "SELECT id, kind, summary, outcome, at FROM episodes "
        "ORDER BY at DESC LIMIT ?",
        (limit,),
    )


def life() -> dict[str, Any]:
    """The world model: what is true about you right now."""
    matters = _rows(
        "world",
        "SELECT id, kind, name, detail, due, state, source, weight, last_seen "
        "FROM matters WHERE state = 'open' ORDER BY "
        "  CASE WHEN due IS NULL THEN 1 ELSE 0 END, due, weight DESC",
    )
    now = datetime.now().isoformat(timespec="minutes")
    return {
        "overdue": [m for m in matters if m["due"] and m["due"] < now],
        "due": [m for m in matters if m["due"] and m["due"] >= now],
        "people": [m for m in matters if m["kind"] == "person"],
        "doing": [m for m in matters if m["kind"] == "doing"],
        "all": matters,
    }


def skills(limit: int = 200) -> list[dict[str, Any]]:
    found = _rows(
        "skills",
        "SELECT id, name, template, keywords, app, tier, confidence, "
        "       success, fail, unconfirmed, disabled, last_used, steps "
        "FROM skills ORDER BY success DESC, confidence DESC LIMIT ?",
        (limit,),
    )
    for skill in found:
        # Steps are stored as JSON; the window wants a count, not a blob.
        try:
            skill["step_count"] = len(json.loads(skill.pop("steps") or "[]"))
        except Exception:  # noqa: BLE001
            skill["step_count"] = 0
            skill.pop("steps", None)
    return found


def limitations(limit: int = 200) -> list[dict[str, Any]]:
    """What Alfred believes it cannot do.

    Worth being able to delete by hand: a limitation learned once, from
    a bad afternoon, otherwise stops it ever trying again.
    """
    return _rows(
        "limitations",
        "SELECT signature, tool, app, detail, hits, workaround, worked, "
        "       taught, first_seen, last_seen "
        "FROM limitations ORDER BY hits DESC, last_seen DESC LIMIT ?",
        (limit,),
    )


def apps() -> list[dict[str, Any]]:
    found = _rows(
        "apps",
        "SELECT key, display, window_title, opens, last_used FROM apps "
        "ORDER BY opens DESC",
    )
    for app in found:
        app["controls"] = _one(
            "apps", "SELECT COUNT(*) FROM app_controls WHERE app_key = ?",
            (app["key"],),
        )
        app["notes"] = _one(
            "apps", "SELECT COUNT(*) FROM app_notes WHERE app_key = ?",
            (app["key"],),
        )
    return found


def tasks(limit: int = 100) -> list[dict[str, Any]]:
    return _rows(
        "tasks",
        "SELECT id, goal, status, summary, source, created_at, updated_at "
        "FROM tasks ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


def automations() -> list[dict[str, Any]]:
    return _rows(
        "schedule",
        "SELECT id, said, goal, kind, due, repeat, every, weekday, source, "
        "       created, last_run, runs, enabled "
        "FROM scheduled ORDER BY enabled DESC, due",
    )


def activity(days: int = 1) -> list[dict[str, Any]]:
    """Where your hours actually went."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    return _rows(
        "activity",
        "SELECT app, SUM(seconds) AS seconds, COUNT(*) AS spells "
        "FROM spells WHERE started >= ? GROUP BY app "
        "ORDER BY seconds DESC LIMIT 15",
        (since,),
    )


def undo() -> list[dict[str, Any]]:
    return _rows(
        "undo",
        "SELECT id, what, tool, task, at, undone FROM reversible "
        "ORDER BY at DESC LIMIT 50",
    )


def thinking(limit: int = 80) -> list[dict[str, Any]]:
    """The brain's own record of what it noticed and decided."""
    found = _rows(
        "brain_audit",
        "SELECT id, kind, payload, created_at FROM brain_audit "
        "WHERE kind != 'tick' ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    for row in found:
        try:
            row["payload"] = json.loads(row["payload"] or "{}")
        except Exception:  # noqa: BLE001
            row["payload"] = {"raw": str(row.get("payload"))[:400]}
    return found


# ----------------------------------------------------------------- summary


def overview() -> dict[str, Any]:
    """The numbers the home panel puts on screen."""
    world = life()
    today = datetime.now().date().isoformat()
    return {
        "facts": _one("memory", "SELECT COUNT(*) FROM facts"),
        "skills": _one("skills", "SELECT COUNT(*) FROM skills WHERE disabled = 0"),
        "limitations": _one("limitations", "SELECT COUNT(*) FROM limitations"),
        "apps": _one("apps", "SELECT COUNT(*) FROM apps"),
        "tasks": _one("tasks", "SELECT COUNT(*) FROM tasks"),
        "tasks_today": _one(
            "tasks", "SELECT COUNT(*) FROM tasks WHERE created_at LIKE ?",
            (f"{today}%",),
        ),
        "automations": _one(
            "schedule", "SELECT COUNT(*) FROM scheduled WHERE enabled = 1"
        ),
        "overdue": len(world["overdue"]),
        "due": len(world["due"]),
        "people": len(world["people"]),
        "episodes": _one("episodes", "SELECT COUNT(*) FROM episodes"),
        "deliberations": _one(
            "brain_audit",
            "SELECT COUNT(*) FROM brain_audit WHERE kind = 'deliberation' "
            "AND created_at LIKE ?", (f"{today}%",),
        ),
    }


def everything() -> dict[str, Any]:
    """One shot of the whole picture, for the window's first paint."""
    return {
        "overview": overview(),
        "life": life(),
        "memory": memory(),
        "episodes": episodes(),
        "skills": skills(),
        "limitations": limitations(),
        "apps": apps(),
        "tasks": tasks(),
        "automations": automations(),
        "activity": activity(),
        "undo": undo(),
        "thinking": thinking(),
    }
