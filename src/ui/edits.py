"""Changing what Alfred believes, from the window.

Reading is the easy half. This is the half that makes the interface
worth having: a wrong fact learned once will otherwise sit there
forever, and a limitation recorded on a bad afternoon quietly stops
Alfred ever attempting that thing again.

Every write here goes through _write, which is deliberately narrow:
a fixed statement, bound arguments, and a busy timeout long enough to
wait out the brain mid-tick rather than throwing "database is locked"
at somebody who just clicked a button.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent


class EditError(RuntimeError):
    """Said in words a person reads, because a person will read it."""


def _write(db: str, sql: str, args: tuple = ()) -> int:
    path = _ROOT / f"alfred_{db}.sqlite3"
    if not path.exists():
        raise EditError(f"There is no {db} store to change.")
    try:
        conn = sqlite3.connect(str(path), timeout=8.0)
        try:
            changed = conn.execute(sql, args).rowcount
            conn.commit()
            return changed
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise EditError(
                "Alfred is in the middle of something and has the file. "
                "Try again in a moment."
            ) from exc
        raise EditError(str(exc)) from exc


# ------------------------------------------------------------------ memory


def forget_fact(fact_id: int) -> bool:
    """Delete something Alfred believes about you."""
    if not _write("memory", "DELETE FROM facts WHERE id = ?", (int(fact_id),)):
        raise EditError("No such fact - it may already be gone.")
    return True


def correct_fact(fact_id: int, content: str) -> bool:
    """Rewrite it rather than delete it.

    The embedding is cleared: it described the old wording, and a stale
    vector would keep recalling this fact for the old question.
    """
    content = (content or "").strip()
    if not content:
        raise EditError("A fact cannot be made empty. Delete it instead.")
    if not _write(
        "memory",
        "UPDATE facts SET content = ?, embedding = NULL, "
        "source = 'you (corrected)', updated_at = datetime('now') WHERE id = ?",
        (content, int(fact_id)),
    ):
        raise EditError("No such fact.")
    return True


def add_fact(content: str, category: str = "general") -> bool:
    """Tell Alfred something directly."""
    content = (content or "").strip()
    if not content:
        raise EditError("Nothing to remember.")
    _write(
        "memory",
        "INSERT INTO facts (content, category, confidence, times_reinforced,"
        " source, created_at, updated_at) "
        "VALUES (?, ?, 1.0, 1, 'you', datetime('now'), datetime('now'))",
        (content, (category or "general").strip()),
    )
    return True


# ------------------------------------------------------------- limitations


def clear_limitation(signature: str) -> bool:
    """Let it try again.

    A limitation is a memory of failing. Deleting one is how you say
    "that was the old version, go and find out".
    """
    if not _write(
        "limitations", "DELETE FROM limitations WHERE signature = ?",
        (str(signature),),
    ):
        raise EditError("No such limitation.")
    return True


# ------------------------------------------------------------------ skills


def set_skill_enabled(skill_id: str, enabled: bool) -> bool:
    if not _write(
        "skills", "UPDATE skills SET disabled = ? WHERE id = ?",
        (0 if enabled else 1, str(skill_id)),
    ):
        raise EditError("No such skill.")
    return True


def delete_skill(skill_id: str) -> bool:
    if not _write("skills", "DELETE FROM skills WHERE id = ?", (str(skill_id),)):
        raise EditError("No such skill.")
    return True


# ------------------------------------------------------------------- world


def settle_matter(matter_id: str, state: str = "done") -> bool:
    """Mark something in your life dealt with, or not yours after all."""
    if state not in ("done", "dropped", "open"):
        raise EditError("A matter is open, done or dropped.")
    if not _write(
        "world", "UPDATE matters SET state = ? WHERE id = ?",
        (state, str(matter_id)),
    ):
        raise EditError("No such thing on your plate.")
    return True


# ------------------------------------------------------------- automations


def set_automation_enabled(item_id: str, enabled: bool) -> bool:
    if not _write(
        "schedule", "UPDATE scheduled SET enabled = ? WHERE id = ?",
        (1 if enabled else 0, str(item_id)),
    ):
        raise EditError("No such automation.")
    return True


def delete_automation(item_id: str) -> bool:
    if not _write("schedule", "DELETE FROM scheduled WHERE id = ?", (str(item_id),)):
        raise EditError("No such automation.")
    return True


# ------------------------------------------------------------------- tasks


def forget_task(task_id: str) -> bool:
    """Remove a task from the record. Does not stop a running one."""
    if not _write("tasks", "DELETE FROM tasks WHERE id = ?", (str(task_id),)):
        raise EditError("No such task.")
    return True


# ------------------------------------------------------------------ router

# Named actions rather than arbitrary SQL from the browser. The window
# is on 127.0.0.1 and nothing else should be reaching it, but a UI that
# can run any statement is one XSS away from being a shell.
ACTIONS = {
    "forget_fact": lambda p: forget_fact(p["id"]),
    "correct_fact": lambda p: correct_fact(p["id"], p["content"]),
    "add_fact": lambda p: add_fact(p["content"], p.get("category", "general")),
    "clear_limitation": lambda p: clear_limitation(p["signature"]),
    "enable_skill": lambda p: set_skill_enabled(p["id"], True),
    "disable_skill": lambda p: set_skill_enabled(p["id"], False),
    "delete_skill": lambda p: delete_skill(p["id"]),
    "settle_matter": lambda p: settle_matter(p["id"], p.get("state", "done")),
    "enable_automation": lambda p: set_automation_enabled(p["id"], True),
    "disable_automation": lambda p: set_automation_enabled(p["id"], False),
    "delete_automation": lambda p: delete_automation(p["id"]),
    "forget_task": lambda p: forget_task(p["id"]),
}


def apply(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    handler = ACTIONS.get(action)
    if handler is None:
        raise EditError(f"There is no such action as {action!r}.")
    try:
        handler(payload or {})
    except KeyError as exc:
        raise EditError(f"That action needs {exc.args[0]!r}.") from exc
    return {"ok": True, "action": action}
