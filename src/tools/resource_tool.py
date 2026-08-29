from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool


class ResourceModeTool(AlfredTool):
    name = "resource_mode"

    description = (
        "Switch Alfred between normal and low-resource 'game' mode. In "
        "game mode Alfred unloads its local models (frees GPU memory) and "
        "pauses background work; voice still works so you can switch back. "
        "Use when the user says they're gaming, wants resources freed, or "
        "wants everything back to normal."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["game", "normal", "status"],
                }
            },
            "required": ["action"],
        }

    def __init__(self, resource_mode: Any) -> None:
        self._rm = resource_mode

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")

        if action == "status":
            return {"status": "success", "mode": self._rm.state}

        if action not in ("game", "normal"):
            return {"status": "error", "error": "action must be game/normal/status"}

        # enter/exit are async; schedule them thread-safely.
        self._rm.request("game" if action == "game" else "normal")

        return {
            "status": "success",
            "mode": "game" if action == "game" else "normal",
            "note": "Switching now; tell the user briefly.",
        }
