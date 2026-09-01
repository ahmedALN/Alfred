"""Alfred's own interface, as something Alfred can open."""

from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool


class InterfaceTool(AlfredTool):
    name = "interface"

    description = (
        "Alfred's own on-screen interface - a window showing its logs, "
        "memory, beliefs, tasks, automations, what it knows about the "
        "user's life, and what it can see. actions: open (show it), "
        "close (shut the window), status (is it open). Use this when "
        "the user asks to see the interface, the dashboard, the logs, "
        "or what Alfred is thinking. It does not open on its own."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "close", "status"],
                },
            },
            "required": ["action"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "open").strip().lower()

        try:
            from src.ui import opener
        except ImportError as exc:
            return {
                "status": "error",
                "error": f"The interface is not installed here ({exc}).",
            }

        if action in ("open", "show", "up"):
            result = opener.open_interface()
            if result.get("status") != "success":
                return result
            # Said in words, because this answer is read aloud.
            return {
                "status": "success",
                "said": (
                    "The interface is already up - I have brought it to the "
                    "front." if result["what"] == "shown"
                    else "The interface is open."
                ),
            }

        if action in ("close", "hide", "down"):
            result = opener.close_interface()
            return {
                "status": result.get("status", "success"),
                "said": "The interface is closed."
                if result.get("what") == "closed"
                else "It was not open.",
            }

        if action == "status":
            return {
                "status": "success",
                "open": opener.is_open(),
                "said": "The interface is open." if opener.is_open()
                else "The interface is not open.",
            }

        return {
            "status": "error",
            "error": "action must be one of ['open', 'close', 'status']",
        }
