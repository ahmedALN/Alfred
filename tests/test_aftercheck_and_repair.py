"""Checking the world, and fixing what the check finds.

Alfred used to report a game as opened because the launcher said
"Launched; window not confirmed yet" - which it said for anything it
started, including a game that had already exited. Every check was a
reading of the transcript, so a tool that said the wrong thing was
never caught.

These cover the two halves of the answer: go and look, and when what
you see is wrong, do the one thing that was missing first.
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.brain import aftercheck, repair
from src.brain.agent import TaskAgent
from src.brain.policy import Policy
from tests._taskfakes import KNOWN, DispatchChat, FakeRegistry

DEAD = r"C:\nope\a-program-that-is-not-running.exe"
ALIVE = r"C:\Windows\explorer.exe"


def _use(tool, args, why="do it"):
    return {"action": "use_tool", "tool": tool, "args": args, "rationale": why}


def _agent(chat, reg, learner=None):
    return TaskAgent(
        chat, reg, Policy("full", KNOWN, surface="brain"),
        policy_voice=Policy("full", KNOWN, surface="voice"),
        learner=learner,
    )


# ------------------------------------------------------- going to look


def test_an_app_that_exited_is_not_open_however_cheerful_the_result():
    """The exact shape the launcher used to call a success."""
    found = aftercheck.check(
        "open_app",
        {"app": "How to Fish"},
        {"status": "success", "app": "How to Fish", "executable": DEAD,
         "pid": None, "hwnd": None,
         "note": "Launched; window not confirmed yet."},
        settle=0,
    )

    assert found is not None and not found.ok
    assert "not running" in str(found).lower()


def test_an_app_that_is_actually_running_passes():
    found = aftercheck.check(
        "open_app", {"app": "Explorer"},
        {"status": "success", "app": "Explorer", "executable": ALIVE},
        settle=0,
    )

    assert found is not None and found.ok


def test_a_store_app_is_not_judged_at_all():
    """The shell starts something this cannot name.

    Saying "not running" about a process it never had a handle on
    would fail working steps, which is worse than not checking.
    """
    assert aftercheck.check(
        "open_app", {"app": "Spotify"},
        {"status": "success", "app": "Spotify",
         "executable": "Spotify.exe!App", "method": "appsfolder"},
        settle=0,
    ) is None


def test_a_reused_window_is_not_a_fresh_process_to_follow():
    assert aftercheck.check(
        "open_app", {"app": "Chrome"},
        {"status": "success", "app": "Chrome", "executable": ALIVE,
         "method": "existing-window"},
        settle=0,
    ) is None


def test_a_tool_that_already_admitted_failure_is_left_alone():
    assert aftercheck.check(
        "open_app", {"app": "X"},
        {"status": "not_found", "executable": DEAD}, settle=0,
    ) is None


def test_a_file_that_was_written_is_checked_on_disk(tmp_path):
    made = tmp_path / "report.txt"
    made.write_text("hello", encoding="utf-8")

    good = aftercheck.check(
        "powershell", {}, {"status": "success", "path": str(made)}
    )
    assert good is not None and good.ok

    missing = aftercheck.check(
        "powershell", {}, {"status": "success",
                           "path": str(tmp_path / "never-made.txt")}
    )
    assert missing is not None and not missing.ok


def test_a_relative_name_is_not_guessed_at():
    assert aftercheck.check(
        "powershell", {}, {"status": "success", "path": "output.txt"}
    ) is None


# ---------------------------------------------------- what to do first


def test_a_steam_game_wants_steam():
    fix = repair.prerequisite(
        "How to Fish",
        executable=r"C:\Program Files (x86)\Steam\steamapps\common\Fish\f.exe",
        note="it exited immediately",
    )
    assert fix is not None
    assert fix.args["app"] == "Steam"
    assert fix.step == "Open Steam"


def test_a_steam_url_wants_steam_too():
    fix = repair.prerequisite(
        "Some Game", executable="steam://rungameid/12345", note="",
    )
    assert fix is not None and fix.args["app"] == "Steam"


def test_an_epic_game_wants_the_epic_launcher():
    fix = repair.prerequisite(
        "Fortnite",
        executable=r"C:\Program Files\Epic Games\Fortnite\game.exe", note="",
    )
    assert fix is not None and "Epic" in fix.args["app"]


def test_the_error_is_read_when_the_path_says_nothing():
    """This is the general case: anything that says what it needs."""
    fix = repair.prerequisite(
        "Some Client", executable=r"C:\Apps\client.exe",
        note="Error: Docker Desktop must be running to continue.",
    )
    assert fix is not None
    assert fix.args["app"] == "Docker Desktop"


def test_please_start_x_first_is_also_understood():
    fix = repair.prerequisite(
        "Thing", executable=r"C:\Apps\thing.exe",
        note="Please start Mullvad VPN first.",
    )
    assert fix is not None and "Mullvad" in fix.args["app"]


def test_nothing_is_invented_when_there_is_no_evidence():
    """Without a model to ask and nothing in the text, say nothing.

    Guessing here means inserting a step that opens some unrelated
    app, which is worse than reporting the failure honestly.
    """
    assert repair.prerequisite(
        "Mystery", executable=r"C:\Apps\mystery.exe", note="it exited",
    ) is None


def test_it_does_not_ask_for_the_thing_that_just_failed():
    """Steam failing to open is not fixed by opening Steam."""
    assert repair.prerequisite(
        "Steam", executable=r"C:\Program Files (x86)\Steam\steam.exe",
        note="it exited immediately",
    ) is None


class _Says:
    def __init__(self, reply):
        self.reply = reply
        self.asked = []

    def generate(self, prompt, **_kw):
        self.asked.append(prompt)
        return self.reply


def test_the_model_is_asked_only_when_nothing_else_knows():
    chat = _Says("Battle.net")
    fix = repair.prerequisite(
        "Some Game", executable=r"C:\Games\g.exe", note="it exited",
        chat=chat,
    )
    assert fix is not None and fix.args["app"] == "Battle.net"
    assert not fix.certain, "a guess must not be recorded as a fact"
    assert fix.lesson == "", "a guess is not yet worth writing down"


def test_the_model_saying_none_is_believed():
    assert repair.prerequisite(
        "Some Game", executable=r"C:\Games\g.exe", note="it crashed",
        chat=_Says("NONE"),
    ) is None


def test_a_known_launcher_is_not_paid_for_with_a_model_call():
    chat = _Says("Something Else")
    fix = repair.prerequisite(
        "A Game", executable=r"D:\SteamLibrary\steamapps\common\a\a.exe",
        note="", chat=chat,
    )
    assert fix is not None and fix.args["app"] == "Steam"
    assert chat.asked == [], "the path already said Steam"


# ------------------------------------------------------- the whole run


class _GameNeedsSteam:
    """open_app fails until Steam has been opened - like the real thing."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self.steam_open = False

    def gemini_declarations(self):
        return [{"name": "open_app", "description": "launch an app"}]

    def names(self):
        return ["open_app", "ui_control", "powershell", "system_info",
                "desktop_control"]

    def execute(self, name: str, args: dict[str, Any]) -> Any:
        self.executed.append((name, args))
        app = str(args.get("app", ""))

        if name != "open_app":
            return {"status": "success"}

        if app == "Steam":
            self.steam_open = True
            return {"status": "success", "app": "Steam",
                    "executable": ALIVE, "window_title": "Steam"}

        exe = r"D:\SteamLibrary\steamapps\common\Fish\Fish.exe"
        if self.steam_open:
            return {"status": "success", "app": app, "executable": ALIVE,
                    "window_title": app}
        return {"status": "success", "app": app, "executable": exe,
                "pid": None, "note": "Launched; window not confirmed yet."}


