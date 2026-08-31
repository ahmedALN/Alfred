"""The words a model reaches for.

Twelve of the bench's tool calls failed, and not one of them failed
because Alfred could not do the thing. They failed on the label of an
argument: 'query' where 'app' was expected, 'contains' where 'query'
was, and a name that described what was wanted rather than what the
control happened to be called.

Each of those costs a round trip to be told a synonym - which is both
the accuracy problem and, at several seconds a call, most of the speed
one.
"""

from src.tools.open_app import OpenAppTool
from src.tools.ui_control import UIControlTool


class _Ui(UIControlTool):
    """Stops before touching a real desktop."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.seen = None

    def _session(self, *a, **k):
        raise AssertionError("should have been refused before this")


# ------------------------------------------------------------- open_app


def test_asking_to_open_something_by_query_works():
    """{'target': 'current', 'query': 'Notepad'} - four bench failures."""
    from src.tools.open_app import app_name

    for key in ("app", "name", "application", "app_name",
                "query", "program", "executable", "path"):
        assert app_name({key: "Notepad", "target": "current"}) == "Notepad", key


def test_the_proper_name_wins_when_several_are_given():
    from src.tools.open_app import app_name

    assert app_name({"app": "Notepad", "query": "something else"}) == "Notepad"


def test_saying_only_where_to_open_it_names_nothing():
    from src.tools.open_app import app_name

    assert app_name({"target": "current"}) is None
    assert app_name({"app": "   "}) is None


def test_saying_only_where_to_open_it_explains_what_is_missing():
    """'target' says which desktop, not which program - the error now
    says so instead of naming a key the caller did not use."""
    answer = OpenAppTool().execute({"target": "current"})

    assert answer["status"] == "error"
    assert "Notepad" in answer["error"]
    assert "target" in answer["error"]


# ----------------------------------------------------------------- find


def test_find_takes_the_words_however_they_are_labelled():
    tool = _Ui()
    for key in ("query", "contains", "text", "search"):
        answer = tool.execute({"action": "find", "window": "Steam", key: "Hades"})
        # It gets past the argument check and fails later, on the desktop.
        assert "needs 'query'" not in str(answer.get("error", "")), key


def test_find_with_nothing_to_look_for_says_what_it_wants():
    answer = _Ui().execute({"action": "find", "window": "Steam"})

    assert "query" in answer["error"]
    assert "look for" in answer["error"]
