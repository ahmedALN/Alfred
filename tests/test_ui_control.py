import pytest

from src.tools.ui_control import UIControlTool
from src.windows.uia import (
    Control,
    UiaError,
    UiaSession,
    _escape,
    title_pattern,
)


class FakeUia:
    def __init__(self, controls=None):
        self.calls = []
        self._controls = controls if controls is not None else [
            Control(0, "Button", "Search", "", (10, 10, 40, 30), True),
            Control(1, "Edit", "Search input", "q", (50, 10, 300, 30), True),
            Control(2, "ListItem", "God's Plan - Drake", "", (0, 40, 300, 70), True),
            Control(3, "Edit", "Password", "pwd", (0, 80, 300, 110), True,
                    is_password=True),
            Control(4, "Edit", "Email address", "em", (0, 120, 300, 150), True),
        ]

    # --- reads -----------------------------------------------------
    def windows(self, limit=40):
        self.calls.append(("windows",))
        return [{"title": "Spotify Premium", "pid": 42, "class": "Chrome_WidgetWin_1"}]

    def focus_window(self, title_re=None, pid=None):
        self.calls.append(("focus", title_re, pid))
        return "Spotify Premium"

    def tree(self, title_re=None, pid=None, limit=80, max_depth=14, contains=None):
        self.calls.append(("tree", title_re, pid, contains))
        ctrls = list(self._controls)
        if contains:
            c = contains.lower()
            ctrls = [x for x in ctrls if c in x.name.lower()]
        return "Spotify Premium", ctrls

    def find(self, query, limit=20):
        self.calls.append(("find", query))
        q = query.lower()
        return [c for c in self._controls if q in c.name.lower()]

    def control_info(self, ref=None, name=None):
        if ref is not None and 0 <= ref < len(self._controls):
            return self._controls[ref]
        if name:
            for c in self._controls:
                if name.lower() in c.name.lower():
                    return c
        return None

    # --- actions ---------------------------------------------------
    def click(self, ref=None, name=None, double=False, right=False):
        self.calls.append(("click", ref, name, double, right))
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

    def select(self, item, ref=None, name=None):
        self.calls.append(("select", item, ref, name))
        return item

    def expand(self, ref=None, name=None):
        self.calls.append(("expand", ref, name))
        return "expanded"

    def scroll(self, direction="down", amount=3, ref=None, name=None):
        self.calls.append(("scroll", direction, amount))
        return f"scrolled {direction}"

    def menu_select(self, path):
        self.calls.append(("menu", path))
        return path

    # --- waits -----------------------------------------------------
    def exists(self, name, title_re=None):
        return any(name.lower() in c.name.lower() for c in self._controls)

    def wait_for(self, name, title_re=None, timeout=10.0):
        return self.exists(name)

    def wait_ready(self, title_re=None, pid=None, timeout=25.0, min_controls=3):
        self.calls.append(("wait_ready", title_re, timeout))
        return len(self._controls) >= min_controls


def _tool(controls=None):
    return UIControlTool(FakeUia(controls))


# ---------------------------------------------------------------- reads


def test_tree_returns_actionable_controls():
    out = _tool().execute({"action": "tree", "window": "Spotify"})
    assert out["status"] == "success"
    assert out["window"] == "Spotify Premium"
    names = {c["name"] for c in out["controls"]}
    assert "Search" in names and "God's Plan - Drake" in names
    assert all("ref" in c and "center" in c for c in out["controls"])


def test_tree_contains_filters():
    out = _tool().execute({"action": "tree", "window": "Spotify",
                           "contains": "drake"})
    assert out["count"] == 1
    assert "Drake" in out["controls"][0]["name"]


def test_windows_lists_open_windows():
    out = _tool().execute({"action": "windows"})
    assert out["status"] == "success"
    assert out["windows"][0]["title"] == "Spotify Premium"


def test_find_searches_current_window():
    out = _tool().execute({"action": "find", "query": "search"})
    assert out["status"] == "success" and out["count"] == 2