class _OneCallPerStep(DispatchChat):
    """An executor that acts once per step, like a competent one.

    DispatchChat pops a reply per call, and the agent asks again when
    it rejects a repeat - so a scripted list gets eaten by the first
    step and every later step is answered "done" with no action, which
    is a property of the fake and of nothing else.
    """

    def __init__(self, *, calls: dict[str, Any], **kw: Any) -> None:
        super().__init__(steps={}, **kw)
        self._calls = calls
        self._acted: set[tuple[str, int]] = set()
        self._visits: dict[str, int] = {}
        self._last = ""

    def generate(self, prompt: str, **kw: Any) -> str:
        if "Alfred's task executor" not in prompt:
            return super().generate(prompt, **kw)

        step = ""
        for line in prompt.split("\n"):
            if line.startswith("CURRENT STEP:"):
                step = line.split(":", 1)[1].strip()
                break

        if step != self._last:          # a new visit to a step
            self._visits[step] = self._visits.get(step, 0) + 1
            self._last = step

        key = (step, self._visits.get(step, 1))
        for name, call in self._calls.items():
            if name in step and key not in self._acted:
                self._acted.add(key)
                return json.dumps(call)
        return json.dumps({"action": "done", "evidence": "acted already"})


class _Learner:
    def __init__(self):
        self.remembered = []

    def remember(self, content, category="general", source="conversation"):
        self.remembered.append(content)
        return {"status": "stored"}

    def recall(self, *_a, **_k):
        return []


