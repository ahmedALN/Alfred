"""What is already on the screen.

Every task began by finding out what a glance would have told it. Asked
to type into a Notepad that was open and in front, Alfred's first move
was to open Notepad - and that was not the model being stupid, it was
the model being told nothing. The executor's prompt carried the goal,
the plan, the tools and the history of the current task, and not one
word about the machine it was working on.
"""

import src.brain.onscreen as onscreen
from src.brain.onscreen import Screen


def _screen():
    return Screen(
        focused="Untitled - Notepad",
        focused_app="Notepad",
        windows=[
            ("Notepad", "Untitled - Notepad"),
            ("chrome", "Gmail - Inbox"),
            ("steam", "Steam"),
        ],
    )


# ---------------------------------------------------- what it can say


def test_it_says_what_is_in_front():
    told = _screen().brief()

    assert "In front: Untitled - Notepad" in told
    assert "Notepad" in told


def test_it_lists_the_others_without_repeating_the_front_one():
    told = _screen().brief()

    assert "Gmail - Inbox" in told
    assert told.count("Untitled - Notepad") == 1


def test_nothing_open_says_nothing():
    """An empty snapshot must not put an empty heading in every prompt."""
    assert Screen().brief() == ""


def test_it_can_be_asked_whether_something_is_running():
    screen = _screen()

    assert screen.running("steam") is True
    assert screen.running("Notepad") is True
    assert screen.running("gmail") is True        # matches on the title too
    assert screen.running("photoshop") is False
    assert screen.running("") is False


def test_a_long_list_is_trimmed_so_it_fits_in_a_prompt():
    crowd = Screen(
        focused="A", focused_app="a",
        windows=[(f"app{n}", f"window {n}") for n in range(40)],
    )

    assert crowd.brief(limit=6).count(";") <= 6


# --------------------------------------------------------- the cache


def test_the_answer_is_reused_for_a_moment(monkeypatch):
    """A plan and its first steps all happen inside a second or two,
    and what is on screen does not change in that time."""
    reads = []
    monkeypatch.setattr(onscreen, "_read", lambda: reads.append(1) or Screen())
    onscreen.forget()

    for _ in range(10):
        onscreen.look()

    assert len(reads) == 1


def test_it_can_be_told_to_look_again(monkeypatch):
    reads = []
    monkeypatch.setattr(onscreen, "_read", lambda: reads.append(1) or Screen())
    onscreen.forget()

    onscreen.look()
    onscreen.look(fresh=True)

    assert len(reads) == 2


def test_forgetting_makes_the_next_look_real(monkeypatch):
    reads = []
    monkeypatch.setattr(onscreen, "_read", lambda: reads.append(1) or Screen())
    onscreen.forget()

    onscreen.look()
    onscreen.forget()
    onscreen.look()

    assert len(reads) == 2


# ------------------------------------------------ it reaches the model


def test_the_planner_and_the_executor_are_both_told():
    """The executor is the one that mattered - it had no situation at
    all, so its opening move was always exploratory."""
    from src.brain.agent import _EXEC_SYSTEM, _PLAN_SYSTEM

    assert "ON SCREEN NOW" in _PLAN_SYSTEM
    assert "ON SCREEN NOW" in _EXEC_SYSTEM
    assert "do NOT open it" in _EXEC_SYSTEM
    assert "there is no \"open" in _PLAN_SYSTEM


# ------------------------------- a window in the taskbar is still open


def test_a_minimised_window_is_still_on_the_list(monkeypatch):
    """Discord and Spotify both vanished from the snapshot while
    sitting in the taskbar - open, openable, addressable, and invisible
    to the thing whose job is saying what is open. A minimised window
    reports a rectangle off in the negative thousands, which the size
    test read as "not really there"."""
    import types


    fake_gui = types.SimpleNamespace(
        IsWindowVisible=lambda h: True,
        GetWindowText=lambda h: {1: "Discord", 2: "tooltip"}[h],
        IsIconic=lambda h: h == 1,                    # Discord minimised
        GetWindowRect=lambda h: (-32000, -32000, -31900, -31900)
        if h == 1 else (0, 0, 50, 50),                # the tooltip is tiny
        GetForegroundWindow=lambda: 0,
        EnumWindows=lambda fn, extra: [fn(1, extra), fn(2, extra)],
    )
    fake_proc = types.SimpleNamespace(
        GetWindowThreadProcessId=lambda h: (0, 100 + h)
    )

    class _P:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return f"app{self.pid}.exe"

    monkeypatch.setitem(__import__("sys").modules, "win32gui", fake_gui)
    monkeypatch.setitem(__import__("sys").modules, "win32process", fake_proc)
    monkeypatch.setitem(
        __import__("sys").modules, "psutil",
        types.SimpleNamespace(Process=_P),
    )

    screen = onscreen._read()
    titles = [t for _, t in screen.windows]

    assert "Discord" in titles          # minimised, and kept
    assert "tooltip" not in titles      # genuinely too small to be a window