def test_password_field_is_flagged_in_the_tree():
    out = _tool().execute({"action": "tree", "window": "App"})
    pwd = [c for c in out["controls"] if c["name"] == "Password"][0]
    assert pwd["password_field"] is True
    other = [c for c in out["controls"] if c["name"] == "Search"][0]
    assert "password_field" not in other


# ---------------------------------------------------------------- actions


def test_click_by_ref_and_by_name():
    t = _tool()
    t.execute({"action": "click", "ref": 0})
    t.execute({"action": "click", "name": "God's Plan"})
    assert ("click", 0, None, False, False) in t._uia.calls
    assert ("click", None, "God's Plan", False, False) in t._uia.calls


def test_double_and_right_click():
    t = _tool()
    t.execute({"action": "double_click", "ref": 2})
    t.execute({"action": "right_click", "ref": 2})
    assert ("click", 2, None, True, False) in t._uia.calls
    assert ("click", 2, None, False, True) in t._uia.calls


def test_type_into_a_field():
    t = _tool()
    t.execute({"action": "type", "text": "drake", "into": 1})
    assert ("type", "drake", 1, None) in t._uia.calls


def test_key_select_expand_scroll_menu():
    t = _tool()
    t.execute({"action": "key", "keys": "{ENTER}"})
    t.execute({"action": "select", "item": "Playlists", "ref": 0})
    t.execute({"action": "expand", "ref": 0})
    t.execute({"action": "scroll", "direction": "down", "amount": 5})
    t.execute({"action": "menu", "path": "File->Exit"})
    kinds = {c[0] for c in t._uia.calls}
    assert {"key", "select", "expand", "scroll", "menu"} <= kinds


def test_wait_ready_reports_failure_as_error():
    ok = _tool().execute({"action": "wait_ready", "window": "Spotify"})
    assert ok["status"] == "success" and ok["ready"] is True

    empty = _tool(controls=[]).execute({"action": "wait_ready", "window": "X"})
    assert empty["status"] == "error" and empty["ready"] is False


def test_wait_for():
    out = _tool().execute({"action": "wait_for", "name": "Drake"})
    assert out["found"] is True


# ------------------------------------------------------- credential guard


def test_refuses_to_type_into_a_password_field():
    t = _tool()
    out = t.execute({"action": "type", "text": "hunter2", "into": 3})
    assert out["status"] == "refused"
    assert "password" in out["error"].lower()
    assert "themselves" in out["instruction"]
    assert not any(c[0] == "type" for c in t._uia.calls)


def test_refuses_a_field_named_like_a_secret():
    ctrls = [Control(0, "Edit", "PIN code", "", (0, 0, 10, 10), True)]
    t = _tool(ctrls)
    out = t.execute({"action": "type", "text": "1234", "name": "PIN code"})
    assert out["status"] == "refused"
    assert not any(c[0] == "type" for c in t._uia.calls)


def test_ordinary_fields_still_accept_typing():
    t = _tool()
    out = t.execute({"action": "type", "text": "me@example.com", "into": 4})
    assert out["status"] == "success"
    assert ("type", "me@example.com", 4, None) in t._uia.calls


# ---------------------------------------------------------------- errors


def test_missing_required_args():
    t = _tool()
    assert t.execute({"action": "type"})["status"] == "error"
    assert t.execute({"action": "key"})["status"] == "error"
    assert t.execute({"action": "select"})["status"] == "error"
    assert t.execute({"action": "menu"})["status"] == "error"
    assert t.execute({"action": "fly"})["status"] == "error"


def test_uia_error_is_reported_cleanly():
    class Broken(FakeUia):
        def tree(self, *a, **k):
            raise UiaError("window not found: Spotify")

    out = UIControlTool(Broken()).execute({"action": "tree", "window": "Spotify"})
    assert out["status"] == "error" and "window not found" in out["error"]


# ---------------------------------------------------------------- helpers


def test_escape_special_keys_chars():
    assert _escape("a+b") == "a{+}b"
    assert _escape("100%") == "100{%}"
    assert _escape("plain text") == "plain text"


def test_title_pattern_wraps_plain_strings_only():
    assert title_pattern("Spotify") == "(?i).*Spotify.*"
    assert title_pattern(r"^Spot.*fy$") == r"^Spot.*fy$"


