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
        self.typed = text

    def send_key(self, keys):
        self.calls.append(("key", keys))

    def get_text(self, ref=None, name=None):
        self.calls.append(("get", ref, name))
        # A field reads back what was put in it, which is what lets
        # 'search' tell "typed" from "actually landed".
        return getattr(self, "typed", None) or "God's Plan"

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


# --------------------------------------------- reading a busy page


class _DeepUia(FakeUia):
    """Records the reach of each tree read."""

    def tree(self, title_re=None, pid=None, limit=80, max_depth=30,
             contains=None):
        self.calls.append(("tree", title_re, limit, max_depth))
        return "YouTube", list(self._controls)[:limit]

    def wait_ready(self, title_re=None, pid=None, timeout=25.0,
                   min_controls=3):
        self.calls.append(("wait_ready", title_re, timeout, min_controls))
        return len(self._controls) >= min_controls


def test_a_busy_page_can_be_read_past_the_default_cut_off():
    """A website's first 80 controls are its navigation - on a YouTube
    channel the videos start around 80, so the default stopped just
    before the content."""
    t = UIControlTool(_DeepUia())
    t.execute({"action": "tree", "window": "YouTube", "limit": 300})

    assert ("tree", "YouTube", 300, 30) in t._uia.calls


def test_the_read_limit_is_capped_so_a_huge_page_cannot_run_away():
    t = UIControlTool(_DeepUia())
    t.execute({"action": "tree", "window": "YouTube", "limit": 99999})

    assert t._uia.calls[0][2] == 500


def test_tree_depth_can_be_raised_for_a_deeply_nested_page():
    t = UIControlTool(_DeepUia())
    t.execute({"action": "tree", "window": "YouTube", "max_depth": 45})

    assert t._uia.calls[0][3] == 45


def test_waiting_can_require_a_real_number_of_controls():
    """A loading page reports a handful of controls; without this,
    wait_ready returns the moment the window exists."""
    t = UIControlTool(_DeepUia())
    t.execute({
        "action": "wait_ready", "window": "YouTube", "min_controls": 40,
    })

    assert t._uia.calls[0] == ("wait_ready", "YouTube", 25.0, 40)


def test_waiting_still_defaults_to_just_being_open():
    t = UIControlTool(_DeepUia())
    t.execute({"action": "wait_ready", "window": "Notepad"})

    assert t._uia.calls[0][3] == 3


# ------------------------------------------- whole jobs, not gestures


def _app(controls):
    """A tool wired to a fake app with a given control tree."""
    return UIControlTool(FakeUia(controls))


def _c(ref, ctype, name, aid="", enabled=True, password=False):
    return Control(ref, ctype, name, aid, (0, 0, 10, 10), enabled,
                   is_password=password)


STEAM = [
    _c(0, "Button", "Store"),
    _c(1, "Button", "Library"),
    _c(2, "Edit", "Search games", "searchbox"),
    _c(3, "ListItem", "Hades"),
    _c(4, "ListItem", "Hades II"),
    _c(5, "Button", "Play"),
]


def test_search_finds_the_apps_own_search_box():
    """Five calls collapse into one, and the field is chosen against the
    real tree instead of guessed from a printed list."""
    t = _app(STEAM)
    out = t.execute({"action": "search", "window": "Steam", "text": "Hades"})

    assert out["status"] == "success"
    assert out["field"] == "Search games"
    assert out["guessed_field"] is False
    kinds = [c[0] for c in t._uia.calls]
    assert kinds == ["tree", "click", "key", "type", "get", "key"]


def test_search_presses_enter_by_default_and_can_be_told_not_to():
    t = _app(STEAM)
    assert t.execute(
        {"action": "search", "window": "Steam", "text": "Hades"}
    )["submitted"] is True

    t2 = _app(STEAM)
    out = t2.execute({
        "action": "search", "window": "Steam", "text": "Hades",
        "submit": False,
    })
    assert "submitted" not in out
    assert [c[0] for c in t2._uia.calls].count("key") == 1  # only the ctrl+a


def test_search_says_so_when_it_had_to_guess_the_field():
    """An unnamed text box is better than nothing, but the caller should
    know it was a guess rather than a labelled search box."""
    t = _app([_c(0, "Edit", ""), _c(1, "Button", "Go")])
    out = t.execute({"action": "search", "window": "App", "text": "x"})

    assert out["status"] == "success" and out["guessed_field"] is True


def test_search_refuses_a_password_box_rather_than_typing_into_it():
    t = _app([_c(0, "Edit", "Password", "pwd", password=True)])
    out = t.execute({"action": "search", "window": "App", "text": "hunter2"})

    assert out["status"] == "refused"


def test_search_reports_honestly_when_there_is_no_box():
    t = _app([_c(0, "Button", "OK")])
    out = t.execute({"action": "search", "window": "App", "text": "x"})

    assert out["status"] == "error" and "no search box" in out["error"]


