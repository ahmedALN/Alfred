"""Asking Alfred to do something later."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.brain.schedule import ScheduleStore
from src.brain.when import phrase, read
from src.tools.base import AlfredTool


class ScheduleTool(AlfredTool):
    name = "schedule"

    description = (
        "Do something later, once or repeatedly. Use for anything with a "
        "time in it: 'remind me at 6', 'every morning summarise my inbox', "
        "'in 20 minutes', 'every Friday'. action=add needs 'when' (the "
        "person's own words about the time) and 'what' (what to do or "
        "say). kind='notify' just tells them; kind='do' actually runs it "
        "as a task. Also: list, cancel."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "cancel"],
                },
                "when": {
                    "type": "string",
                    "description": (
                        "The time, in the words it was said: 'at 6pm', "
                        "'every weekday at 9', 'in 20 minutes', "
                        "'tomorrow morning'."
                    ),
                },
                "what": {
                    "type": "string",
                    "description": (
                        "What should happen. For notify, the message. For "
                        "do, the job, written as an instruction."
                    ),
                },
                "kind": {"type": "string", "enum": ["notify", "do"]},
                "id": {
                    "type": "string",
                    "description": "Which one to cancel (from list).",
                },
            },
            "required": ["action"],
        }

    def __init__(self, store: ScheduleStore, now=None) -> None:
        self._store = store
        self._now = now or datetime.now

    # ----------------------------------------------------------------

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip().lower()

        if action == "list":
            return self._list()
        if action == "cancel":
            return self._cancel(arguments)
        if action == "add":
            return self._add(arguments)

        return {
            "status": "error",
            "error": "action must be one of ['add', 'list', 'cancel']",
        }

    def _add(self, arguments: dict[str, Any]) -> dict[str, Any]:
        said = str(
            arguments.get("when") or arguments.get("time") or ""
        ).strip()
        what = str(
            arguments.get("what") or arguments.get("goal")
            or arguments.get("text") or ""
        ).strip()

        if not what:
            return {
                "status": "error",
                "error": "'what' is needed - what should happen at that time.",
            }

        now = self._now()
        # The time may be given on its own or left inside the sentence.
        when = read(said, now) or read(f"{said} {what}".strip(), now)
        if when is None:
            return {
                "status": "error",
                "error": (
                    f"I couldn't find a time in {said!r}. Say it like "
                    "'at 6pm', 'in 20 minutes', 'every weekday at 9', "
                    "or 'tomorrow morning'."
                ),
            }

        kind = "do" if str(arguments.get("kind") or "").lower() == "do" else "notify"
        row = self._store.add(when, what, kind=kind)

        return {
            "status": "success",
            "id": row["id"],
            "kind": kind,
            "when": phrase(when, now),
            "what": what,
            # What to say back, so the person can catch a misread time
            # before it is too late to matter.
            "confirm": f"{'Will do' if kind == 'do' else 'Reminder set'}: "
                       f"{what} - {phrase(when, now)}.",
        }

    def _list(self) -> dict[str, Any]:
        now = self._now()
        rows = self._store.pending()
        return {
            "status": "success",
            "count": len(rows),
            "scheduled": [
                {
                    "id": r["id"],
                    "what": r["goal"],
                    "kind": r["kind"],
                    "when": phrase(_when_of(r), now),
                    "next": r["due"],
                }
                for r in rows
            ],
        }

    def _cancel(self, arguments: dict[str, Any]) -> dict[str, Any]:
        entry_id = str(arguments.get("id") or "").strip()
        if not entry_id:
            return {"status": "error", "error": "'id' is needed - see list."}
        if self._store.cancel(entry_id):
            return {"status": "success", "cancelled": entry_id}
        return {
            "status": "not_found",
            "error": f"nothing scheduled with id {entry_id!r}",
        }


def _when_of(row: dict[str, Any]):
    from src.brain.schedule import _as_when

    return _as_when(row)
