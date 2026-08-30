"""A direct voice request for isolation must actually be isolated.

The first live test failed here: the voice model answered "without
disturbing me, open Notepad and type hello" inline with open_app +
ui_control, never touching run_task, so the whole isolation path was
skipped - and Alfred then *claimed* it had used its private desktop.
"""

import asyncio
import os

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from src.ai.gemini import AlfredLiveSession  # noqa: E402
from src.tools.open_app import OpenAppTool  # noqa: E402
from src.tools.registry import ToolRegistry  # noqa: E402


class FakeRouter:
    def __init__(self, isolated=False):
        self.isolated = isolated
        self.switches = []

    def use_isolated(self):
        self.isolated = True
        self.switches.append("child")

    def use_users_desktop(self):
        self.isolated = False
        self.switches.append("current")


class FakeDesktop:
    def __init__(self, session=11):
        self._session = session
        self.ensured = 0
        self.cleaned = 0
        self.launched = []

    def ensure(self):
        self.ensured += 1
        return self._session

    def cleanup(self):
        self.cleaned += 1
        return {"closed": []}

    def launch(self, path, args=None):
        self.launched.append(path)
        return {"launched": True, "pid": 123, "session": self._session}


def _session(isolated_request=True, desktop=None, router=None):
    s = AlfredLiveSession(ToolRegistry())
    s.attach_isolation(desktop or FakeDesktop(), router or FakeRouter())
    s._turn_isolated = isolated_request
    return s


# ------------------------------------------------------- the gate


def test_desktop_tool_brings_the_private_desktop_up_first():
    d, r = FakeDesktop(), FakeRouter()
    s = _session(desktop=d, router=r)

    blocked = asyncio.run(s._apply_isolation("desktop_control"))

    assert blocked is None          # the tool is allowed to run
    assert d.ensured == 1
    assert r.isolated is True


def test_desktop_agnostic_tools_are_untouched():
    d, r = FakeDesktop(), FakeRouter()
    s = _session(desktop=d, router=r)

    for tool in ("system_info", "recall", "powershell", "run_task"):
        assert asyncio.run(s._apply_isolation(tool)) is None
    assert d.ensured == 0 and r.isolated is False


def test_nothing_happens_when_isolation_was_not_requested():
    d, r = FakeDesktop(), FakeRouter()
    s = _session(isolated_request=False, desktop=d, router=r)

    assert asyncio.run(s._apply_isolation("open_app")) is None
    assert d.ensured == 0 and r.isolated is False


def test_ui_control_now_reaches_the_private_desktop():
    """It used to be refused: UI Automation is session-scoped, so the
    copy inside Alfred could not see that session. The accessibility
    layer runs inside the session now, so the precise tool works there
    too - screenshot-and-guess is no longer the only option."""
    d, r = FakeDesktop(), FakeRouter()
    s = _session(desktop=d, router=r)

    assert asyncio.run(s._apply_isolation("ui_control")) is None
    assert d.ensured == 1
    assert r.isolated is True


def test_a_failed_session_is_reported_not_hidden():
    class Broken(FakeDesktop):
        def ensure(self):
            return None

    d, r = Broken(), FakeRouter()
    s = _session(desktop=d, router=r)
    result = asyncio.run(s._apply_isolation("open_app"))

    assert result["status"] == "error"
    assert "Do NOT claim" in result["instruction"]
    assert r.isolated is False       # never pretended
    assert s._turn_isolated is False  # and does not keep trying


# ------------------------------------------------------- turn end


def test_turn_end_returns_to_the_users_desktop_and_cleans_up():
    d, r = FakeDesktop(), FakeRouter()
    s = _session(desktop=d, router=r)
    asyncio.run(s._apply_isolation("desktop_control"))
    assert r.isolated is True

    s._end_isolated_turn()

    assert r.isolated is False
    assert d.cleaned == 1
    assert s._turn_isolated is False


def test_turn_end_is_a_no_op_for_an_ordinary_turn():
    d, r = FakeDesktop(), FakeRouter()
    s = _session(isolated_request=False, desktop=d, router=r)
    s._end_isolated_turn()
    assert d.cleaned == 0 and r.switches == []


# ------------------------------------------------------- open_app


def test_open_app_opens_on_the_private_desktop_when_isolated():
    d, r = FakeDesktop(), FakeRouter(isolated=True)
    tool = OpenAppTool(launcher=object(), router=r, isolated_desktop=d)

    out = tool.execute({"app": "Notepad"})

    assert out["status"] == "success"
    assert out["session"] == 11
    assert d.launched == ["Notepad"]
    # the claim Alfred repeats has to come from the tool, not the model
    assert "private desktop" in out["opened_in"]


def test_open_app_uses_the_normal_launcher_otherwise():
    class Launcher:
        def __init__(self):
            self.opened = []

        def open(self, app_name, target="alfred"):
            self.opened.append(app_name)

            class R:
                @staticmethod
                def as_dict():
                    return {"status": "success", "app": app_name}
            return R()

    launcher = Launcher()
    d, r = FakeDesktop(), FakeRouter(isolated=False)
    tool = OpenAppTool(launcher=launcher, router=r, isolated_desktop=d)

    tool.execute({"app": "Notepad"})

    assert launcher.opened == ["Notepad"]
    assert d.launched == []


# ------------------------------------------------- handing off to a task


def test_isolation_travels_with_a_backgrounded_task():
    """The model rewrites run_task's goal in its own words and drops the
    phrase while doing it, so re-reading the goal finds nothing - which
    is how "without disturbing me" opened Chrome on the user's screen."""
    s = _session()
    arguments = {"goal": "research Deji's channel and open his latest video"}

    assert asyncio.run(s._apply_isolation("run_task", arguments)) is None
    assert arguments["_isolated"] is True


def test_an_ordinary_task_is_not_marked_isolated():
    s = _session(isolated_request=False)
    arguments = {"goal": "tidy my downloads"}

    asyncio.run(s._apply_isolation("run_task", arguments))

    assert "_isolated" not in arguments


def test_the_turn_does_not_tidy_up_under_a_running_task():
    """The task owns the private desktop now and cleans up after itself;
    cleaning up here would close the apps it is still using."""
    d, r = FakeDesktop(), FakeRouter()
    s = _session(desktop=d, router=r)
    asyncio.run(s._apply_isolation("run_task", {"goal": "research"}))

    s._end_isolated_turn()

    assert d.cleaned == 0
    assert s._turn_isolated is False