# ------------------------------------------- model-invented arg shapes


def test_clean_title_unwraps_pseudo_selectors():
    from src.windows.uia import clean_title

    assert clean_title("[contains='Untitled - Notepad']") == "Untitled - Notepad"
    assert clean_title("title='Calculator'") == "Calculator"
    assert clean_title('"Spotify"') == "Spotify"
    assert clean_title("Untitled - Notepad") == "Untitled - Notepad"
    assert clean_title("Notepad") == "Notepad"


def test_ref_accepts_a_numeric_string():
    t = _tool()
    t.execute({"action": "click", "ref": "2"})
    assert ("click", 2, None, False, False) in t._uia.calls


def test_into_may_name_the_field_instead_of_referencing_it():
    t = _tool()
    t.execute({"action": "type", "text": "hi", "into": "Search input"})
    assert ("type", "hi", None, "Search input") in t._uia.calls


def test_named_password_field_via_into_is_still_refused():
    t = _tool()
    out = t.execute({"action": "type", "text": "hunter2", "into": "Password"})
    assert out["status"] == "refused"
    assert not any(c[0] == "type" for c in t._uia.calls)


def test_normalise_keys_accepts_what_models_emit():
    from src.windows.uia import normalise_keys as n

    # lists and human spellings - the shapes that were failing live
    assert n(["ctrl", "a"]) == "^a"
    assert n("ctrl+a") == "^a"
    assert n(["alt", "F4"]) == "%{F4}"
    assert n("enter") == "{ENTER}"
    assert n("esc") == "{ESC}"
    assert n(["ctrl", "shift", "escape"]) == "^+{ESC}"
    # already-correct pywinauto syntax passes through untouched
    assert n("^a") == "^a"
    assert n("{ENTER}") == "{ENTER}"
    assert n("%{F4}") == "%{F4}"
    # junk
    assert n("") == "" and n(None) == "" and n([]) == ""


def test_key_action_normalises_a_list():
    t = _tool()
    out = t.execute({"action": "key", "keys": ["ctrl", "a"]})
    assert out["status"] == "success"
    assert ("key", "^a") in t._uia.calls


def test_key_action_reports_a_useful_error():
    out = _tool().execute({"action": "key"})
    assert out["status"] == "error" and "ctrl+a" in out["error"]


# ------------------------------------------------- which desktop?


class _Router:
    def __init__(self, isolated=False):
        self.isolated = isolated


def _both():
    """A tool wired to both backends, as main.py wires it."""
    local, remote = FakeUia(), FakeUia()
    router = _Router()
    tool = UIControlTool(local, router=router, remote=remote)
    return tool, local, remote, router


def test_the_users_desktop_is_driven_locally():
    tool, local, remote, _ = _both()
    tool.execute({"action": "tree", "window": "Notepad"})

    assert local.calls and not remote.calls


def test_alfreds_own_desktop_is_driven_inside_that_session():
    """UI Automation cannot cross sessions - running the local backend
    here would silently act on the user's screen, which is the one thing
    'without disturbing me' rules out."""
    tool, local, remote, router = _both()
    router.isolated = True
    tool.execute({"action": "tree", "window": "Notepad"})

    assert remote.calls and not local.calls


def test_the_backend_can_change_between_calls():
    tool, local, remote, router = _both()
    tool.execute({"action": "windows"})
    router.isolated = True
    tool.execute({"action": "windows"})
    router.isolated = False
    tool.execute({"action": "windows"})

    assert len(local.calls) == 2
    assert len(remote.calls) == 1


def test_the_credential_refusal_applies_on_both_desktops():
    """The guard reads the target field's flags - it has to ask the
    backend that can actually see them."""
    tool, _, remote, router = _both()
    router.isolated = True
    out = tool.execute({"action": "type", "text": "hunter2", "into": 3})

    assert out["status"] == "refused"
    assert not any(c[0] == "type" for c in remote.calls)


