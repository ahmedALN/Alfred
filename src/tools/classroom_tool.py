"""Coursework, as something Alfred can look at."""

from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool
from src.workspace.account import GoogleError


class ClassroomTool(AlfredTool):
    name = "classroom"

    description = (
        "The user's Google Classroom. actions: due (what is coming, "
        "'days' ahead, soonest first), courses (which classes), "
        "announcements. Read only - Alfred cannot submit work, join a "
        "class, or change anything."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["due", "courses", "announcements"],
                },
                "days": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["action"],
        }

    def __init__(self, classroom: Any) -> None:
        self._classroom = classroom

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip().lower()

        if action in ("submit", "turn_in", "hand_in", "join", "grade"):
            return {
                "status": "refused",
                "error": (
                    "Alfred can only read Classroom. Handing work in is "
                    "the user's own to do - tell them what is due."
                ),
            }

        try:
            return self._do(action, arguments)
        except GoogleError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    def _do(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if action == "due":
            days = int(arguments.get("days") or 14)
            work = self._classroom.due(days=days)
            outstanding = [w for w in work if not w["handed_in"]]
            return {
                "status": "success",
                "days": days,
                "count": len(outstanding),
                "due": outstanding,
                # Kept separate so a summary can say "and three already
                # handed in" rather than listing them as though they
                # were hanging over somebody.
                "already_handed_in": len(work) - len(outstanding),
            }

        if action == "courses":
            found = self._classroom.courses()
            return {"status": "success", "count": len(found), "courses": found}

        if action == "announcements":
            limit = int(arguments.get("limit") or 10)
            return {
                "status": "success",
                "announcements": self._classroom.announcements(limit),
            }

        return {
            "status": "error",
            "error": (
                "action must be one of ['due', 'courses', 'announcements']"
            ),
        }
