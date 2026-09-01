"""The diary, as something Alfred can work with."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.brain.when import read as read_time
from src.tools.base import AlfredTool
from src.workspace.account import GoogleError


class CalendarTool(AlfredTool):
    name = "calendar"

    description = (
        "The user's Google Calendar. actions: agenda (what is on, "
        "'days' ahead), next (the very next thing), free (is a window "
        "clear - needs 'when'), add (put something in the diary - needs "
        "'title' and 'when', optional 'minutes', 'where'). Alfred can "
        "read and add; it cannot delete or change existing events."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["agenda", "next", "free", "add"],
                },
                "days": {"type": "integer"},
                "when": {
                    "type": "string",
                    "description": (
                        "In the words it was said: 'tomorrow at 3', "
                        "'friday at 09:00', 'in two hours'."
                    ),
                },
                "title": {"type": "string"},
                "minutes": {"type": "integer"},
                "where": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["action"],
        }

    def __init__(self, calendar: Any, now=None) -> None:
        self._calendar = calendar
        self._now = now or datetime.now

    # ----------------------------------------------------------------

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip().lower()

        # Said as a rule rather than as a schema error, so the model
        # learns what Alfred is for rather than what it can spell.
        if action in ("delete", "remove", "cancel", "move", "edit", "update"):
            return {
                "status": "refused",
                "error": (
                    "Alfred can read the calendar and add to it, and does "
                    "not change or remove what is already there. Tell the "
                    "user what needs changing and let them do it."
                ),
            }

        try:
            return self._do(action, arguments)
        except GoogleError as exc:
            return {"status": "error", "error": self._why(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": self._why(exc)}

    def _why(self, exc: BaseException) -> str:
        """A refusal named as the thing that will not work."""
        from src.workspace.account import explain_denied

        account = getattr(
            getattr(self, "_mail", None)
            or getattr(self, "_calendar", None)
            or getattr(self, "_classroom", None),
            "_account", None,
        )
        held = account.granted() if account is not None else []
        return explain_denied(exc, held)

    def _do(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if action == "agenda":
            days = int(arguments.get("days") or 1)
            events = self._calendar.agenda(days=days, now=self._now())
            return {
                "status": "success",
                "days": days,
                "count": len(events),
                "events": events,
            }

        if action == "next":
            found = self._calendar.next_up(now=self._now())
            return {
                "status": "success",
                "event": found,
                "nothing": found is None,
            }

        if action == "free":
            when = self._when(arguments)
            if when is None:
                return _needs_when()
            minutes = int(arguments.get("minutes") or 60)
            from datetime import timedelta

            clear, clashes = self._calendar.free_between(
                when, when + timedelta(minutes=minutes)
            )
            return {
                "status": "success",
                "free": clear,
                "clashes": clashes,
                "window": when.strftime("%a %d %b %H:%M"),
            }

        if action == "add":
            title = str(arguments.get("title") or arguments.get("what") or "")
            when = self._when(arguments)
            if not title.strip():
                return {"status": "error", "error": "'title' is needed."}
            if when is None:
                return _needs_when()
            return {
                "status": "success",
                **self._calendar.add(
                    title.strip(),
                    when,
                    minutes=int(arguments.get("minutes") or 60),
                    where=str(arguments.get("where") or ""),
                    notes=str(arguments.get("notes") or ""),
                ),
            }

        return {
            "status": "error",
            "error": "action must be one of ['agenda', 'next', 'free', 'add']",
        }

    def _when(self, arguments: dict[str, Any]) -> datetime | None:
        said = str(
            arguments.get("when") or arguments.get("time")
            or arguments.get("start") or ""
        )
        found = read_time(said, self._now())
        return found.at if found else None


def _needs_when() -> dict[str, Any]:
    return {
        "status": "error",
        "error": (
            "'when' is needed, in the words it was said - 'tomorrow at 3', "
            "'friday at 09:00', 'in two hours'."
        ),
    }
