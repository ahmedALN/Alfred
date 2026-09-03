"""The window the interface lives in.

pywebview insists on owning the main thread, and Alfred's main thread
is an asyncio loop that is busy being Alfred. So the window is its own
process: Alfred serves the page, and a second process draws it.

That split buys the thing that was asked for - closing only hides. The
window process stays alive with the page still loaded and the socket
still open, so the second opening is instant instead of a cold start.
Asking for it again just shows what is already there.

Nothing is passed to this process but a URL. The token is in it, and
the window has no other way in, so the interface cannot be opened by
anything that has not been told the key.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

TITLE = "Alfred"

# Its own mark rather than the Python interpreter's, which is what a
# pywebview window inherits otherwise - so the taskbar showed a Python
# logo for something that is not, to the person using it, Python.
ICON = Path(__file__).resolve().parent.parent.parent / "assets" / "alfred.ico"


_window: Any = None


def _apply_taskbar_icon() -> None:
    """Belt-and-suspenders on top of the ``icon=`` passed to
    webview.start() below: pywebview's own docs claim icon support is
    "GTK/QT only", even though its WinForms backend's own source reads
    and applies it regardless - one undocumented code path is not
    something to hang the whole fix on. Sets it directly instead, the
    same WM_SETICON call any Win32 window answers to, found the same
    way opener.py already finds this exact window (by its exact
    title) to hide and show it."""
    if sys.platform != "win32" or not ICON.exists():
        return
    try:
        import win32api
        import win32con
        import win32gui

        hwnd = win32gui.FindWindow(None, TITLE)
        if not hwnd:
            return

        loadflags = win32con.LR_LOADFROMFILE
        big = win32gui.LoadImage(
            0, str(ICON), win32con.IMAGE_ICON, 0, 0,
            loadflags | win32con.LR_DEFAULTSIZE,
        )
        small = win32gui.LoadImage(
            0, str(ICON), win32con.IMAGE_ICON,
            win32api.GetSystemMetrics(win32con.SM_CXSMICON),
            win32api.GetSystemMetrics(win32con.SM_CYSMICON),
            loadflags,
        )
        if big:
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, 1, big)
        if small:
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, 0, small)
    except Exception:  # noqa: BLE001
        pass  # the icon= kwarg below is still a real attempt on its own


class Api:
    """What the page can ask the window to do.

    Deliberately tiny, and deliberately holding no references.

    pywebview builds its JavaScript bridge by walking this object's
    attributes. The first version kept the pywebview Window here as
    self.window, so that walk descended into WinForms and from there
    into WebView2's COM interfaces - thousands of cross-thread property
    reads, most of them throwing, and finally "maximum recursion depth
    exceeded". All of it on the UI thread, which is why the window
    painted perfectly and then never processed another message: the
    title bar said Not Responding while the page carried on updating,
    because WebView2 renders in its own process.

    So the window lives in a module global that the bridge never sees.
    """

    def show(self) -> bool:
        if _window is None:
            return False
        try:
            _window.show()
            return True
        except Exception:  # noqa: BLE001
            return False

    def hide(self) -> bool:
        if _window is None:
            return False
        try:
            _window.hide()
            return True
        except Exception:  # noqa: BLE001
            return False


def run(url: str) -> int:
    """Open the window and stay until the process is killed."""
    try:
        import webview
    except ImportError:
        print("pywebview is not installed: pip install pywebview")
        return 2

    global _window

    api = Api()
    window = webview.create_window(
        TITLE,
        url,
        js_api=api,
        width=1280,
        height=820,
        min_size=(940, 620),
        background_color="#03060C",
        # No native frame would look better and cost the ability to
        # move the window, which matters more on a display you are
        # meant to glance at beside other things.
        frameless=False,
        easy_drag=False,
    )
    _window = window

    def closing() -> bool:
        """Hide rather than quit, so reopening is instant."""
        try:
            window.hide()
        except Exception:  # noqa: BLE001
            return True   # could not hide, so let it close properly
        return False

    window.events.closing += closing
    window.events.shown += _apply_taskbar_icon

    # EdgeChromium is WebView2, which is present on Windows 11 and is a
    # current Chromium - so the canvas, the backdrop filters and the
    # web audio all behave as they do in a real browser.
    start: dict[str, Any] = {
        "gui": "edgechromium" if sys.platform == "win32" else None,
        "private_mode": False,
    }
    if ICON.exists():
        start["icon"] = str(ICON)

    webview.start(**start)
    return 0
