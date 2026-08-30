from __future__ import annotations

from typing import Any

from src.brain.tasks import TaskQueue
from src.tools.base import AlfredTool


class RunTaskTool(AlfredTool):
    name = "run_task"

    description = (
        "Delegate a multi-step job to Alfred's background task agent, "
        "which works on Alfred's own desktop without disturbing the user "
        "and reports back when done. Use this for anything that needs "
        "several tool calls in sequence (e.g. 'organize my Downloads "
        "folder', 'check my firewall and tighten obvious risks', 'set up "
        "a Python project in C:\\dev\\foo'). For a single quick action, "
        "just call that tool directly instead."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "The outcome you want, in plain language. Be "
                        "specific about what 'done' looks like."
                    ),
                }
            },
            "required": ["goal"],
        }

    def __init__(self, queue: TaskQueue) -> None:
        self._queue = queue

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        goal = arguments.get("goal")

        if not isinstance(goal, str) or not goal.strip():
            return {"status": "error", "error": "'goal' must be a non-empty string."}

        # Set by the voice session when the request said "without
        # disturbing me". It cannot be recovered from the goal, because
        # the model rewrites the goal in its own words and drops the
        # phrase on the way.
        isolated = arguments.get("_isolated")
        isolated = True if isolated is True else None

        task_id = self._queue.submit(goal, source="voice", isolated=isolated)

        return {
            "status": "started",
            "task_id": task_id,
            **({"running_on": "alfred's private desktop"} if isolated else {}),
            "note": (
                "Working on it in the background. Tell the user you've "
                "started and will report back; don't wait. If a step is "
                "risky I'll ask them out loud before doing it."
            ),
        }


class TaskStatusTool(AlfredTool):
    name = "task_status"

    description = (
        "Check what Alfred's background task agent is doing or has "
        "recently finished. Use when the user asks 'how's that going' "
        "or 'did you finish X'."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def __init__(self, queue: TaskQueue) -> None:
        self._queue = queue

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments

        return {
            "status": "success",
            "tasks": [
                {
                    "task_id": r.id,
                    "goal": r.goal,
                    "state": r.status,
                    "summary": r.summary,
                    "awaiting_approval": r.skipped_confirmations,
                }
                for r in self._queue.recent()
            ],
        }
