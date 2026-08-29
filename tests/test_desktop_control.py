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

    def _guard(self):
        if self.broken:
            raise ChildSessionError("agent not running")

    def screenshot(self):
        self._guard()
        self.calls.append(("screenshot",))
        return FakeScreenshot()

    def mouse_move(self, x, y):
        self._guard()
        self.calls.append(("mouse_move", x, y))

    def click(self, button="left"):
        self._guard()
        self.calls.append(("click", button))

    def type_text(self, text):
        self._guard()
        self.calls.append(("type", text))

    def activate(self, hwnd):
        self._guard()
        self.calls.append(("activate", hwnd))


class FakeVision:
    name = "fake"
    model = "fake"

    def analyze(self, image_bytes, prompt, *, mime_type="image/png"):
        return "A Notepad window at (100, 200), size 800x600. OK button at (450, 500)."


def _tool(broken=False):
    client = FakeClient(broken=broken)
    return DesktopControlTool(client, FakeVision()), client


def test_look_captures_and_analyzes():
    tool, client = _tool()
    out = tool.execute({"action": "look"})

    assert out["status"] == "success"
    assert "Notepad" in out["analysis"]
    assert out["width"] == 1920
    assert client.calls == [("screenshot",)]


def test_click_moves_then_clicks():
    tool, client = _tool()
    out = tool.execute({"action": "click", "x": 450, "y": 500})

    assert out["status"] == "success"
    assert client.calls == [("mouse_move", 450, 500), ("click", "left")]


def test_double_click_clicks_twice():
    tool, client = _tool()
    tool.execute({"action": "double_click", "x": 10, "y": 20})

    assert client.calls == [
        ("mouse_move", 10, 20),
        ("click", "left"),
        ("click", "left"),
    ]


def test_right_click_uses_right_button():
    tool, client = _tool()
    tool.execute({"action": "right_click", "x": 5, "y": 5})
    assert ("click", "right") in client.calls


def test_type_sends_text():
    tool, client = _tool()
    out = tool.execute({"action": "type", "text": "hello world"})
    assert out["status"] == "success"
    assert ("type", "hello world") in client.calls


def test_click_requires_coordinates():
    tool, _ = _tool()
    out = tool.execute({"action": "click"})
    assert out["status"] == "error"
    assert "x" in out["error"]


def test_then_look_appends_followup_analysis():
    tool, client = _tool()
    out = tool.execute({"action": "click", "x": 1, "y": 2, "then_look": True})
    assert out["status"] == "success"
    assert "desktop_after" in out
    assert ("screenshot",) in client.calls


def test_unreachable_agent_is_reported_cleanly():
    tool, _ = _tool(broken=True)
    out = tool.execute({"action": "look"})
    assert out["status"] == "error"
    assert "not reachable" in out["error"]


def test_invalid_action():
    tool, _ = _tool()
    out = tool.execute({"action": "fly"})
    assert out["status"] == "error"
