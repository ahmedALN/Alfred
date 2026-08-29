from __future__ import annotations

import re
from typing import Any

from src.tools.base import AlfredTool
from src.windows.uia import UiaError, UiaSession

_ACTIONS = (
    "windows", "focus", "tree", "find", "click", "double_click",
    "right_click", "invoke", "type", "key", "get", "select", "expand",
    "scroll", "menu", "wait_for", "wait_ready", "exists",
)

# Field names that mean "secret" - Alfred never types into these.
_SECRET_NAME = re.compile(
    r"\b(password|passwd|pwd|passcode|pass\s*phrase|passphrase|pin|"
    r"security\s*code|secret|cvv|cvc|card\s*number|otp|"
    r"one[-\s]?time\s*code|2fa|authenticator|recovery\s*key|"
    r"private\s*key|api\s*key|token)\b",
    re.I,
)

_SECRET_REFUSAL = (
    "Alfred will not type credentials. Tell the user the sign-in screen "
    "is ready and ask them to enter it themselves (or use their password "
    "manager); once they say they're signed in, carry on with the rest of "
    "the task."
)


class UIControlTool(AlfredTool):
    """
    Control real Windows apps through the accessibility layer - reads
    buttons/fields/menu items by name and drives them exactly. No
    screenshots, no guessed coordinates. This is the right tool for
    Spotify, browsers, File Explorer, Settings, Office, launchers and
    most game menus.
    """

    name = "ui_control"

    description = (
        "Drive a Windows app precisely via its accessibility tree - the "
        "main way to do work INSIDE an app. Typical flow: 'wait_ready' "
        "after launching, 'tree' to see the controls, then click / type / "
        "select by ref or name, then 'get' to read the result back. "
        "Actions: windows (list open windows); focus (bring a window "
        "forward); tree window= [contains=] (list controls, each with a "
        "ref); find query= (search the current window's controls); click / "
        "double_click / right_click / invoke ref=|name=; type text= "
        "[into=ref|name=]; key keys= (e.g. '^l' Ctrl+L, '{ENTER}', "
        "'{ESC}', '{TAB}'); get ref=|name= (read a control's text); select "
        "item= ref=|name= (combo box / list / tab); expand ref=|name=; "
        "scroll direction= [amount=] ; menu path='File->Save As'; "
        "wait_ready window= [timeout=] (wait for a just-launched app to "
        "become usable); wait_for name= [timeout=]; exists name=. "
        "Prefer this over desktop_control - it is exact. Alfred refuses to "
        "type into password fields."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "window": {
                    "type": "string",
                    "description": "Window title substring (or regex).",
                },
                "pid": {"type": "integer"},
                "ref": {"type": "integer", "description": "Control ref from 'tree'."},
                "name": {"type": "string", "description": "Control name to match."},
                "text": {"type": "string", "description": "Text to type."},
                "into": {
                    "type": "integer",
                    "description": "ref of the field to type into.",
                },
                "item": {
                    "type": "string",
                    "description": "Item to pick, for 'select'.",
                },
                "query": {
                    "type": "string",
                    "description": "Search text, for 'find'.",
                },
                "contains": {
                    "type": "string",
                    "description": "Only list controls mentioning this, for 'tree'.",
                },
                "path": {
                    "type": "string",
                    "description": "Menu path, e.g. 'File->Save As'.",
                },
                "keys": {
                    "type": "string",
                    "description": "Key string, e.g. '^a', '{ENTER}', '%{F4}'.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                },
                "amount": {"type": "integer"},
                "timeout": {"type": "number"},
            },
            "required": ["action"],
        }

    def __init__(self, session: UiaSession | None = None) -> None:
        self._uia = session or UiaSession()

    # ----------------------------------------------------------------

    def _secret_guard(self, ref: int | None, name: str | None,
                      text: str) -> dict[str, Any] | None:
        """Refuse to type into a masked or obviously-secret field."""
        info = None
        try:
            info = self._uia.control_info(ref, name)
        except Exception:  # noqa: BLE001
            info = None

        if info is not None and getattr(info, "is_password", False):
            return {
                "status": "refused",
                "error": "that is a password field",
                "instruction": _SECRET_REFUSAL,
            }

        target_name = (getattr(info, "name", "") or name or "")
        if target_name and _SECRET_NAME.search(target_name):
            return {
                "status": "refused",
                "error": f"the field {target_name!r} asks for a secret",
                "instruction": _SECRET_REFUSAL,
            }

        return None

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        if action not in _ACTIONS:
            return {
                "status": "error",
                "error": f"action must be one of {list(_ACTIONS)}",
            }

        window = arguments.get("window")
        pid = arguments.get("pid") if isinstance(arguments.get("pid"), int) else None
        ref = arguments.get("ref") if isinstance(arguments.get("ref"), int) else None
        name = arguments.get("name")
        timeout = arguments.get("timeout")
        timeout = float(timeout) if isinstance(timeout, (int, float)) else None

        try:
            if action == "windows":
                wins = self._uia.windows()
                return {"status": "success", "count": len(wins), "windows": wins}

            if action == "focus":
                title = self._uia.focus_window(window, pid)
                return {"status": "success", "focused": title or window}

            if action == "tree":
                title, controls = self._uia.tree(
                    window, pid, contains=arguments.get("contains"),
                )
                return {
                    "status": "success",
                    "window": title,
                    "count": len(controls),
                    "controls": [c.as_dict() for c in controls],
                }

            if action == "find":
                query = arguments.get("query") or name
                if not isinstance(query, str) or not query:
                    return {"status": "error", "error": "'find' needs 'query'."}
                hits = self._uia.find(query)
                return {
                    "status": "success",
                    "count": len(hits),
                    "controls": [c.as_dict() for c in hits],
                }

            if action in ("click", "double_click", "right_click"):
                out = self._uia.click(
                    ref, name,
                    double=(action == "double_click"),
                    right=(action == "right_click"),
                )
                return {"status": "success", "clicked": out or name or ref}

            if action == "invoke":
                out = self._uia.invoke(ref, name)
                return {"status": "success", "invoked": out or name or ref}

            if action == "type":
                text = arguments.get("text")
                if not isinstance(text, str):
                    return {"status": "error", "error": "'type' needs 'text'."}
                into = arguments.get("into")
                into = into if isinstance(into, int) else ref
                refused = self._secret_guard(into, name, text)
                if refused is not None:
                    return refused
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

            if action == "select":
                item = arguments.get("item")
                if not isinstance(item, str) or not item:
                    return {"status": "error", "error": "'select' needs 'item'."}
                return {
                    "status": "success",
                    "selected": self._uia.select(item, ref, name),
                }

            if action == "expand":
                return {
                    "status": "success",
                    "expanded": self._uia.expand(ref, name),
                }

            if action == "scroll":
                return {
                    "status": "success",
                    "scrolled": self._uia.scroll(
                        str(arguments.get("direction", "down")),
                        int(arguments.get("amount", 3) or 3),
                        ref, name,
                    ),
                }

            if action == "menu":
                path = arguments.get("path")
                if not isinstance(path, str) or not path:
                    return {"status": "error", "error": "'menu' needs 'path'."}
                return {"status": "success", "menu": self._uia.menu_select(path)}

            if action == "exists":
                if not isinstance(name, str):
                    return {"status": "error", "error": "'exists' needs 'name'."}
                return {
                    "status": "success",
                    "exists": self._uia.exists(name, window),
                }

            if action == "wait_for":
                if not isinstance(name, str):
                    return {"status": "error", "error": "'wait_for' needs 'name'."}
                found = self._uia.wait_for(name, window, timeout or 10.0)
                return {"status": "success", "found": found}

            if action == "wait_ready":
                ready = self._uia.wait_ready(window, pid, timeout or 25.0)
                return {
                    "status": "success" if ready else "error",
                    "ready": ready,
                    **({} if ready else {
                        "error": f"{window or 'the window'} did not become "
                                 "usable in time",
                    }),
                }

            return {"status": "error", "error": "unhandled action"}

        except UiaError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
