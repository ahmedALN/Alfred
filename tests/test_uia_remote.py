"""Driving the accessibility layer inside Alfred's own session.

UI Automation cannot cross Windows sessions, so ui_control could only
ever reach the user's screen - the whole reason "without disturbing me"
used to fall back to screenshot-and-guess. RemoteUia is the near side of
the agent that runs inside that session; what matters is that it is
indistinguishable from the local backend to everything above it.
"""

import pytest

from src.windows.child_session import ChildSessionError
from src.windows.uia import UiaError, key_tokens
from src.windows.uia_remote import RemoteUia

_TREE = {
    "window": "Untitled - Notepad",
    "count": 3,
    "controls": [
        {"ref": 0, "type": "Document", "name": "Text editor", "id": None,
         "center": [400, 300], "enabled": True},
        {"ref": 1, "type": "Button", "name": "Bold", "id": "bold",
         "center": [40, 60], "enabled": True},
        {"ref": 2, "type": "Edit", "name": "Password", "id": "pwd",
         "center": [80, 90], "enabled": True, "password_field": True},
    ],
}


class FakeAgent:
    """Stands in for ChildInputAgent at the far end of the pipe."""

    def __init__(self, replies=None, fail=None):
        self.requests = []
        self.chords = []
        self._replies = replies or {}
        self._fail = fail

    def uia(self, action, **arguments):
        request = {"op": "uia", "action": action}
        request.update({k: v for k, v in arguments.items() if v is not None})
        self.requests.append(request)
        if self._fail:
            raise ChildSessionError(self._fail)
        return self._replies.get(action, {})

    def key(self, chord):
        self.chords.append(list(chord))
        return {"ok": True}


def _ui(replies=None, fail=None):
    agent = FakeAgent(replies, fail)
    return RemoteUia(lambda: agent), agent


# ---------------------------------------------------------------- reads


def test_tree_comes_back_as_controls_not_json():
    ui, agent = _ui({"tree": _TREE})
    title, controls = ui.tree("Notepad")

    assert title == "Untitled - Notepad"
    assert [c.name for c in controls] == ["Text editor", "Bold", "Password"]
    assert controls[1].automation_id == "bold"
    assert agent.requests[0]["op"] == "uia"
    assert agent.requests[0]["action"] == "tree"


def test_a_password_field_stays_flagged_across_the_pipe():
    """The credential refusal reads this flag - losing it in transit
    would let Alfred type into a password box on its own desktop."""
    ui, _ = _ui({"tree": _TREE})
    _, controls = ui.tree("Notepad")

    assert controls[2].is_password is True
    assert controls[0].is_password is False
    assert controls[2].as_dict()["password_field"] is True


def test_control_info_carries_the_flag_too():
    ui, _ = _ui({"info": {"control": _TREE["controls"][2]}})
    info = ui.control_info(ref=2)

    assert info is not None and info.is_password is True


def test_control_info_is_none_when_nothing_matches():
    ui, _ = _ui({"info": {"control": None}})
    assert ui.control_info(name="nope") is None


def test_a_model_invented_window_selector_is_cleaned_first():
    """Models write window="[contains='Untitled - Notepad']"; the agent
    matches on plain text."""
    ui, agent = _ui({"tree": _TREE})
    ui.tree("[contains='Untitled - Notepad']")

    assert agent.requests[0]["window"] == "Untitled - Notepad"


def test_windows_and_find():
    ui, _ = _ui({
        "windows": {"windows": [{"title": "Notepad", "pid": 4, "class": "N"}]},
        "find": {"controls": [_TREE["controls"][1]]},
    })
    assert ui.windows()[0]["pid"] == 4
    assert [c.name for c in ui.find("bold")] == ["Bold"]


# -------------------------------------------------------------- actions


def test_click_variants_map_to_the_right_action():
    ui, agent = _ui({
        "click": {"clicked": "Bold"},
        "double_click": {"clicked": "Bold"},
        "right_click": {"clicked": "Bold"},
    })
    ui.click(ref=1)
    ui.click(ref=1, double=True)
    ui.click(ref=1, right=True)

    assert [r["action"] for r in agent.requests] == [
        "click", "double_click", "right_click",
    ]


