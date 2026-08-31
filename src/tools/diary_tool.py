"""Being asked what you did."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.tools.base import AlfredTool

_ROOT = Path(__file__).resolve().parent.parent.parent


class DiaryTool(AlfredTool):
    name = "what_did_you_do"

    description = (
        "An account of what Alfred did on a given day - jobs asked for "
        "and how they went, what it did on its own, what it learned, "
        "what it could not get past. Use when the user asks 'what did "
        "you do today', 'how did it go', 'what have you been up to', "
        "'did you get anything done yesterday'. Read from the record, "
        "not from memory."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "enum": ["today", "yesterday"],
                    "description": "Which day. Defaults to today.",
                }
            },
        }

    def __init__(self, chat: Any = None, root: Path | str = _ROOT) -> None:
        self._chat = chat
        self._root = Path(root)

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from src.brain.diary import gather, tell

        which = str(arguments.get("day") or "today").strip().lower()
        on = date.today() - timedelta(days=1) if which == "yesterday" else date.today()

        try:
            day = gather(self._root, on)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        return {
            "status": "success",
            "day": on.isoformat(),
            "account": tell(day, self._chat),
            # The counts separately, so a spoken answer can be checked
            # against them rather than trusted.
            "asked_for": len(day.asked),
            "own": len(day.own),
            "learned": len(day.learned),
            "stuck_on": len(day.stuck),
        }
