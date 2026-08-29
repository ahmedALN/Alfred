from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from src.ai.providers.base import ProviderError, VisionProvider
from src.ai.vision import screenshot_prompt
from src.config import load_settings
from src.tools.base import AlfredTool
from src.windows.child_session import ChildSessionClient, ChildSessionError
from src.windows.desktops import DesktopManager
from src.windows.system_probe import idle_seconds

_ACTIONS = (
    "look",
    "move",
    "click",
    "double_click",
    "right_click",
    "middle_click",
    "type",
    "key",
    "scroll",
    "drag",
    "activate",
    "wait",
    "wait_for",
)

# Don't yank focus away while the user is actively typing/clicking.
_USER_ACTIVE_SECONDS = 2.5


class DesktopControlTool(AlfredTool):
    """
    See and control Alfred's own desktop (Windows virtual desktop 2).

    Alfred's apps live on that desktop, so it never covers the user's
    work. Pointer and key actions briefly switch focus there and back
    (~100 ms); a whole sequence sent in one turn only flickers once.
    """

    name = "desktop_control"

    description = (
        "See and control Alfred's own desktop (separate from the user's). "
        "action='look' returns a screenshot analysis with pixel "
        "coordinates of windows and controls - always look before acting, "
        "and look again afterwards to confirm. Other actions: move, click, "
        "double_click, right_click, middle_click (need x,y); type (needs "
        "text); key (needs keys, e.g. 'ctrl+s' or ['enter']); scroll "
        "(x,y,dy - negative dy scrolls down); drag (x1,y1,x2,y2); activate "
        "(hwnd); wait (seconds); wait_for (text,timeout). Do a full "
        "sequence of actions in ONE turn - the screen only flickers once. "
        "If an action is deferred because the user is busy, try again."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "x2": {"type": "integer"},
                "y2": {"type": "integer"},
                "text": {"type": "string"},
                "keys": {
                    "type": "string",
                    "description": "e.g. 'ctrl+s', 'enter', 'alt+f4'",
                },
                "dy": {"type": "integer"},
                "hwnd": {"type": "integer"},
                "seconds": {"type": "number"},
                "timeout": {"type": "number"},
                "force": {
                    "type": "boolean",
                    "description": "Act even if the user seems busy.",
                },
            },
            "required": ["action"],
        }

    def __init__(
        self,
        client: ChildSessionClient,
        vision: VisionProvider,
        desktop_manager: DesktopManager | None = None,
        alfred_desktop: int | None = None,
    ) -> None:
        self._client = client
        self._vision = vision
        self._desktops = desktop_manager or DesktopManager()
        self._alfred_desktop = (
            alfred_desktop
            if alfred_desktop is not None
            else load_settings().default_desktop
        )

    # ----------------------------------------------------------------

    @contextmanager
    def _focus_borrow(self, force: bool = False):
        """Switch to Alfred's desktop for the duration, then switch back."""

        if not force and idle_seconds() < _USER_ACTIVE_SECONDS:
            raise _UserBusy()

        try:
            previous = self._desktops.current_number()
        except Exception:  # noqa: BLE001
            previous = None

        switched = False
        if previous is not None and previous != self._alfred_desktop:
            try:
                self._desktops.switch_to(self._alfred_desktop)
                switched = True
                time.sleep(0.12)
            except Exception as exc:  # noqa: BLE001
                print(f"[Desktop] could not switch desktops: {exc}")

        try:
            yield
        finally:
            if switched and previous is not None:
                try:
                    time.sleep(0.05)
                    self._desktops.switch_to(previous)
                except Exception as exc:  # noqa: BLE001
                    print(f"[Desktop] could not switch back: {exc}")

    # ----------------------------------------------------------------

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")

        if action not in _ACTIONS:
            return {
                "status": "error",
                "error": f"'action' must be one of {list(_ACTIONS)}.",
            }

        try:
            if action == "look":
                return self._look()

            if action == "wait":
                time.sleep(min(float(arguments.get("seconds", 1.0)), 20.0))
                return {"status": "success", "action": "wait"}

            if action == "wait_for":
                return self._wait_for(arguments)

            if action == "activate":
                hwnd = arguments.get("hwnd")
                if not isinstance(hwnd, int):
                    return {"status": "error", "error": "'activate' needs 'hwnd'."}
                with self._focus_borrow(bool(arguments.get("force"))):
                    self._client.activate(hwnd)
                return {"status": "success", "action": "activate", "hwnd": hwnd}

            with self._focus_borrow(bool(arguments.get("force"))):
                result = self._pointer_or_key(action, arguments)

            return result

        except _UserBusy:
            return {
                "status": "deferred",
                "reason": (
                    "The user is active right now; not stealing focus. "
                    "Retry shortly, or pass force=true if it's urgent."
                ),
            }
        except ChildSessionError as exc:
            return {
                "status": "error",
                "error": f"Desktop agent not reachable: {exc}",
            }
        except ProviderError as exc:
            return {"status": "error", "error": f"Vision failed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    # ----------------------------------------------------------------

    def _look(self) -> dict[str, Any]:
        shot = self._client.screenshot()
        analysis = self._vision.analyze(
            shot.png_bytes,
            screenshot_prompt(shot.width, shot.height, isolated=True),
        )
        return {
            "status": "success",
            "action": "look",
            "width": shot.width,
            "height": shot.height,
            "session": shot.session,
            "analysis": analysis,
        }

    def _wait_for(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("text", "")).strip().lower()
        if not target:
            return {"status": "error", "error": "'wait_for' needs 'text'."}

        deadline = time.monotonic() + min(float(args.get("timeout", 15.0)), 60.0)
        last = ""
        while time.monotonic() < deadline:
            look = self._look()
            last = look.get("analysis", "")
            if target in last.lower():
                return {"status": "success", "action": "wait_for", "found": True,
                        "analysis": last}
            time.sleep(1.0)

        return {"status": "success", "action": "wait_for", "found": False,
                "analysis": last}

    def _pointer_or_key(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == "type":
            text = args.get("text")
            if not isinstance(text, str) or not text:
                return {"status": "error", "error": "'type' needs 'text'."}
            self._client.type_text(text)
            return {"status": "success", "action": "type", "typed": text}

        if action == "key":
            keys = args.get("keys")
            if not keys:
                return {"status": "error", "error": "'key' needs 'keys'."}
            self._client.key(keys)
            return {"status": "success", "action": "key", "keys": keys}

        x, y = args.get("x"), args.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return {"status": "error",
                    "error": f"'{action}' needs integer 'x' and 'y'."}

        if action == "scroll":
            self._client.scroll(x, y, int(args.get("dy", -3)))
            return {"status": "success", "action": "scroll", "x": x, "y": y}

        if action == "drag":
            x2, y2 = args.get("x2"), args.get("y2")
            if not isinstance(x2, int) or not isinstance(y2, int):
                return {"status": "error", "error": "'drag' needs x2 and y2."}
            self._client.drag(x, y, x2, y2)
            return {"status": "success", "action": "drag"}

        self._client.mouse_move(x, y)

        if action == "move":
            return {"status": "success", "action": "move", "x": x, "y": y}

        button = {
            "click": "left", "double_click": "left",
            "right_click": "right", "middle_click": "middle",
        }[action]
        self._client.click(button)
        if action == "double_click":
            time.sleep(0.08)
            self._client.click(button)

        return {"status": "success", "action": action, "x": x, "y": y,
                "button": button}


class _UserBusy(Exception):
    pass
