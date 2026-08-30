import pytest

from src.tools.desktop_control import DesktopControlTool
from src.windows.child_session import ChildSessionError


class FakeScreenshot:
    png_bytes = b"\x89PNG fake"
    width = 1920
    height = 1080
    session = 2
    mime_type = "image/png"


class FakeClient:
    def __init__(self, broken=False):
        self.broken = broken
        self.calls = []

    def _g(self):
        if self.broken:
            raise ChildSessionError("agent not running")

    def screenshot(self):
        self._g()
        self.calls.append(("screenshot",))
        return FakeScreenshot()

    def capture_window(self, hwnd):
        self._g()
        self.calls.append(("capture_window", hwnd))
        return {"started": True}

    def mouse_move(self, x, y):
        self._g(); self.calls.append(("mouse_move", x, y))

    def click(self, button="left"):
        self._g(); self.calls.append(("click", button))

    def type_text(self, text):
        self._g(); self.calls.append(("type", text))

    def key(self, keys):
        self._g(); self.calls.append(("key", keys))

    def scroll(self, x, y, dy):
        self._g(); self.calls.append(("scroll", x, y, dy))

    def drag(self, x1, y1, x2, y2):
        self._g(); self.calls.append(("drag", x1, y1, x2, y2))

    def activate(self, hwnd):
        self._g(); self.calls.append(("activate", hwnd))


class FakeDesktops:
    def __init__(self, current=1):
        self._current = current
        self.switches = []

    def current_number(self):
        return self._current

    def switch_to(self, n):
        self.switches.append(n)
        self._current = n


class FakeVision:
    name = "fake"
    model = "fake"

    def analyze(self, image_bytes, prompt, *, mime_type="image/png"):
        return "Notepad at (100,200). OK button at (450,500)."


@pytest.fixture()
def tool(monkeypatch):
    # user is idle by default -> focus-borrow allowed
    monkeypatch.setattr("src.tools.desktop_control.idle_seconds", lambda: 60.0)
    client = FakeClient()
    desks = FakeDesktops(current=1)
    t = DesktopControlTool(client, FakeVision(), desktop_manager=desks, alfred_desktop=2)
    return t, client, desks


def test_look_no_focus_switch(tool):
    t, client, desks = tool
    out = t.execute({"action": "look"})
    assert out["status"] == "success" and "Notepad" in out["analysis"]
    assert desks.switches == []  # look never switches desktops


def test_look_with_hwnd_captures_that_window(tool):
    t, client, desks = tool
    out = t.execute({"action": "look", "hwnd": 12345})
    assert out["status"] == "success"
    assert ("capture_window", 12345) in client.calls
    assert desks.switches == []  # no flicker


def test_click_borrows_focus_and_returns(tool):
    t, client, desks = tool
    out = t.execute({"action": "click", "x": 450, "y": 500})
    assert out["status"] == "success"
    assert desks.switches == [2, 1]  # to Alfred's desktop and back
    assert ("mouse_move", 450, 500) in client.calls
    assert ("click", "left") in client.calls


def test_key_combo(tool):
    t, client, _ = tool
    t.execute({"action": "key", "keys": "ctrl+s"})
    assert ("key", "ctrl+s") in client.calls


def test_scroll_and_drag(tool):
    t, client, _ = tool
    t.execute({"action": "scroll", "x": 10, "y": 20, "dy": -5})
    t.execute({"action": "drag", "x": 1, "y": 2, "x2": 3, "y2": 4})
    assert ("scroll", 10, 20, -5) in client.calls
    assert ("drag", 1, 2, 3, 4) in client.calls


def test_deferred_when_user_active(monkeypatch):
    monkeypatch.setattr("src.tools.desktop_control.idle_seconds", lambda: 0.5)
    client = FakeClient()
    desks = FakeDesktops()
    t = DesktopControlTool(client, FakeVision(), desktop_manager=desks, alfred_desktop=2)

    out = t.execute({"action": "click", "x": 1, "y": 1})
    assert out["status"] == "deferred"
    assert client.calls == []  # nothing happened

    # force overrides
    out = t.execute({"action": "click", "x": 1, "y": 1, "force": True})
    assert out["status"] == "success"


def test_wait(tool):
    t, _, _ = tool
    out = t.execute({"action": "wait", "seconds": 0.01})
    assert out["status"] == "success"


def test_unreachable_agent(monkeypatch):
    monkeypatch.setattr("src.tools.desktop_control.idle_seconds", lambda: 60.0)
    t = DesktopControlTool(FakeClient(broken=True), FakeVision(),
                           desktop_manager=FakeDesktops(), alfred_desktop=2)
    out = t.execute({"action": "look"})
    assert out["status"] == "error" and "not reachable" in out["error"]


# ------------------------------------------------- isolated session


class _Router:
    def __init__(self, isolated):
        self.isolated = isolated


def test_actions_are_never_deferred_on_alfreds_own_desktop(monkeypatch):
    """Alfred's session has its own input queue - typing there cannot
    touch the user's screen, so 'the user is active' must not block it.
    Deferring would defeat the entire point of the isolated desktop."""
    import src.tools.desktop_control as dc

    monkeypatch.setattr(dc, "idle_seconds", lambda: 0.0)  # user is busy

    client = FakeClient()
    tool = dc.DesktopControlTool(
        client, FakeVision(), desktop_manager=FakeDesktops(current=1),
        alfred_desktop=2, router=_Router(isolated=True),
    )

    out = tool.execute({"action": "type", "text": "hello"})

    assert out["status"] == "success"
    assert ("type", "hello") in client.calls


def test_actions_are_still_deferred_on_the_users_own_desktop(monkeypatch):
    import src.tools.desktop_control as dc

    monkeypatch.setattr(dc, "idle_seconds", lambda: 0.0)

    tool = dc.DesktopControlTool(
        FakeClient(), FakeVision(), desktop_manager=FakeDesktops(current=1),
        alfred_desktop=2, router=_Router(isolated=False),
    )

    out = tool.execute({"action": "type", "text": "hello"})

    assert out["status"] == "deferred"
