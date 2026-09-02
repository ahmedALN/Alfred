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


# ====================================================================
# Typing into a window that has not finished coming to the front
# ====================================================================


def test_it_waits_for_the_window_to_actually_have_the_keyboard(monkeypatch):
    """send_keys types into whatever has focus AT THE MOMENT EACH KEY
    GOES, so starting before the switch has finished does not lose the
    first few characters cleanly - it interleaves them. Notepad
    received "hellohellohello ooom     rrress eest" for "hello from the
    stress test"."""
    import src.windows.uia as uia

    foreground = [111]
    monkeypatch.setattr(uia, "time", uia.time)

    class _User32:
        @staticmethod
        def GetForegroundWindow():
            # Third look is the charm, as a real switch would be.
            foreground[0] = 222 if len(seen) >= 2 else 111
            seen.append(1)
            return foreground[0]

    seen: list[int] = []

    class _Ctypes:
        windll = type("w", (), {"user32": _User32})

    monkeypatch.setitem(__import__("sys").modules, "ctypes", _Ctypes)

    win = type("W", (), {"handle": 222})()

    assert uia._wait_for_focus(win, timeout=2.0) is True
    assert len(seen) >= 3, "it did not wait for the switch"


def test_it_gives_up_rather_than_hanging(monkeypatch):
    """A window that never takes focus must not stall the whole task."""
    import src.windows.uia as uia

    class _User32:
        @staticmethod
        def GetForegroundWindow():
            return 999

    class _Ctypes:
        windll = type("w", (), {"user32": _User32})

    monkeypatch.setitem(__import__("sys").modules, "ctypes", _Ctypes)

    win = type("W", (), {"handle": 222})()

    started = __import__("time").monotonic()
    assert uia._wait_for_focus(win, timeout=0.3) is False
    assert __import__("time").monotonic() - started < 2.0


def test_a_window_with_no_handle_is_not_waited_on():
    import src.windows.uia as uia

    class _NoHandle:
        @property
        def handle(self):
            raise RuntimeError("gone")

    assert uia._wait_for_focus(_NoHandle()) is False


# ====================================================================
# Offering to map an app that needs no mapping
# ====================================================================


class _Ui:
    def __init__(self, blanks, named):
        self._blanks = blanks
        self._controls = list(range(named))

    def unnamed(self, window, pid, limit=60):
        return window, list(range(self._blanks))


class _Memory:
    @staticmethod
    def landmarks(_window):
        return None


def _offer(blanks, named):
    from src.tools.ui_control import UIControlTool

    tool = UIControlTool.__new__(UIControlTool)
    tool._memory = _Memory()

    return tool._offer_mapping(_Ui(blanks, named), "An App", None)


def test_an_app_with_plenty_of_names_is_not_offered_for_mapping():
    """Spotify has 1,511 named controls, including a search box called
    "What do you want to play?", and was offered for mapping over
    eleven unnamed ones - after which Alfred told the user it could not
    play a song without mapping the app first."""
    assert _offer(blanks=11, named=1511) == {}


def test_an_app_that_really_cannot_be_driven_by_name_is_offered():
    """MultiMC's Launch panel: 23 unnamed Custom controls against a
    handful with names. That is an app you genuinely cannot use by
    name."""
    offer = _offer(blanks=23, named=6)

    assert offer.get("needs_user") == "offer_mapping"
    assert "23 controls with no names" in offer["question"]


def test_a_couple_of_unnamed_controls_is_never_worth_asking_about():
    assert _offer(blanks=3, named=0) == {}


def test_a_window_nothing_could_be_read_from_is_still_offered():
    """Nothing named at all is the case mapping exists for."""
    assert _offer(blanks=12, named=0).get("needs_user") == "offer_mapping"
