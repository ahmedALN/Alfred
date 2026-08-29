from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool


class EpisodesTool(AlfredTool):
    name = "episodes"

    description = (
        "Look up what Alfred has actually done recently - tasks finished, "
        "routines replayed, notable background events - with timestamps. "
        "Use this for questions like 'what have you done today?', 'did you "
        "already move those files?', or 'when did you last check the "
        "firewall?'. This is episodic history, not the durable-facts memory "
        "(that's 'recall')."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["recent", "search"],
                    "description": "'recent' for the latest activity, "
                    "'search' to find episodes mentioning some text.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to search for (action='search').",
                },
                "hours": {
                    "type": "number",
                    "description": "How far back to look for action='recent' "
                    "(default 24).",
                },
            },
            "required": ["action"],
        }

    def __init__(self, store: Any) -> None:
        self._store = store

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action", "recent")).strip()

        try:
            if action == "search":
                text = str(arguments.get("text", "")).strip()
                if not text:
                    return {"status": "error", "error": "search needs 'text'"}
                rows = self._store.search(text)
            else:
                hours = float(arguments.get("hours", 24) or 24)
                rows = self._store.recent(hours=hours)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        return {
            "status": "success",
            "count": len(rows),
            "episodes": [
                {
                    "at": r["at"],
                    "kind": r["kind"],
                    "summary": r["summary"],
                    "outcome": r["outcome"],
                }
                for r in rows
            ],
        }
