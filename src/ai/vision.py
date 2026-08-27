from __future__ import annotations

import asyncio

from google import genai

from src.config import load_settings


class VisionAnalysisError(RuntimeError):
    """Raised when Gemini vision analysis fails."""


class VisionAnalyzer:
    """
    Deterministic screenshot analyzer.

    Uses the Gemini Interactions API rather than the Live API
    realtime-video path.
    """

    MODEL = "gemini-3.7-flash"

    def __init__(self) -> None:
        settings = load_settings()

        self._client = genai.Client(
            api_key=settings.gemini_api_key,
        )

    def analyze(
        self,
        image_bytes: bytes,
        width: int,
        height: int,
    ) -> str:
        if not image_bytes:
            raise VisionAnalysisError(
                "Screenshot image is empty."
            )

        prompt = (
            "Inspect this exact Windows desktop screenshot. "
            "Only describe things that are visibly present "
            "in this image. "
            "Do not rely on previous screenshots or assume "
            "what applications should be open. "
            "Identify the foreground application, other "
            "visible application windows, dialogs, and the "
            "applications visible on the taskbar. "
            f"The screenshot resolution is {width}x{height}. "
            "Be precise and concise."
        )

        try:
            interaction = (
                self._client.interactions.create(
                    model=self.MODEL,
                    input=[
                        {
                            "type": "text",
                            "text": prompt,
                        },
                        {
                            "type": "image",
                            "mime_type": "image/png",
                            "data": (
                                __import__("base64")
                                .b64encode(image_bytes)
                                .decode("ascii")
                            ),
                        },
                    ],
                )
            )
        except Exception as exc:
            raise VisionAnalysisError(
                f"Gemini vision request failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        text = (
            getattr(
                interaction,
                "output_text",
                None,
            )
            or ""
        ).strip()

        if not text:
            raise VisionAnalysisError(
                "Gemini vision returned no textual analysis."
            )

        return text

    async def analyze_async(
        self,
        image_bytes: bytes,
        width: int,
        height: int,
    ) -> str:
        return await asyncio.to_thread(
            self.analyze,
            image_bytes,
            width,
            height,
        )
