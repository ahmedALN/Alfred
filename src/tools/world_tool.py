"""Being asked what is going on."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.brain.world import Matter, World
from src.tools.base import AlfredTool


class WorldTool(AlfredTool):
    name = "whats_on"

    description = (
        "What is going on in the user's life - what is due, what is "
        "overdue, who is waiting on them, what they have been working "
        "on. Use for 'what's on', 'what have I got coming up', 'what "
        "am I forgetting', 'anything I should know'. Also: "
        "action=remember to write down something they say matters ('my "
        "dissertation is due the 14th', 'Sam is my project partner'), "
        "and action=done when something is finished."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["whats_on", "remember", "done"],
                },
                "kind": {
                    "type": "string",
                    "enum": ["due", "person", "doing"],
                    "description": (
                        "For remember: a deadline (due), somebody (person), "
                        "or something they are working on (doing)."
                    ),
                },
                "name": {"type": "string"},
                "detail": {"type": "string"},
                "when": {
                    "type": "string",
                    "description": (
                        "For a deadline, in their words: 'friday', "
                        "'the 14th', 'next tuesday at 5'."
                    ),
                },
            },
        }

    def __init__(self, world: World, now=None) -> None:
        self._world = world
        self._now = now or datetime.now

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "whats_on").strip().lower()

        if action == "remember":
            return self._remember(arguments)
        if action == "done":
            return self._done(arguments)

        now = self._now()
        return {
            "status": "success",
            "brief": self._world.brief(now) or "Nothing on the books.",
            "overdue": self._world.overdue(now),
            "due_soon": self._world.due_soon(7, now),
            "people": self._world.of_kind("person"),
        }

    def _remember(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name") or "").strip()
        if not name:
            return {"status": "error", "error": "'name' is needed - what is it?"}

        kind = str(arguments.get("kind") or "due").strip().lower()
        if kind not in ("due", "person", "doing"):
            kind = "due"

        when = None
        said = str(arguments.get("when") or "").strip()
        if said:
            from src.brain.when import read

            found = read(said, self._now())
            when = found.at if found else None

        self._world.note(Matter(
            kind=kind, name=name,
            detail=str(arguments.get("detail") or ""),
            due=when, source="you",
        ), self._now())

        return {
            "status": "success",
            "noted": name,
            "kind": kind,
            "due": when.isoformat(timespec="minutes") if when else None,
        }

    def _done(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name") or "").strip().lower()
        if not name:
            return {"status": "error", "error": "'name' is needed."}

        for matter in self._world.all_open():
            if name in matter["name"].lower():
                self._world.settle(matter["id"], "done")
                return {"status": "success", "settled": matter["name"]}

        return {
            "status": "not_found",
            "error": f"nothing open called {name!r}",
        }
