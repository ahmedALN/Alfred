from __future__ import annotations

import asyncio
import io

from src.ai.providers.base import ProviderError, VisionProvider


class VisionAnalysisError(RuntimeError):
    """Raised when screenshot analysis fails."""


def annotate_grid(png_bytes: bytes, spacing: int = 100) -> bytes:
    """
    Overlay a faint pixel-coordinate grid on a screenshot. Small vision
    models are much better at "the button is near 640, 380" than at
    estimating raw coordinates, and the labels ARE the real click
    coordinates. Returns the original bytes unchanged if Pillow is
    missing or anything goes wrong.
    """

    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return png_bytes

    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img, "RGBA")
        w, h = img.size

        line = (255, 0, 0, 70)
        label_bg = (0, 0, 0, 140)
        label_fg = (255, 255, 0, 255)

        for x in range(spacing, w, spacing):
            draw.line([(x, 0), (x, h)], fill=line, width=1)
        for y in range(spacing, h, spacing):
            draw.line([(0, y), (w, y)], fill=line, width=1)

        for x in range(spacing, w, spacing):
            for y in range(spacing, h, spacing):
                text = f"{x},{y}"
                draw.rectangle([x + 1, y + 1, x + 8 * len(text), y + 12],
                               fill=label_bg)
                draw.text((x + 2, y + 1), text, fill=label_fg)

        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return png_bytes


def screenshot_prompt(
    width: int, height: int, *, isolated: bool = True, gridded: bool = False
) -> str:
    scope = (
        "from Alfred's isolated desktop"
        if isolated
        else "of the Windows desktop"
    )

    grid_note = (
        "A red grid is drawn on the image with yellow labels like "
        "'700,500' at every line crossing. Those labels are exact screen "
        "pixel coordinates. Read every position by comparing to the "
        "nearest labels. "
        if gridded else ""
    )

    return (
        f"Inspect this exact screenshot {scope}. "
        f"The image is {width} pixels wide and {height} tall. "
        f"{grid_note}"
        "Describe only what is visible. Do NOT return JSON, arrays, or "
        "bounding boxes. Write plain lines, one per item:\n"
        "  <name> - center at (X, Y)\n"
        "where X and Y are screen pixel coordinates"
        + (" read off the grid labels" if gridded else "") + ". "
        "Cover: the foreground window and its title bar buttons, every "
        "other visible window, dialogs, buttons, text fields, menus, "
        "links, and the taskbar. Also state the foreground window's "
        "title. Accuracy of the (X, Y) values matters most - another "
        "program will click exactly there."
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
