"""Skills, as something Alfred can be asked to build."""

from __future__ import annotations

from typing import Any

from src.tools.arguments import normalise_enum_action
from src.tools.base import AlfredTool


class SkillTool(AlfredTool):
    name = "skill"

    description = (
        "Alfred's own learned routines. actions: learn (work out how to "
        "do something and keep it as a reusable routine - needs 'goal'), "
        "list (what it knows how to do), show (one routine's steps - "
        "needs 'name'), forget (delete one - needs 'name'). Use 'learn' "
        "when the user asks Alfred to learn, remember how, or always do "
        "something a particular way."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["learn", "list", "show", "forget"],
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "What the routine should achieve, in the words it "
                        "would be asked for: 'search Steam for a game'."
                    ),
                },
                "name": {"type": "string"},
            },
            "required": ["action"],
        }

    def __init__(self, learner: Any, store: Any, registry: Any,
                 chat: Any) -> None:
        self._learner = learner
        self._store = store
        self._registry = registry
        self._chat = chat

    def _find(self, name: str) -> dict[str, Any] | None:
        """By name, or by what it is for - people say either."""
        wanted = (name or "").strip().lower()
        if not wanted:
            return None
        known = self._store.all(include_disabled=True)
        for skill in known:
            if skill["name"].lower() == wanted:
                return skill
        for skill in known:
            if wanted in skill["template"].lower():
                return skill
        return None

    # ----------------------------------------------------------------

    def normalise_arguments(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """A call with a goal in it is a request to learn that goal.

        "Learn a routine for making a cup of tea" reached this tool
        eight times without an `action`, was refused eight times, and
        wrote the refusal into memory each time as though it were a
        fact about the world.
        """

        return normalise_enum_action(
            arguments,
            valid=("learn", "list", "show", "forget"),
            tells=(
                ("learn", ("goal",)),
                ("show", ("name",)),
            ),
        )

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip().lower()
        action = {"create": "learn", "teach": "learn", "add": "learn",
                  "delete": "forget", "remove": "forget"}.get(action, action)

        if action == "learn":
            return self._learn(str(arguments.get("goal") or "").strip())
        if action == "list":
            return self._list()
        if action == "show":
            return self._show(str(arguments.get("name") or "").strip())
        if action == "forget":
            return self._forget(str(arguments.get("name") or "").strip())

        return {
            "status": "error",
            "error": "action must be one of ['learn', 'list', 'show', 'forget']",
        }

    # ----------------------------------------------------------------

    def _learn(self, goal: str) -> dict[str, Any]:
        from src.brain.design import Impossible, design

        if not goal:
            return {"status": "error",
                    "error": "'goal' is needed - what should the routine do?"}

        # Already knows it? Teaching it twice is how you end up with two
        # routines that drift apart.
        existing = self._learner.match(goal)
        if existing:
            return {
                "status": "success",
                "already_knew": True,
                "said": (
                    f"I already know how to do that - "
                    f"'{existing.get('template', goal)}'."
                ),
            }

        try:
            skill = design(goal, self._chat, self._registry,
                           learner=self._learner)
        except Impossible as exc:
            return {
                "status": "error",
                "error": str(exc),
                "said": f"I can't work out how to do that: {exc}",
            }

        self._learner.save(skill)
        steps = " then ".join(s["tool"] for s in skill["steps"])
        return {
            "status": "success",
            "name": skill["name"],
            "template": skill["template"],
            "steps": len(skill["steps"]),
            "said": (
                f"Learned it: {skill['template']} - {steps}. "
                "I haven't run it yet, so I'll check it carefully the "
                "first time."
            ),
        }

    def _list(self) -> dict[str, Any]:
        known = self._store.all()
        return {
            "status": "success",
            "count": len(known),
            "skills": [
                {"name": s["name"], "template": s["template"],
                 "used": s.get("success", 0), "unproven": bool(s.get("unconfirmed"))}
                for s in known
            ],
        }

    def _show(self, name: str) -> dict[str, Any]:
        found = self._find(name)
        if not found:
            return {"status": "error", "error": f"no routine called {name!r}"}
        return {
            "status": "success",
            "template": found["template"],
            "steps": found["steps"],
            "used": found.get("success", 0),
            "failed": found.get("fail", 0),
        }

    def _forget(self, name: str) -> dict[str, Any]:
        found = self._find(name)
        if not found:
            return {"status": "error", "error": f"no routine called {name!r}"}
        self._store.delete(found["id"])
        return {"status": "success",
                "said": f"Forgotten how to {found['template']}."}
