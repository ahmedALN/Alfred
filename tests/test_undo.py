"""Putting something back.

Alfred could act and could not reverse anything. The diary, the audit
and the task log all say what happened and none of them offer to undo
it.
"""

from datetime import datetime, timedelta

from src.brain.undo import Undo, _reverse_of
from src.tools.undo_tool import UndoTool

NOW = datetime(2026, 9, 1, 14, 30)


def _undo(tmp_path):
    return Undo(tmp_path / "u.sqlite3")


# --------------------------------------------- what has a way back


def test_opening_an_app_can_be_put_back():
    back = _reverse_of("open_app", {"app": "Notepad"})

    assert back[0] == "ui_control"
    assert back[1]["action"] == "close"
    assert "Notepad" in back[2]


def test_the_way_back_survives_the_argument_being_called_something_else():
    """open_app takes eight words for the same thing."""
    assert _reverse_of("open_app", {"query": "Steam"})[1]["window"] == "Steam"


def test_a_diary_entry_says_it_cannot_undo_it_itself():
    """Alfred holds no permission to delete an event. The honest way
    back is telling you which one to remove."""
    tool, _args, what = _reverse_of("calendar", {"action": "add", "title": "Dentist"})

    assert tool is None
    assert "Dentist" in what
    assert "cannot delete" in what


def test_a_draft_is_recorded_as_unsent():
    _, _, what = _reverse_of("mail", {"action": "draft", "to": "sam@x.com"})

    assert "unsent" in what


def test_most_things_have_no_way_back_and_say_none():
    """Pretending otherwise would be worse than useless - you cannot
    un-search a store or un-tell somebody something."""
    assert _reverse_of("powershell", {"command": "Get-Process"}) is None
    assert _reverse_of("web", {"action": "search"}) is None
    assert _reverse_of("open_app", {}) is None


# ------------------------------------------------------ remembering


def test_a_reversible_action_is_kept(tmp_path):
    undo = _undo(tmp_path)

    undo.note_tool("open_app", {"app": "Notepad"}, now=NOW)

    assert undo.last(NOW)["what"] == "opened Notepad"


def test_something_with_no_way_back_is_not_kept(tmp_path):
    undo = _undo(tmp_path)

    assert undo.note_tool("powershell", {"command": "ls"}, now=NOW) == ""
    assert undo.recent(now=NOW) == []


def test_the_newest_comes_first(tmp_path):
    undo = _undo(tmp_path)
    undo.note_tool("open_app", {"app": "Notepad"}, now=NOW)
    undo.note_tool("open_app", {"app": "Steam"}, now=NOW + timedelta(minutes=1))

    assert undo.last(NOW + timedelta(minutes=2))["what"] == "opened Steam"


def test_yesterday_is_not_offered(tmp_path):
    """Offering to undo yesterday is offering to break something whose
    reason you have forgotten."""
    undo = _undo(tmp_path)
    undo.note_tool("open_app", {"app": "Notepad"}, now=NOW - timedelta(days=1))

    assert undo.recent(now=NOW) == []


def test_something_undone_is_not_offered_twice(tmp_path):
    undo = _undo(tmp_path)
    undo.note_tool("open_app", {"app": "Notepad"}, now=NOW)

    undo.mark(undo.last(NOW)["id"])

    assert undo.recent(now=NOW) == []


# ---------------------------------------------------------- the tool


class _Registry:
    def __init__(self, ok=True):
        self.ok = ok
        self.ran = []

    def execute(self, name, args):
        self.ran.append((name, args))
        return {"status": "success"} if self.ok else {"status": "error"}


def test_undoing_the_last_thing_runs_the_way_back(tmp_path):
    undo = _undo(tmp_path)
    undo.note_tool("open_app", {"app": "Notepad"})
    registry = _Registry()

    answer = UndoTool(undo, registry).execute({"action": "undo_last"})

    assert answer["status"] == "success"
    assert registry.ran[0][0] == "ui_control"
    assert registry.ran[0][1]["action"] == "close"


def test_something_it_cannot_reverse_is_said_plainly(tmp_path):
    undo = _undo(tmp_path)
    undo.record("added 'Dentist' to your calendar", None, {})

    answer = UndoTool(undo, _Registry()).execute({"action": "undo_last"})

    assert answer["status"] == "cannot"
    assert "Dentist" in answer["what"]


def test_nothing_to_undo_says_so(tmp_path):
    answer = UndoTool(_undo(tmp_path), _Registry()).execute({"action": "undo_last"})

    assert answer["status"] == "not_found"


def test_a_way_back_that_fails_is_reported_as_failing(tmp_path):
    undo = _undo(tmp_path)
    undo.note_tool("open_app", {"app": "Notepad"})

    answer = UndoTool(undo, _Registry(ok=False)).execute({"action": "undo_last"})

    assert answer["status"] == "error"
    assert undo.recent()      # still on the list, not marked done


def test_you_can_ask_what_is_still_reversible(tmp_path):
    undo = _undo(tmp_path)
    undo.note_tool("open_app", {"app": "Notepad"})
    undo.record("sent something", None, {})

    answer = UndoTool(undo, _Registry()).execute({"action": "what_can_i_undo"})

    assert answer["count"] == 2
    assert [c["reversible"] for c in answer["can_undo"]] == [False, True]
