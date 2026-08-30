"""Listing top-level windows cheaply.

pywinauto's accessibility walk costs hundreds of milliseconds and wakes
apps up. Answering "what windows exist right now" needs none of that.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32

_ENUM = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
)


def ordered_titles() -> list[str]:
    """Visible top-level window titles, front-most first.

    EnumWindows walks in Z-order, so position in this list is how near
    the front a window is - which is the best available answer to "which
    of these did the user mean" when several match.
    """
    found: list[str] = []

    def visit(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        text = buffer.value.strip()
        if text and text not in found:
            found.append(text)
        return True

    try:
        _user32.EnumWindows(_ENUM(visit), 0)
    except Exception:  # noqa: BLE001
        return found

    return found


def titles() -> set[str]:
    """Every visible top-level window title, right now."""
    return set(ordered_titles())
