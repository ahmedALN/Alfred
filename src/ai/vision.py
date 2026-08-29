from __future__ import annotations

import asyncio

from src.ai.providers.base import ProviderError, VisionProvider


class VisionAnalysisError(RuntimeError):
    """Raised when screenshot analysis fails."""


def screenshot_prompt(width: int, height: int, *, isolated: bool = True) -> str:
    scope = (
        "from Alfred's isolated desktop"
        if isolated
        else "of the Windows desktop"
    )

    return (
        f"Inspect this exact screenshot {scope}. "
        f"The image resolution is {width}x{height}. "
        "Report only what is actually visible in this image. Do not infer "
        "hidden windows, previous state, or applications that are not "
        "visible. Identify the foreground window, every other visibly open "
        "application window, visible dialogs, and important visible text. "
        "For each visible window and clickable control, give its "
        "approximate pixel position (x, y) and size when that can be "
        "determined. Pay attention to buttons, text fields, menus, "
        "PowerShell, File Explorer, browsers, and the taskbar. "
        "This analysis will be used by another AI to control the desktop, "
        "so factual accuracy and precise coordinates matter more than "
        "verbosity."
    )


class VisionAnalyzer:
    """
    Thin wrapper over the configured VisionProvider that adds Alfred's
    standard screenshot prompt. Kept so callers have a stable API
    regardless of which backend (Gemini / Ollama / OpenAI-compatible)
    is doing the analysis.
    """

    def __init__(self, provider: VisionProvider) -> None:
        self._provider = provider

    def analyze(
        self,
        image_bytes: bytes,
        width: int,
        height: int,
        *,
        isolated: bool = True,
    ) -> str:
        if not image_bytes:
            raise VisionAnalysisError("Screenshot image is empty.")

        try:
            return self._provider.analyze(
                image_bytes,
                screenshot_prompt(width, height, isolated=isolated),
            )
        except ProviderError as exc:
            raise VisionAnalysisError(str(exc)) from exc

    async def analyze_async(
        self,
        image_bytes: bytes,
        width: int,
        height: int,
        *,
        isolated: bool = True,
    ) -> str:
        return await asyncio.to_thread(
            self.analyze, image_bytes, width, height, isolated=isolated
        )