def test_open_item_opens_a_library_row_by_double_click():
    t = _app(STEAM)
    out = t.execute({"action": "open_item", "window": "Steam", "name": "Hades"})

    assert out["opened"] == "Hades"
    assert out["via"] == "double click"


def test_an_exact_name_beats_a_longer_one_containing_it():
    """'Hades' must not open 'Hades II'; '1.21.11' must not open
    '1.21.11 (needs update)'."""
    t = _app(STEAM)
    assert t.execute(
        {"action": "open_item", "window": "Steam", "name": "Hades"}
    )["opened"] == "Hades"

    instances = [
        _c(0, "ListItem", "1.21.11 (needs update, last played Tuesday)"),
        _c(1, "ListItem", "1.21.11"),
        _c(2, "ListItem", "1.20.4"),
    ]
    assert _app(instances).execute(
        {"action": "open_item", "window": "MultiMC", "name": "1.21.11"}
    )["opened"] == "1.21.11"


def test_a_button_is_clicked_once_not_twice():
    t = _app(STEAM)
    out = t.execute({"action": "open_item", "window": "Steam", "name": "Play"})

    assert out["via"] == "click"


def test_a_disabled_match_loses_to_an_enabled_one():
    controls = [
        _c(0, "ListItem", "Latest save", enabled=False),
        _c(1, "Button", "Latest save"),
    ]
    out = _app(controls).execute(
        {"action": "open_item", "window": "Game", "name": "Latest save"}
    )
    assert out["via"] == "click"      # the enabled Button, not the dead row


def test_open_item_offers_nearby_names_when_nothing_matches():
    """A wrong guess is worse than a question - the model gets real
    names to put back to the user."""
    t = _app(STEAM)
    out = t.execute({
        "action": "open_item", "window": "Steam", "name": "Celeste",
    })

    assert out["status"] == "not_found"
    assert "Hades" in out["nearby"]


# --------------------------------------------- picking the right window


def test_a_whole_word_title_beats_a_fragment_in_a_path():
    """Searching in Explorer renames it to "notepad - Search Results in
    Windows - File Explorer", at which point a terminal whose title is a
    system32 path was the shorter match for "Windows" and the next
    action went there instead."""
    from src.windows.uia import _title_score

    assert _title_score("Windows", "Windows") > _title_score(
        "Windows PowerShell", "Windows")
    assert _title_score("Windows PowerShell", "Windows") > _title_score(
        r"C:\WINDOWS\system32\cmd.exe", "Windows")


def test_a_title_that_does_not_contain_it_scores_nothing():
    from src.windows.uia import _title_score

    assert _title_score("Spotify Premium", "Steam") == 0
    assert _title_score("", "Steam") == 0
    assert _title_score("Steam", "") == 0


def test_an_exact_title_wins_outright():
    from src.windows.uia import _title_score

    assert _title_score("Steam", "steam") > _title_score(
        "Steam - Big Picture Mode", "steam")


def test_a_failed_wait_says_what_is_actually_on_screen():
    """Steam sitting on "Sign in to Steam" is a different problem from
    Steam not starting, and a bare timeout makes them look identical."""

    class NeverReady(FakeUia):
        def wait_ready(self, title_re=None, pid=None, timeout=25.0,
                       min_controls=3):
            return False

        def windows(self, limit=40):
            return [{"title": "Sign in to Steam", "pid": 1, "class": "X"}]

    out = UIControlTool(NeverReady()).execute(
        {"action": "wait_ready", "window": "Steam", "timeout": 1}
    )

    assert out["status"] == "error"
    assert out["windows_open"] == ["Sign in to Steam"]
    # And when the window itself says what it wants, that replaces the
    # general advice: this tree has a password field in it.
    assert out["needs_user"] == "sign_in"
    assert "Do NOT type" in out["instruction"]


def test_a_link_is_preferred_over_the_text_label_inside_it():
    """Steam's results carry both: a Hyperlink "Hades 17 Sep, 2020" and
    a Text "Hades" sitting on top of it. The label often works because
    it overlays the link, but the link is the thing that navigates."""
    controls = [
        _c(0, "Text", "Hades"),
        _c(1, "Hyperlink", "Hades 17 Sep, 2020"),
    ]
    out = _app(controls).execute(
        {"action": "open_item", "window": "Steam", "name": "Hades"}
    )
    assert out["control"] == "Hyperlink"


def test_an_exact_label_still_wins_when_no_link_matches():
    controls = [
        _c(0, "Text", "Latest save"),
        _c(1, "Hyperlink", "Something else"),
    ]
    out = _app(controls).execute(
        {"action": "open_item", "window": "Game", "name": "Latest save"}
    )
    assert out["opened"] == "Latest save"


