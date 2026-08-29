from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool
from src.windows.uia import UiaError, UiaSession

_ACTIONS = (
    "tree", "click", "invoke", "type", "key", "get", "wait_for", "exists",
)


class UIControlTool(AlfredTool):
    """
    Control real Windows apps through the accessibility layer - reads
    buttons/fields/menu items by name and clicks them exactly. No
    screenshots, no guessed coordinates. This is the right tool for
    Spotify, browsers, File Explorer, Settings, Office, and most apps.
    """

    name = "ui_control"

    description = (
        "Drive a Windows app precisely via its accessibility tree. "
        "action='tree' with window= (a title substring, regex) or pid= "
        "lists every clickable control with a 'ref', its name, type and "
        "centre. Then: click ref=/name=, invoke ref=/name=, type text= "
        "[into=ref], key keys= (e.g. '^l' for Ctrl+L, '{ENTER}'), get "
        "ref=/name=, wait_for name= [timeout=]. Always 'tree' first. "
        "Prefer this over desktop_control for normal apps - it's exact. "
        "Use desktop_control only when 'tree' returns nothing useful "
        "(some games)."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "window": {
                    "type": "string",
                    "description": "Window title substring/regex (for 'tree').",
                },
                "pid": {"type": "integer"},
                "ref": {"type": "integer", "description": "Control ref from 'tree'."},
                "name": {"type": "string", "description": "Control name to match."},
                "text": {"type": "string"},
                "into": {"type": "integer", "description": "ref to type into."},
                "keys": {
                    "type": "string",
                    "description": "pywinauto key string, e.g. '^a', '{ENTER}', '^l'.",
                },
                "timeout": {"type": "number"},
            },
            "required": ["action"],
        }

    def __init__(self, session: UiaSession | None = None) -> None:
        self._uia = session or UiaSession()

    # ----------------------------------------------------------------

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        if action not in _ACTIONS:
            return {"status": "error", "error": f"action must be one of {list(_ACTIONS)}"}

        window = arguments.get("window")
        pid = arguments.get("pid") if isinstance(arguments.get("pid"), int) else None
        ref = arguments.get("ref") if isinstance(arguments.get("ref"), int) else None
        name = arguments.get("name")

        try:
            if action == "tree":
                title, controls = self._uia.tree(window, pid)
                return {
                    "status": "success",
                    "window": title,
                    "count": len(controls),
                    "controls": [c.as_dict() for c in controls],
                }

            if action == "click":
                out = self._uia.click(ref, name)
                return {"status": "success", "clicked": out or name or ref}

            if action == "invoke":
                out = self._uia.invoke(ref, name)
                return {"status": "success", "invoked": out or name or ref}

            if action == "type":
                text = arguments.get("text")
                if not isinstance(text, str):
                    return {"status": "error", "error": "'type' needs 'text'."}
                into = arguments.get("into")
                into = into if isinstance(into, int) else None
                self._uia.type_text(text, into, name if into is None else None)
                return {"status": "success", "typed": text}

            if action == "key":
                keys = arguments.get("keys")
                if not isinstance(keys, str) or not keys:
                    return {"status": "error", "error": "'key' needs 'keys'."}
                self._uia.send_key(keys)
                return {"status": "success", "keys": keys}

            if action == "get":
                return {"status": "success", "text": self._uia.get_text(ref, name)}

            if action == "exists":
                if not isinstance(name, str):
                    return {"status": "error", "error": "'exists' needs 'name'."}
                return {"status": "success", "exists": self._uia.exists(name, window)}

            if action == "wait_for":
                if not isinstance(name, str):
                    return {"status": "error", "error": "'wait_for' needs 'name'."}
                found = self._uia.wait_for(
                    name, window, float(arguments.get("timeout", 10.0))
                )
                return {"status": "success", "found": found}

            return {"status": "error", "error": "unhandled action"}

        except UiaError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
