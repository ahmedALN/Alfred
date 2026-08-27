from __future__ import annotations

import base64
from typing import Any

from google import genai

from src.config import load_settings
from src.tools.base import AlfredTool
from src.windows.child_session import (
    ChildSessionClient,
    ChildSessionError,
)


class ComputerScreenshotTool(AlfredTool):
    """
    Capture and deterministically analyze the complete
    Alfred child-session desktop.
    """

    name = "computer_screenshot"

    description = (
        "Capture the complete current desktop inside Alfred's "
        "isolated child Windows session and analyze exactly "
        "what is visibly present. Use this when visual "
        "inspection of the child desktop is needed."
    )

    VISION_MODEL = "gemini-3.5-flash-lite"

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
    ) -> None:
        self._client = client

        settings = load_settings()

        self._vision_client = genai.Client(
            api_key=settings.gemini_api_key
        )

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

            analysis = self._analyze(
                screenshot.png_bytes,
                screenshot.width,
                screenshot.height,
            )

            print(
                "[Screenshot] Vision analysis received."
            )

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
            return {
                "status": "error",
                "error": str(exc),
            }

        except Exception as exc:
            return {
                "status": "error",
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }

    def _analyze(
        self,
        image_bytes: bytes,
        width: int,
        height: int,
    ) -> str:
        encoded_image = base64.b64encode(
            image_bytes
        ).decode(
            "ascii"
        )

        prompt = (
            "Inspect this exact Windows desktop screenshot "
            "from Alfred's isolated child session. "
            f"The image resolution is {width}x{height}. "
            "Report only what is actually visible in this "
            "image. Do not infer hidden windows, previous "
            "state, or applications that are not visible. "
            "Identify the current foreground window, every "
            "other visibly open application window, visible "
            "dialogs, and important visible text. "
            "Also report the approximate screen position and "
            "size of each visible application window when "
            "that can be determined from the screenshot. "
            "Pay particular attention to PowerShell, "
            "Notepad, File Explorer, browsers, dialogs, and "
            "taskbar contents. "
            "This analysis will be used by another AI to "
            "control the desktop, so factual accuracy is more "
            "important than verbosity."
        )

        interaction = (
            self._vision_client.interactions.create(
                model=self.VISION_MODEL,
                input=[
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image",
                        "data": encoded_image,
                        "mime_type": "image/png",
                    },
                ],
                timeout=30,
            )
        )

        output_text = getattr(
            interaction,
            "output_text",
            None,
        )

        if not isinstance(
            output_text,
            str,
        ):
            raise RuntimeError(
                "Vision model returned no text analysis."
            )

        output_text = output_text.strip()

        if not output_text:
            raise RuntimeError(
                "Vision model returned an empty analysis."
            )

        return output_text
