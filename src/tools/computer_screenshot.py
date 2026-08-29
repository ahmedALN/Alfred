from __future__ import annotations

from typing import Any

from src.ai.providers.base import ProviderError, VisionProvider
from src.ai.vision import annotate_grid, screenshot_prompt
from src.config import load_settings
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
        grid: bool | None = None,
    ) -> None:
        self._client = client
        self._vision = vision
        self._grid = load_settings().desktop_grid if grid is None else grid

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

            image = screenshot.png_bytes
            if self._grid:
                image = annotate_grid(image)

            analysis = self._vision.analyze(
                image,
                screenshot_prompt(
                    screenshot.width, screenshot.height,
                    isolated=True, gridded=self._grid,
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
