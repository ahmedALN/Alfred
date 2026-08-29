from __future__ import annotations

import time
from typing import Any

from src.ai.providers.base import ProviderError, VisionProvider
from src.ai.vision import screenshot_prompt
from src.tools.base import AlfredTool
from src.windows.child_session import ChildSessionClient, ChildSessionError

_ACTIONS = (
    "look",
    "move",
    "click",
    "double_click",
    "right_click",
    "middle_click",
    "type",
    "activate",
)


class DesktopControlTool(AlfredTool):
    """
    Mouse + keyboard control of the isolated desktop Alfred owns, plus
    'look' to see it. Coordinates are pixels from the top-left of the
    image returned by 'look'.
    """

    name = "desktop_control"

    description = (
        "See and control Alfred's isolated desktop: move/click the mouse, "
        "type text, focus a window. Always call action='look' first to get "
        "a fresh screenshot analysis with the pixel coordinates of windows "
        "and controls, then act on those coordinates. After a click or "
        "keystroke that should change the screen, call 'look' again to "
        "confirm what happened. This desktop is separate from the user's, "
        "so controlling it does not disturb them."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": (
                        "'look' captures + analyzes the desktop. "
                        "'move'/'click'/'double_click'/'right_click'/"
                        "'middle_click' need x and y. 'type' needs text. "
                        "'activate' needs hwnd."
                    ),
                },
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "text": {"type": "string"},
                "hwnd": {"type": "integer"},
                "then_look": {
                    "type": "boolean",
                    "description": (
                        "If true, capture and analyze the desktop again "
                        "after the action and return that too."
                    ),
                },
            },
            "required": ["action"],
        }

    def __init__(
        self,
        client: ChildSessionClient,
        vision: VisionProvider,
    ) -> None:
        self._client = client
        self._vision = vision

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

            result = self._act(action, arguments)

            if arguments.get("then_look"):
                time.sleep(0.4)
                look = self._look()
                result["desktop_after"] = look.get("analysis")

            return result

        except ChildSessionError as exc:
            return {
                "status": "error",
                "error": (
                    f"Alfred's desktop agent is not reachable: {exc}. "
                    "The ChildInputAgent may not be running."
                ),
            }
        except ProviderError as exc:
            return {"status": "error", "error": f"Vision failed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

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

    def _act(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == "type":
            text = args.get("text")
            if not isinstance(text, str) or not text:
                return {"status": "error", "error": "'type' needs 'text'."}
            self._client.type_text(text)
            return {"status": "success", "action": "type", "typed": text}

        if action == "activate":
            hwnd = args.get("hwnd")
            if not isinstance(hwnd, int):
                return {"status": "error", "error": "'activate' needs 'hwnd'."}
            self._client.activate(hwnd)
            return {"status": "success", "action": "activate", "hwnd": hwnd}

        # Pointer actions.
        x, y = args.get("x"), args.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return {
                "status": "error",
                "error": f"'{action}' needs integer 'x' and 'y'.",
            }

        self._client.mouse_move(x, y)

        if action == "move":
            return {"status": "success", "action": "move", "x": x, "y": y}

        button = {
            "click": "left",
            "double_click": "left",
            "right_click": "right",
            "middle_click": "middle",
        }[action]

        self._client.click(button)

        if action == "double_click":
            time.sleep(0.08)
            self._client.click(button)

        return {
            "status": "success",
            "action": action,
            "x": x,
            "y": y,
            "button": button,
        }
