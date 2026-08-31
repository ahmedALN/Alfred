"""Showing someone their own screen, from their phone.

Asking "what's on my screen" and getting a paragraph back is a poor
substitute for the picture. This takes one and sends it, in the same
chat, without going near the task agent - that route plans, executes,
verifies and reports, which is right for work and absurd for a
photograph. From asking to seeing should be a couple of seconds.

A recording is the same idea with ffmpeg doing the work. It is capped
hard: a phone is not the place to receive a ten-minute screen capture,
and WhatsApp would refuse it anyway.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

# Long enough to show what happened, short enough to arrive.
MAX_SECONDS = 30
DEFAULT_SECONDS = 8


# Omitting ffmpeg means "go and find it"; passing None means "there
# isn't one" - two different things that a plain default cannot tell
# apart.
_FIND = object()


class ScreenShare:
    def __init__(self, screenshot, send_file, ffmpeg=_FIND) -> None:
        self._screenshot = screenshot
        self._send_file = send_file
        self._ffmpeg = shutil.which("ffmpeg") if ffmpeg is _FIND else ffmpeg

    # ------------------------------------------------------------ picture

    def picture(self, caption: str = "") -> str:
        """Grab the screen and send it. Returns what to say back."""
        try:
            png = self._screenshot()
        except Exception as exc:  # noqa: BLE001
            return f"I couldn't grab the screen: {exc}"

        if not png:
            return "I couldn't grab the screen - nothing came back."

        # Stamped with the moment it was taken. A picture of a screen
        # looks the same whether it was captured now or an hour ago, and
        # "is this current?" is not a question anybody should have to
        # ask twice.
        stamp = time.strftime("%H:%M:%S")
        if self._send_file(png, "image", caption or f"Taken at {stamp}"):
            return ""          # the picture IS the reply
        return "I took it but couldn't send it."

    # ------------------------------------------------------------ clip

    @property
    def can_record(self) -> bool:
        return bool(self._ffmpeg)

    def clip(self, seconds: int = DEFAULT_SECONDS, caption: str = "") -> str:
        if not self._ffmpeg:
            return (
                "I can send a screenshot, but not a recording - ffmpeg "
                "isn't installed."
            )

        seconds = max(1, min(int(seconds or DEFAULT_SECONDS), MAX_SECONDS))
        out = Path(tempfile.gettempdir()) / f"alfred-screen-{int(time.time())}.mp4"

        try:
            self._record(out, seconds)
        except subprocess.TimeoutExpired:
            return "The recording didn't finish in time."
        except Exception as exc:  # noqa: BLE001
            return f"I couldn't record the screen: {exc}"

        try:
            if not out.exists() or out.stat().st_size == 0:
                return "The recording came out empty."
            stamp = time.strftime("%H:%M:%S")
            if self._send_file(
                out.read_bytes(), "video",
                caption or f"{seconds}s from {stamp}",
            ):
                return ""
            return "I recorded it but couldn't send it."
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass

    def _record(self, out: Path, seconds: int) -> None:
        subprocess.run(
            [
                self._ffmpeg, "-y",
                "-f", "gdigrab",
                "-framerate", "12",       # plenty for a screen, small file
                "-t", str(seconds),
                "-i", "desktop",
                "-vcodec", "libx264",
                "-pix_fmt", "yuv420p",    # what phones will actually play
                "-preset", "veryfast",
                "-movflags", "+faststart",
                str(out),
            ],
            capture_output=True,
            timeout=seconds + 40,
            check=True,
        )