def test_none_arguments_are_left_out_of_the_request():
    """A null 'name' must not shadow the ref the caller did give."""
    ui, agent = _ui({"click": {"clicked": "Bold"}})
    ui.click(ref=1)

    assert "name" not in agent.requests[0]
    assert agent.requests[0]["ref"] == 1


def test_keys_are_sent_as_chords_the_agent_understands():
    ui, agent = _ui()
    ui.send_key("^a")
    ui.send_key("{ENTER}")

    assert agent.chords == [["ctrl", "a"], ["enter"]]


def test_a_multi_key_sequence_arrives_in_order():
    ui, agent = _ui()
    ui.send_key("^(ab)")

    assert agent.chords == [["ctrl", "a"], ["ctrl", "b"]]


def test_an_unreadable_key_is_an_error_not_a_silent_no_op():
    ui, _ = _ui()
    with pytest.raises(UiaError):
        ui.send_key("")


def test_type_get_select_expand_scroll_menu():
    ui, agent = _ui({
        "get": {"text": "hello"},
        "select": {"selected": "Playlists"},
        "expand": {"expanded": "More"},
        "scroll": {"scrolled": "scrolled down"},
        "menu": {"menu": "File->Exit"},
    })
    ui.type_text("hi", ref=0)
    assert ui.get_text(ref=0) == "hello"
    assert ui.select("Playlists", ref=1) == "Playlists"
    assert ui.expand(ref=1) == "More"
    assert ui.scroll("down", 3) == "scrolled down"
    assert ui.menu_select("File->Exit") == "File->Exit"

    assert [r["action"] for r in agent.requests] == [
        "type", "get", "select", "expand", "scroll", "menu",
    ]


# ---------------------------------------------------------------- waits


def test_waits_return_plain_booleans():
    ui, _ = _ui({
        "wait_ready": {"ready": True},
        "wait_for": {"found": False},
        "exists": {"exists": True},
    })
    assert ui.wait_ready("Notepad") is True
    assert ui.wait_for("Save") is False
    assert ui.exists("Save") is True


def test_wait_ready_passes_the_timeout_through():
    ui, agent = _ui({"wait_ready": {"ready": True}})
    ui.wait_ready("Notepad", timeout=40)

    assert agent.requests[0]["timeout"] == 40


# --------------------------------------------------------------- errors


def test_an_unreachable_agent_is_a_uia_error_not_a_crash():
    """ui_control catches UiaError and reports it; anything else would
    surface as a stack trace the model cannot act on."""
    ui, _ = _ui(fail="ChildInputAgent is not connected.")

    with pytest.raises(UiaError, match="not connected"):
        ui.tree("Notepad")


def test_a_missing_data_block_does_not_explode():
    class Empty:
        def uia(self, action, **arguments):
            return {}

    ui = RemoteUia(lambda: Empty())
    title, controls = ui.tree("Notepad")
    assert title == "" and controls == []


def test_an_agent_without_the_accessibility_layer_says_what_to_do():
    """Whoever hits this needs to know it is a stale binary, not a
    broken window."""
    ui, _ = _ui(fail="unknown_op: Unknown operation 'uia'.")

    with pytest.raises(UiaError, match="out of date"):
        ui.tree("Notepad")


# ----------------------------------------------------------- key tokens


def test_key_tokens_reads_every_shape_a_model_emits():
    assert key_tokens("ctrl+a") == [["ctrl", "a"]]
    assert key_tokens(["ctrl", "a"]) == [["ctrl", "a"]]
    assert key_tokens("enter") == [["enter"]]
    assert key_tokens("{ENTER}") == [["enter"]]
    assert key_tokens("^a") == [["ctrl", "a"]]
    assert key_tokens("%{F4}") == [["alt", "f4"]]
    assert key_tokens("{VK_LWIN}e") == [["win", "e"]]
    assert key_tokens("^+n") == [["ctrl", "shift", "n"]]
    assert key_tokens("") == []
    assert key_tokens(None) == []


def test_key_tokens_survives_a_round_trip_through_normalise():
    from src.windows.uia import normalise_keys

    for raw in ("ctrl+a", ["ctrl", "a"], "enter", "alt+f4", "ctrl+shift+n"):
        assert key_tokens(normalise_keys(raw)) == key_tokens(raw)
