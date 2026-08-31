"""A window that is plainly there and cannot be touched.

Task Manager, Registry Editor, anything started as administrator: the
window is on screen and the accessibility layer cannot see it at all,
because a program cannot read the controls of one running at a higher
integrity level than itself.

What Alfred said was "window not found: timed out". So it tried again,
and again - nine tool calls in one task - while the window sat there in
front of it. Alfred is deliberately not an administrator, and this is
the honest version of that limit.
"""

import src.brain.onscreen as onscreen
from src.brain.onscreen import Screen
from src.tools.ui_control import _explain


def _on_screen(monkeypatch, *titles):
    monkeypatch.setattr(
        onscreen, "_read",
        lambda: Screen(windows=[("app", t) for t in titles]),
    )
    onscreen.forget()


def test_a_window_the_screen_can_see_is_explained(monkeypatch):
    _on_screen(monkeypatch, "Task Manager")

    said = _explain("window not found: timed out", "Task Manager")

    assert "IS on screen" in said
    assert "administrator" in said
    assert "do not keep trying" in said


def test_a_window_that_really_is_not_there_is_left_alone(monkeypatch):
    """Not every failure is an elevation problem, and saying so when it
    is not would be worse than the timeout."""
    _on_screen(monkeypatch, "Notepad")

    said = _explain("window not found: timed out", "Photoshop")

    assert said == "window not found: timed out"


def test_a_different_kind_of_failure_is_not_reinterpreted(monkeypatch):
    _on_screen(monkeypatch, "Task Manager")

    said = _explain("'find' needs 'query'", "Task Manager")

    assert said == "'find' needs 'query'"


def test_not_becoming_usable_gets_the_same_explanation(monkeypatch):
    _on_screen(monkeypatch, "Registry Editor")

    said = _explain(
        "Registry Editor did not become usable in time", "Registry Editor"
    )

    assert "administrator" in said


def test_with_no_window_named_there_is_nothing_to_explain(monkeypatch):
    _on_screen(monkeypatch, "Task Manager")

    assert _explain("window not found", None) == "window not found"


def test_a_broken_snapshot_does_not_break_the_error(monkeypatch):
    def _boom():
        raise RuntimeError("no display")

    monkeypatch.setattr(onscreen, "_read", _boom)
    onscreen.forget()

    assert _explain("window not found", "Task Manager") == "window not found"
