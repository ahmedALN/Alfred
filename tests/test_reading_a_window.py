"""Spotify was open, healthy, and reported as not responding.

The whole of one evening's failures came from reading windows badly:

    "Spotify did not become usable in time"
        - it was on screen with 1,347 usable controls

    "Spotify Premium has 11 controls with no names - I have not worked
     this app out before. Shall I map it now?"
        - it has 1,511 NAMED controls, including a search box called
          "What do you want to play?"

    "could not read the control tree: 'NoneType' object has no
     attribute 'has_depth'"
        - twice, with an apology for a technical issue

    "But 'Spotify Premium' IS on screen - which means it is running as
     administrator"
        - it was not; pywinauto had dropped GetForegroundWindow and the
          AttributeError was being dressed up as a diagnosis

Measured against six real windows, the depth limit that was supposed to
stop a big tree hanging the call was the thing making it hang:

    window                    depth=30      unbounded
    Spotify Premium     1770 ctl  18.8s   1770 ctl  0.8s
    Discord             1008 ctl   8.8s   1008 ctl  0.4s
    Claude               305 ctl   3.2s    305 ctl  0.2s
    File Explorer        100 ctl   0.4s    100 ctl  0.2s
    Notepad               47 ctl   0.1s     47 ctl  0.0s

Identical counts, up to twenty-three times the speed.
"""

from __future__ import annotations

import pytest

from src.tools.ui_control import _OUR_FAULT, _explain

# ====================================================================
# Reading the tree
# ====================================================================


class _Win:
    """A window that answers descendants() the way pywinauto does."""

    def __init__(self, *, unbounded=None, bounded=None, raises=None):
        self._unbounded = unbounded if unbounded is not None else []
        self._bounded = bounded
        self._raises = raises
        self.calls: list[dict] = []
        self.woken = 0
        self.handle = 1

    def descendants(self, **kwargs):
        self.calls.append(kwargs)

        if self._raises and len(self.calls) <= self._raises:
            raise AttributeError("'NoneType' object has no attribute 'has_depth'")

        if "depth" in kwargs:
            return self._bounded if self._bounded is not None else self._unbounded

        return self._unbounded


def _session():
    from src.windows.uia import UiaSession

    return UiaSession()


def test_the_tree_is_read_unbounded_first():
    """The bound cost nineteen seconds on Spotify and bought nothing."""
    session = _session()
    win = _Win(unbounded=["a", "b", "c"])

    assert session._descendants(win, 30) == ["a", "b", "c"]
    assert win.calls[0] == {}, "the depth-limited walk went first"


def test_a_sleeping_chromium_tree_is_woken_and_retried(monkeypatch):
    """The crash Alfred apologised for twice, rather than fixing."""
    session = _session()
    woken = []

    monkeypatch.setattr(
        type(session), "_wake_accessibility",
        staticmethod(lambda w: woken.append(w)),
    )
    monkeypatch.setattr("time.sleep", lambda _s: None)

    win = _Win(unbounded=["a"], raises=1)

    assert session._descendants(win, 30) == ["a"]
    assert woken, "it gave up without asking the app to wake"


def test_a_tree_that_never_reads_still_raises_clearly():
    from src.windows.uia import UiaError

    session = _session()
    win = _Win(raises=99)

    with pytest.raises(UiaError, match="could not read the control tree"):
        session._descendants(win, 30)


def test_an_old_pywinauto_without_depth_is_still_handled():
    """Some versions do not take the keyword at all."""

    class _Old(_Win):
        def descendants(self, **kwargs):
            self.calls.append(kwargs)
            if "depth" in kwargs:
                raise TypeError("unexpected keyword argument 'depth'")
            return ["a", "b"]

    assert _session()._descendants(_Old(), 30) == ["a", "b"]


# ====================================================================
# Saying why, when it cannot be read
# ====================================================================


@pytest.mark.parametrize("error", [
    "window not found: module 'x' has no attribute 'GetForegroundWindow'",
    "window not found: AttributeError somewhere",
    "window not found: TypeError: bad argument",
    "window not found: NameError: thing",
])
def test_our_own_bugs_are_not_explained_as_an_admin_window(error):
    """It told the user Spotify was running as administrator. It was
    not. pywinauto had dropped a function, and the AttributeError was
    being dressed up as a confident diagnosis - the sort that sends
    somebody off restarting things for an hour."""
    assert _explain(error, "Spotify") == error


def test_a_real_unreadable_window_still_gets_the_explanation(monkeypatch):
    """Task Manager really is unreadable, and saying so is the point."""

    class _Look:
        windows = [("taskmgr", "Task Manager")]

    monkeypatch.setattr(
        "src.brain.onscreen.look", lambda fresh=False: _Look()
    )

    out = _explain("window not found: timed out", "Task Manager")

    assert "running as administrator" in out


def test_a_window_that_is_genuinely_absent_is_left_alone(monkeypatch):
    class _Look:
        windows = []

    monkeypatch.setattr(
        "src.brain.onscreen.look", lambda fresh=False: _Look()
    )

    assert _explain("window not found: timed out", "Nothing") == (
        "window not found: timed out"
    )


@pytest.mark.parametrize("text,ours", [
    ("has no attribute 'GetForegroundWindow'", True),
    ("AttributeError", True),
    ("unexpected keyword argument", True),
    ("timed out", False),
    ("access is denied", False),
])
def test_which_errors_read_as_our_fault(text, ours):
    assert bool(_OUR_FAULT.search(text)) is ours