def test_without_a_remote_backend_it_still_works_locally():
    tool = UIControlTool(FakeUia(), router=_Router(isolated=True), remote=None)
    out = tool.execute({"action": "windows"})

    assert out["status"] == "success"


# ------------------------------------- reading a control's text


class _Element:
    """Enough of a pywinauto wrapper for get_text."""

    def __init__(self, value=None, legacy=None, label=""):
        self._value = value
        self._legacy = legacy
        self._label = label

    def get_value(self):
        if self._value is None:
            raise RuntimeError("no ValuePattern")
        return self._value

    def legacy_properties(self):
        if self._legacy is None:
            raise RuntimeError("no legacy properties")
        return self._legacy

    def window_text(self):
        return self._label


def _session_reading(element):
    session = UiaSession()
    session._resolve = lambda ref=None, name=None: element  # type: ignore
    return session


def test_an_empty_field_reads_back_empty_not_its_label():
    """It used to fall through to the label, so a cleared text box read
    back as "Text editor" - which a model takes for its contents."""
    session = _session_reading(_Element(value="", label="Text editor"))

    assert session.get_text(ref=0) == ""


def test_a_field_with_text_reads_its_value():
    session = _session_reading(_Element(value="hello", label="Text editor"))

    assert session.get_text(ref=0) == "hello"


def test_a_control_with_no_value_falls_back_to_its_label():
    session = _session_reading(_Element(label="Save"))

    assert session.get_text(ref=0) == "Save"


def test_legacy_value_is_used_when_there_is_no_value_pattern():
    session = _session_reading(
        _Element(legacy={"Value": "42"}, label="Quantity")
    )

    assert session.get_text(ref=0) == "42"


# ------------------------------------------------- menu paths


def test_menu_paths_split_on_both_separators():
    """'File->Exit' used to become 'File-' and 'Exit': '>' was replaced
    with '->' first, turning it into 'File-->Exit'."""
    from src.windows.uia import _MENU_SEPARATOR

    def parts(path):
        return [p for p in _MENU_SEPARATOR.split(path) if p]

    assert parts("File->Exit") == ["File", "Exit"]
    assert parts("File>Exit") == ["File", "Exit"]
    assert parts("File -> Save As") == ["File", "Save As"]
    assert parts("View>Zoom>In") == ["View", "Zoom", "In"]


def test_a_menu_path_with_no_parts_is_an_error():
    session = UiaSession()
    session._last_window = object()

    with pytest.raises(UiaError):
        session.menu_select("->")


# ------------------------------------------- typing into the right place


def test_naming_a_window_focuses_it_before_typing():
    """Without this, 'type into Spotify' typed into whatever happened to
    have focus - which could be one of the user's own windows."""
    t = _tool()
    t.execute({"action": "type", "text": "drake", "window": "Spotify"})

    kinds = [c[0] for c in t._uia.calls]
    assert kinds.index("focus") < kinds.index("type")


def test_a_named_field_is_used_directly_without_stealing_focus():
    t = _tool()
    t.execute({"action": "type", "text": "drake", "into": 1})

    assert not any(c[0] == "focus" for c in t._uia.calls)
    assert ("type", "drake", 1, None) in t._uia.calls


def test_keys_alongside_type_are_pressed_afterwards():
    """Models write type(text=..., keys='{ENTER}') to mean "type this
    then run it". Dropping the keys filled a search box and never
    searched."""
    t = _tool()
    out = t.execute({
        "action": "type", "text": "drake", "into": 1, "keys": "{ENTER}",
    })

    assert out["then_pressed"] == "{ENTER}"
    calls = [c[0] for c in t._uia.calls]
    assert calls.index("type") < calls.index("key")


def test_type_without_keys_presses_nothing():
    t = _tool()
    out = t.execute({"action": "type", "text": "drake", "into": 1})

    assert "then_pressed" not in out
    assert not any(c[0] == "key" for c in t._uia.calls)


def test_a_password_field_is_still_refused_before_any_focus_happens():
    t = _tool()
    out = t.execute({
        "action": "type", "text": "hunter2", "into": 3, "window": "Bank",
    })

    assert out["status"] == "refused"
    assert t._uia.calls == []
