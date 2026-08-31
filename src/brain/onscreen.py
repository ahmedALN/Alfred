"""What is already on the screen.

Every task began by finding out what any glance would have told it.
Asked to type into a Notepad that was open and in front, Alfred's first
move was to open Notepad - and that is not the model being stupid, it is
the model being told nothing. The executor's prompt carried the goal,
the plan, the tools and the history of the current task, and not one
word about the machine it was working on.

So it starts with a look. Which windows are open, which one is in front,
what each of them is. It costs a few milliseconds and it removes a whole
class of opening move: launching what is running, waiting for what is
already up, reading a tree to find an app that is in front of you.

Cached for a moment, because a plan and its first few steps all happen
inside a second or two and the answer does not change in that time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Windows that are always there and never what anybody means.
_FURNITURE = {
    "Program Manager", "Windows Input Experience", "Taskbar",
    "Search", "Start", "Windows Shell Experience Host",
}

_HOW_LONG = 2.0


@dataclass
class Screen:
    focused: str = ""
    focused_app: str = ""
    windows: list[tuple[str, str]] = field(default_factory=list)

    def running(self, app: str) -> bool:
        want = app.strip().lower()
        if not want:
            return False
        return any(
            want in name.lower() or want in title.lower()
            for name, title in self.windows
        )

    def brief(self, limit: int = 10) -> str:
        """Short enough to sit in every prompt without crowding it."""
        if not self.windows:
            return ""

        lines = []
        if self.focused:
            lines.append(
                f"In front: {self.focused[:70]}"
                + (f" ({self.focused_app})" if self.focused_app else "")
            )

        others = [
            f"{app}: {title[:52]}" if title else app
            for app, title in self.windows[:limit]
            if title != self.focused
        ]
        if others:
            lines.append("Also open: " + "; ".join(others))

        return "\n".join(lines)


_last: tuple[float, Screen] | None = None


def look(fresh: bool = False) -> Screen:
    global _last

    now = time.monotonic()
    if not fresh and _last is not None and (now - _last[0]) < _HOW_LONG:
        return _last[1]

    screen = _read()
    _last = (now, screen)
    return screen


def forget() -> None:
    """Drop the cached look. For after something has been opened or
    closed and the next question deserves a fresh answer."""
    global _last
    _last = None


def _read() -> Screen:
    try:
        import psutil
        import win32gui
        import win32process
    except Exception:  # noqa: BLE001
        return Screen()

    found: list[tuple[str, str]] = []
    names: dict[int, str] = {}

    def visit(hwnd: int, _extra) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title or title in _FURNITURE:
                return True
            # A window with no size is not on screen in any sense that
            # matters - tooltips, hidden hosts, message-only windows.
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if (right - left) < 200 or (bottom - top) < 120:
                return True

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in names:
                try:
                    name = psutil.Process(pid).name()
                    names[pid] = name[:-4] if name.lower().endswith(".exe") else name
                except Exception:  # noqa: BLE001
                    names[pid] = ""
            found.append((names[pid], title))
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:  # noqa: BLE001
        return Screen()

    focused, focused_app = "", ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            focused = (win32gui.GetWindowText(hwnd) or "").strip()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            focused_app = names.get(pid, "")
            if not focused_app:
                name = psutil.Process(pid).name()
                focused_app = name[:-4] if name.lower().endswith(".exe") else name
    except Exception:  # noqa: BLE001
        pass

    return Screen(focused=focused, focused_app=focused_app, windows=found)