def test_a_game_that_did_not_open_gets_its_launcher_started_first():
    """The whole point, end to end.

    Told to open the game, the launcher reports success and the game
    is not there. Alfred should notice, work out that it is a Steam
    game, open Steam, and try again - not report it as done, and not
    throw the goal away and search the web.
    """
    chat = _OneCallPerStep(
        plan=[[{"step": "Open How to Fish",
                "done_when": "open_app returns success"}]] * 3,
        calls={"Open Steam": _use("open_app", {"app": "Steam"}),
               "Open How to Fish": _use("open_app", {"app": "How to Fish"})},
        verify=True,
    )
    reg = _GameNeedsSteam()
    learner = _Learner()
    result = _agent(chat, reg, learner).run("open how to fish", source="voice")

    opened = [a.get("app") for n, a in reg.executed if n == "open_app"]
    assert "Steam" in opened, (
        f"it should have started Steam and tried again; it ran {opened}"
    )
    assert opened.index("Steam") < len(opened) - 1, (
        "Steam should come before the retry, not last"
    )
    assert result.status in ("done", "partial"), result.status
    assert any("Steam" in line for line in learner.remembered), (
        f"the prerequisite is worth keeping; kept {learner.remembered}"
    )


def test_an_app_that_never_opens_is_not_reported_as_done():
    """No repair available, so the honest answer is that it failed."""
    chat = DispatchChat(
        plan=[[{"step": "Open Mystery",
                "done_when": "open_app returns success"}]] * 3,
        steps={"Open Mystery": [_use("open_app", {"app": "Mystery"})] * 3},
        verify=True,
    )
    reg = FakeRegistry(results={"open_app": {
        "status": "success", "app": "Mystery", "executable": DEAD,
        "pid": None, "note": "Launched; window not confirmed yet."}})

    result = _agent(chat, reg).run("open mystery", source="voice")

    assert result.status != "done", (
        f"it did not open and was reported {result.status}: {result.summary}"
    )
    assert "Open Mystery" not in result.verified


def test_a_working_app_is_left_alone(tmp_path):
    """The guard must not cost anything when things are fine."""
    chat = DispatchChat(
        plan=[[{"step": "Open Explorer",
                "done_when": "open_app returns success"}]],
        steps={"Open Explorer": [_use("open_app", {"app": "Explorer"})]},
        verify=True,
    )
    reg = FakeRegistry(results={"open_app": {
        "status": "success", "app": "Explorer", "executable": ALIVE,
        "window_title": "File Explorer"}})

    result = _agent(chat, reg).run("open explorer", source="voice")

    assert result.status == "done", result.summary
    assert [n for n, _ in reg.executed] == ["open_app"], (
        "a healthy step should not be retried"
    )


def test_os_paths_are_what_the_checks_actually_read():
    """A guard built on a path that does not exist proves nothing."""
    assert os.path.exists(ALIVE), "explorer.exe should be on every Windows box"
    assert not os.path.exists(DEAD)


# ------------------------------------- the guard that blocked the fix


def test_a_path_in_a_result_is_not_proof_that_a_launcher_is_open():
    r"""The already-open guard read whole log lines, results included.

    A game launched from D:\SteamLibrary\steamapps\ produced a
    result line containing "steam", which the guard took as proof
    that Steam was already running - so it refused to start the
    launcher the game was waiting for, which is the one thing that
    had to happen. Only the call's own arguments say what was opened.
    """
    from src.brain.agent import _opened_already

    line = (
        '[step 1] open_app({"app": "How to Fish"}) -> ok: '
        '{"status": "success", "executable": '
        '"D:\\SteamLibrary\\steamapps\\common\\Fish\\Fish.exe"}'
    )

    assert not _opened_already("steam", line)
    assert _opened_already("how to fish", line)


def test_a_launch_that_did_not_last_stops_counting_as_open():
    """Corrected in the record, so the retry is allowed to happen."""
    from src.brain.agent import _opened_already

    line = '[step 1] open_app({"app": "Fish"}) -> ok, but it did not last: {}'

    assert not _opened_already("fish", line)