def test_a_version_does_not_match_a_longer_version_beside_it():
    """MultiMC lists "1.21.11 Instance" next to "1.21.11afk1 Instance"
    and "1.21.11-Hardcore Instance". Only the first is what "the 1.21.11
    instance" means."""
    instances = [
        _c(0, "ListItem", "1.21.11afk1 Instance"),
        _c(1, "ListItem", "1.21.11-Hardcore Instance"),
        _c(2, "ListItem", "1.21.11 Instance"),
        _c(3, "ListItem", "1.21.8 Instance"),
    ]
    out = _app(instances).execute(
        {"action": "open_item", "window": "MultiMC", "name": "1.21.11"}
    )
    assert out["opened"] == "1.21.11 Instance"


def test_the_only_candidate_is_still_used_even_if_it_runs_on():
    instances = [_c(0, "ListItem", "1.21.11afk1 Instance")]
    out = _app(instances).execute(
        {"action": "open_item", "window": "MultiMC", "name": "1.21.11"}
    )
    assert out["opened"] == "1.21.11afk1 Instance"


def test_naming_a_window_reads_it_before_clicking_by_name():
    """"Click 'Don't update yet' in MultiMC" used to resolve against
    whatever tree happened to be cached, and failed with "no control
    matches" while the control was plainly on screen."""
    t = _tool()
    t.execute({"action": "click", "window": "MultiMC", "name": "Search"})

    kinds = [c[0] for c in t._uia.calls]
    assert kinds.index("tree") < kinds.index("click")


def test_a_ref_is_never_re_read_because_that_would_renumber_it():
    t = _tool()
    t.execute({"action": "click", "window": "MultiMC", "ref": 1})

    assert not any(c[0] == "tree" for c in t._uia.calls)


# ----------------------------------- controls the layer cannot name


class _Memory:
    def __init__(self, landmarks=None):
        self._marks = landmarks or {}
        self.learned = []

    def find_landmark(self, app, wanted):
        return self._marks.get(wanted.lower())

    def landmarks(self, app):
        return list(self._marks.values())

    def note_landmark(self, app, label, rel_x, rel_y, source="observed"):
        self.learned.append((app, label, rel_x, rel_y, source))


class _Positioned(FakeUia):
    """A window at a known place, with nothing findable by name."""

    def __init__(self):
        super().__init__(controls=[])
        self.clicked_at = []

    def windows(self, limit=40):
        return [{"title": "MultiMC", "pid": 7, "class": "Qt",
                 "rect": [600, 0, 1300, 1000]}]

    def click_point(self, x, y, double=False):
        self.clicked_at.append((x, y))

    def control_info(self, ref=None, name=None):
        return Control(0, "Custom", "", "", (1258, 190, 1260, 204), True)


def test_a_button_with_no_name_is_clicked_by_what_was_learned():
    """MultiMC's Launch has no name, no id, no legacy name - only a
    place. Learned once, it works from then on."""
    ui = _Positioned()
    memory = _Memory({"launch": {"label": "Launch", "rel_x": 0.9,
                                 "rel_y": 0.2}})
    tool = UIControlTool(ui, memory=memory)

    out = tool.execute({"action": "open_item", "window": "MultiMC",
                        "name": "Launch"})

    assert out["status"] == "success"
    assert out["via"] == "learned position"
    # 600 + 700*0.9, 0 + 1000*0.2
    assert ui.clicked_at == [(1230, 200)]


def test_a_landmark_is_stored_relative_so_it_survives_a_move():
    ui = _Positioned()
    memory = _Memory()
    tool = UIControlTool(ui, memory=memory)

    tool.execute({"action": "learn_control", "window": "MultiMC",
                  "name": "Launch", "x": 1230, "y": 200})

    app, label, rel_x, rel_y, _ = memory.learned[0]
    assert label == "Launch"
    assert abs(rel_x - 0.9) < 0.01 and abs(rel_y - 0.2) < 0.01


def test_learning_needs_somewhere_to_put_it():
    out = UIControlTool(_Positioned(), memory=None).execute(
        {"action": "learn_control", "window": "MultiMC", "name": "Launch",
         "x": 1230, "y": 200}
    )
    assert out["status"] == "error"


def test_learning_needs_a_position():
    out = UIControlTool(_Positioned(), memory=_Memory()).execute(
        {"action": "learn_control", "window": "MultiMC", "name": "Launch"}
    )
    assert out["status"] == "error" and "x and y" in out["error"]


def test_clicking_a_bare_position_is_allowed_for_probing():
    ui = _Positioned()
    out = UIControlTool(ui).execute(
        {"action": "click", "window": "MultiMC", "x": 900, "y": 400}
    )
    assert out["clicked_at"] == [900, 400] and ui.clicked_at == [(900, 400)]


def test_an_unknown_name_with_no_landmark_still_reports_not_found():
    ui = _Positioned()
    out = UIControlTool(ui, memory=_Memory()).execute(
        {"action": "open_item", "window": "MultiMC", "name": "Launch"}
    )
    assert out["status"] in ("not_found", "needs_user")
    assert ui.clicked_at == []
