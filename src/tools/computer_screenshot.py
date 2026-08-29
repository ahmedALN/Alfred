from __future__ import annotations

from typing import Any

from src.ai.providers.base import ProviderError, VisionProvider
from src.ai.vision import screenshot_prompt
from src.tools.base import AlfredTool
from src.windows.child_session import (
    ChildSessionClient,
    ChildSessionError,
)


class ComputerScreenshotTool(AlfredTool):
    """
    Capture and deterministically analyze the complete desktop Alfred
    controls (its isolated child session).
    """

    name = "computer_screenshot"

    description = (
        "Capture the complete current desktop inside Alfred's "
        "isolated child Windows session and analyze exactly "
        "what is visibly present, including approximate pixel "
        "coordinates of windows and clickable controls. Use this "
        "when visual inspection of the controlled desktop is needed, "
        "or before clicking/typing so coordinates are accurate."
    )

    @property
    def parameters_schema(
        self,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    def __init__(
        self,
        client: ChildSessionClient,
        vision: VisionProvider,
    ) -> None:
        self._client = client
        self._vision = vision

    def execute(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        del arguments

        try:
            screenshot = self._client.screenshot()

            print(
                "[Screenshot] Captured "
                f"{screenshot.width}x"
                f"{screenshot.height} "
                f"from Session {screenshot.session}."
            )

            analysis = self._vision.analyze(
                screenshot.png_bytes,
                screenshot_prompt(
                    screenshot.width, screenshot.height, isolated=True
                ),
            )

            print("[Screenshot] Vision analysis received.")

            return {
                "status": "success",
                "description": (
                    "The complete child-session desktop "
                    "was captured and analyzed."
                ),
                "width": screenshot.width,
                "height": screenshot.height,
                "session": screenshot.session,
                "mime_type": screenshot.mime_type,
                "analysis": analysis,
            }

        except ChildSessionError as exc:
            return {"status": "error", "error": str(exc)}

        except ProviderError as exc:
            return {"status": "error", "error": f"Vision analysis failed: {exc}"}

        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
