from src.tools.ui_control import UIControlTool
from src.windows.uia import Control, UiaError, _escape


class FakeUia:
    def __init__(self):
        self.calls = []
        self._controls = [
            Control(0, "Button", "Search", "", (10, 10, 40, 30), True),
            Control(1, "Edit", "Search input", "q", (50, 10, 300, 30), True),
            Control(2, "ListItem", "God's Plan - Drake", "", (0, 40, 300, 70), True),
        ]

    def tree(self, title_re=None, pid=None, limit=120):
        self.calls.append(("tree", title_re, pid))
        return "Spotify Premium", list(self._controls)

    def click(self, ref=None, name=None):
        self.calls.append(("click", ref, name))
        return "Search"

    def invoke(self, ref=None, name=None):
        self.calls.append(("invoke", ref, name))
        return "ok"

    def type_text(self, text, ref=None, name=None):
        self.calls.append(("type", text, ref, name))

    def send_key(self, keys):
        self.calls.append(("key", keys))

    def get_text(self, ref=None, name=None):
        self.calls.append(("get", ref, name))
        return "God's Plan"

    def exists(self, name, title_re=None):
        return any(name.lower() in c.name.lower() for c in self._controls)

    def wait_for(self, name, title_re=None, timeout=10.0):
        return self.exists(name)


def _tool():
    return UIControlTool(FakeUia())


def test_tree_returns_actionable_controls():
    out = _tool().execute({"action": "tree", "window": "Spotify"})
    assert out["status"] == "success"
    assert out["window"] == "Spotify Premium"
    names = {c["name"] for c in out["controls"]}
    assert "Search" in names and "God's Plan - Drake" in names
    # each control has a ref + center
    assert all("ref" in c and "center" in c for c in out["controls"])


def test_click_by_ref_and_by_name():
    t = _tool()
    t.execute({"action": "click", "ref": 0})
    t.execute({"action": "click", "name": "God's Plan"})
    assert ("click", 0, None) in t._uia.calls
    assert ("click", None, "God's Plan") in t._uia.calls


def test_type_into_a_field():
    t = _tool()
    t.execute({"action": "type", "text": "drake", "into": 1})
    assert ("type", "drake", 1, None) in t._uia.calls


def test_key_action():
    t = _tool()
    t.execute({"action": "key", "keys": "{ENTER}"})
    assert ("key", "{ENTER}") in t._uia.calls


def test_wait_for():
    out = _tool().execute({"action": "wait_for", "name": "Drake"})
    assert out["found"] is True


def test_missing_required_args():
    t = _tool()
    assert t.execute({"action": "type"})["status"] == "error"
    assert t.execute({"action": "key"})["status"] == "error"
    assert t.execute({"action": "fly"})["status"] == "error"


def test_uia_error_is_reported_cleanly():
    class Broken(FakeUia):
        def tree(self, *a, **k):
            raise UiaError("window not found: Spotify")

    out = UIControlTool(Broken()).execute({"action": "tree", "window": "Spotify"})
    assert out["status"] == "error" and "window not found" in out["error"]


def test_escape_special_keys_chars():
    assert _escape("a+b") == "a{+}b"
    assert _escape("100%") == "100{%}"
    assert _escape("plain text") == "plain text"
