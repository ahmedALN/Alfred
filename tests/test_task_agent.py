import json

from src.brain.agent import TaskAgent, _parse
from src.brain.policy import Policy
from tests._taskfakes import KNOWN, DispatchChat, FakeRegistry


def _agent(chat, registry, **kw):
    return TaskAgent(
        chat, registry, Policy("full", KNOWN, surface="brain"),
        policy_voice=Policy("full", KNOWN, surface="voice"),
        **kw,
    )


def _use(tool, args, why="do the thing"):
    return {"action": "use_tool", "tool": tool, "args": args, "rationale": why}


# --------------------------------------------------------------------
# _parse
# --------------------------------------------------------------------

def test_parse_handles_fences_and_prose():
    assert _parse('```json\n{"action":"done","summary":"x"}\n```')["action"] == (
        "done"
    )
    assert _parse('here you go: {"action":"done"} thanks')["action"] == "done"
    assert _parse("not json") is None
    assert _parse('["a","b"]') is None


# --------------------------------------------------------------------
# plan -> execute -> verify
# --------------------------------------------------------------------

def test_plans_then_executes_then_verifies_every_step():
    chat = DispatchChat(
        plan=[[
            {"step": "open Spotify", "done_when": "Spotify window exists"},
            {"step": "play the top track", "done_when": "a track is playing"},
        ]],
        steps={
            "open Spotify": [_use("ui_control", {"action": "tree"}),
                             {"action": "done", "evidence": "window listed"}],
            "play the top track": [_use("ui_control", {"action": "click",
                                                       "name": "Play"}),
                                   {"action": "done", "evidence": "playing"}],
        },
        verify=True,
    )
    reg = FakeRegistry()
    result = _agent(chat, reg).run("play a drake song", source="voice")

    assert result.status == "done"
    assert result.plan == ["open Spotify", "play the top track"]
    assert result.verified == ["open Spotify", "play the top track"]
    assert result.unverified == []
    assert "Done" in result.summary
    assert [c[0] for c in reg.executed] == ["ui_control", "ui_control"]


def test_unverified_step_is_never_reported_done():
    """The Drake bug: executor claims done, nothing actually happened."""
    chat = DispatchChat(
        plan=[
            [{"step": "play the top track", "done_when": "a track is playing"}],
            [{"step": "play the top track", "done_when": "a track is playing"}],
            [{"step": "play the top track", "done_when": "a track is playing"}],
        ],
        steps={"play the top track": [
            {"action": "done", "evidence": "I opened it"},  # a lie
        ]},
        verify=False,
    )
    reg = FakeRegistry()
    result = _agent(chat, reg).run("play a drake song", source="voice")

    assert result.status == "failed"
    assert result.verified == []
    assert result.unverified and "play the top track" in result.unverified[0]
    assert "Done" not in result.summary
    assert "Couldn't" in result.summary


def test_partial_when_some_steps_verify():
    calls = {"n": 0}

    def verify(step):
        if "play" in step:
            return False
        return True

    chat = DispatchChat(
        plan=[
            [
                {"step": "open Spotify", "done_when": "window exists"},
                {"step": "play the track", "done_when": "audio playing"},
            ],
            [{"step": "play the track", "done_when": "audio playing"}],
            [{"step": "play the track", "done_when": "audio playing"}],
        ],
        steps={
            "open Spotify": [_use("ui_control", {"action": "tree"})],
            "play the track": [_use("ui_control", {"action": "click"})],
        },
        verify=verify,
    )
    result = _agent(chat, FakeRegistry()).run("music", source="voice")

    assert result.status == "partial"
    assert result.verified == ["open Spotify"]
    assert result.unverified and "play the track" in result.unverified[0]
    assert "Partly done" in result.summary


def test_replan_recovers_and_finishes():
    seen = {"count": 0}

    def verify(step):
        if "search" in step:
            seen["count"] += 1
            return seen["count"] > 1  # fails the first time, then passes
        return True

    chat = DispatchChat(
        plan=[
            [{"step": "search for drake", "done_when": "results shown"}],
            [{"step": "search for drake", "done_when": "results shown"}],
        ],
        steps={"search for drake": [
            _use("ui_control", {"action": "type", "text": "drake"}),
            {"action": "done", "evidence": "typed"},
            _use("ui_control", {"action": "type", "text": "drake"}),
            {"action": "done", "evidence": "typed again"},
        ]},
        verify=verify,
    )
    result = _agent(chat, FakeRegistry()).run("find drake", source="voice")

    assert result.status == "done"
    assert result.verified == ["search for drake"]
    assert chat.plan_calls == 2  # initial + one replan


# --------------------------------------------------------------------
# safety
# --------------------------------------------------------------------

def test_brain_surface_skips_dangerous_step_without_ask_user():
    chat = DispatchChat(
        plan=[[{"step": "stop the spooler", "done_when": "service stopped"}]],
        steps={"stop the spooler": [
            _use("powershell", {"command": "Stop-Service -Name Spooler"},
                 "stop it"),
            {"action": "give_up", "reason": "was not allowed"},
        ]},
        verify=False,
    )
    reg = FakeRegistry()
    result = _agent(chat, reg).run("disable print spooler", source="brain")

    assert reg.executed == []
    assert result.skipped_confirmations
    assert "powershell" in result.skipped_confirmations[0]
    assert "Left for you" in result.summary


def test_voice_surface_asks_then_runs_dangerous_step_on_yes():
    asked = []
    chat = DispatchChat(
        plan=[[{"step": "move the files", "done_when": "files in archive"}]],
        steps={"move the files": [
            _use("powershell",
                 {"command": "Move-Item C:\\a C:\\b -Recurse"}, "move them"),
            {"action": "done", "evidence": "moved"},
        ]},
        verify=True,
    )
    reg = FakeRegistry()
    result = _agent(chat, reg).run(
        "archive the files", source="voice",
        ask_user=lambda q: asked.append(q) or True,
    )

    assert asked and "risky" in asked[0].lower()
    assert reg.executed == [
        ("powershell", {"command": "Move-Item C:\\a C:\\b -Recurse"})
    ]
    assert result.status == "done"


def test_voice_surface_skips_dangerous_step_on_no():
    chat = DispatchChat(
        plan=[[{"step": "move the files", "done_when": "files in archive"}]],
        steps={"move the files": [
            _use("powershell",
                 {"command": "Remove-Item C:\\junk -Recurse"}, "clean up"),
        ]},
        verify=False,
    )
    reg = FakeRegistry()
    result = _agent(chat, reg).run(
        "clean up", source="voice", ask_user=lambda q: False,
    )

    assert reg.executed == []
    assert result.skipped_confirmations
    assert "you said no" in result.skipped_confirmations[0]


def test_catastrophic_step_refused_even_on_voice_with_ask_user():
    asked = []
    chat = DispatchChat(
        plan=[[{"step": "wipe D", "done_when": "drive empty"}]],
        steps={"wipe D": [
            _use("powershell", {"command": "Format-Volume -DriveLetter D"}),
        ]},
        verify=False,
    )
    reg = FakeRegistry()
    _agent(chat, reg).run(
        "wipe drive D", source="voice",
        ask_user=lambda q: asked.append(q) or True,
    )

    assert reg.executed == []
    assert asked == []  # never even asked


# --------------------------------------------------------------------
# budgets & control flow
# --------------------------------------------------------------------

def test_cancelled_before_any_work():
    chat = DispatchChat(
        plan=[[{"step": "do a thing", "done_when": "thing done"}]],
    )
    result = _agent(chat, FakeRegistry()).run(
        "something", cancel_check=lambda: True,
    )
    assert result.status == "cancelled"
    assert "Stopped at your request" in result.summary


def test_planner_failure_falls_back_to_single_step():
    chat = DispatchChat(
        plan_raises=RuntimeError("nemotron down"),
        steps={"reboot the router": [
            _use("system_info", {"query": "net"}),
            {"action": "done", "evidence": "done"},
        ]},
        verify=True,
    )
    result = _agent(chat, FakeRegistry()).run("reboot the router")

    assert result.plan == ["reboot the router"]
    assert result.status == "done"
    assert result.verified == ["reboot the router"]


def test_max_steps_caps_total_tool_calls():
    chat = DispatchChat(
        plan=[[
            {"step": "step one", "done_when": "a"},
            {"step": "step two", "done_when": "b"},
            {"step": "step three", "done_when": "c"},
        ]],
        steps={
            "step one": [_use("system_info", {})] * 10,
            "step two": [_use("system_info", {})] * 10,
            "step three": [_use("system_info", {})] * 10,
        },
        verify=False,
    )
    reg = FakeRegistry()
    result = _agent(chat, reg, max_steps=4).run("loop", source="voice")

    assert len(reg.executed) <= 4
    assert result.status in ("failed", "partial")


# ----------------------------------------- reading the finding back out


def _finding_for(goal, steps, answer="Yes, Steam is running."):
    """_finding in isolation: it only needs a chat provider and steps."""
    from src.brain.agent import TaskAgent, TaskResult

    class _Chat:
        def __init__(self):
            self.prompts = []

        def generate(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return answer

    chat = _Chat()
    agent = TaskAgent.__new__(TaskAgent)
    agent._plan_chat = chat
    result = TaskResult(goal=goal, status="done", summary="", steps=steps)
    return agent._finding(result), chat


def _step(tool, output, ok=True):
    from src.brain.agent import Step
    return Step(1, "", tool, {}, "allow", output, ok)


def test_the_finding_comes_from_what_the_tools_returned():
    finding, chat = _finding_for(
        "Check whether Steam is running.",
        [_step("powershell", "steam.exe   12345  Running")],
    )

    assert finding == "Yes, Steam is running."
    assert "steam.exe" in chat.prompts[0]


def test_work_with_nothing_to_report_says_nothing():
    """Opening Notepad has no finding, and inventing one would be worse
    than staying quiet."""
    finding, _ = _finding_for(
        "Open Notepad.", [_step("open_app", {"status": "success"})],
        answer="NOTHING",
    )

    assert finding == ""


def test_steps_that_failed_are_not_read_as_findings():
    finding, chat = _finding_for(
        "Check whether Steam is running.",
        [_step("powershell", "Access denied", ok=False),
         _step("powershell", "steam.exe Running")],
    )

    assert "Access denied" not in chat.prompts[0]
    assert "steam.exe Running" in chat.prompts[0]


def test_a_task_with_no_steps_at_all_has_no_finding():
    finding, chat = _finding_for("Do nothing.", [])

    assert finding == ""
    assert chat.prompts == []


def test_a_model_that_falls_over_costs_only_the_finding():
    from src.brain.agent import TaskAgent, TaskResult

    class _Broken:
        def generate(self, *a, **k):
            raise RuntimeError("no route to host")

    agent = TaskAgent.__new__(TaskAgent)
    agent._plan_chat = _Broken()
    result = TaskResult(
        goal="Check Steam.", status="done", summary="",
        steps=[_step("powershell", "running")],
    )

    assert agent._finding(result) == ""


# ------------------------------------- not calling a bad end a good one


def _result_with(steps, plan=None, verified=None):
    from src.brain.agent import TaskAgent, TaskResult

    agent = TaskAgent.__new__(TaskAgent)
    agent._limitations = None
    agent._plan_chat = None
    agent._first_plan_len = len(plan or [])
    result = TaskResult(
        goal="Save the clipboard image to Downloads.",
        status="running", summary="", steps=steps,
        plan=list(plan or []), verified=list(verified or []),
    )
    # _finding needs a chat provider; this test is about status only.
    agent._finding = lambda _r: ""
    agent._finalize(result)
    return result


def test_a_task_whose_last_act_failed_is_not_done():
    """Alfred announced a saved screenshot over a step that returned
    code 1, then learned the whole run as a reusable skill."""
    steps = [
        _step("powershell", {"status": "success"}),
        _step("powershell", {"status": "error"}, ok=False),
    ]
    result = _result_with(steps, plan=["save it"], verified=["save it"])

    assert result.status == "partial"
    assert any("failed" in u for u in result.unverified)


def test_a_task_that_recovered_and_finished_is_still_done():
    steps = [
        _step("powershell", {"status": "error"}, ok=False),
        _step("powershell", {"status": "success"}),
    ]
    result = _result_with(steps, plan=["save it"], verified=["save it"])

    assert result.status == "done"


def test_a_task_with_no_tool_calls_is_judged_on_its_plan_as_before():
    result = _result_with([], plan=["say hello"], verified=["say hello"])

    assert result.status == "done"


# ----------------------------------------- recording the right wall


def test_the_wall_records_what_actually_went_wrong():
    """It recorded the string "auto" - the step's verdict - so every
    PowerShell failure on the machine was one indistinguishable wall."""
    from src.brain.agent import _why_it_failed

    command = "$Path = [Environment]::GetFolderPath('Downloads')" + " " * 30
    step = _step("powershell", {
        "status": "error", "success": False, "command": command,
        "stderr": command + " : Cannot convert value Downloads to SpecialFolder",
    }, ok=False)

    detail = _why_it_failed(step)

    assert "Cannot convert value Downloads" in detail
    assert "auto" != detail
    assert command not in detail          # the echo is not the error


def test_two_different_powershell_failures_are_two_different_walls():
    from src.brain.agent import _why_it_failed
    from src.brain.limitations import shape_of

    enum = _step("powershell", {"status": "error", "stderr": "Cannot convert value Downloads"}, ok=False)
    denied = _step("powershell", {"status": "error", "stderr": "Access is denied"}, ok=False)

    assert shape_of("powershell", _why_it_failed(enum)) != \
        shape_of("powershell", _why_it_failed(denied))


def test_a_failure_with_nothing_to_say_still_records_something():
    from src.brain.agent import _why_it_failed

    assert _why_it_failed(_step("open_app", {"status": "not_found"}, ok=False)) \
        == "not_found"


# ---------------------------------------- the answer, not the working out


def test_the_answer_is_taken_from_behind_the_marker():
    """"detailed thinking off" is a request, not a guarantee. The bench
    got back "We need to answer: ..." as Alfred's reply to the user."""
    from src.brain.agent import _answer_line

    raw = ('We need to answer: "What version of Windows is this?"\n'
           "The output shows Build 26200.\n"
           "ANSWER: You're on Windows 11 Pro, build 26200.")

    assert _answer_line(raw) == "You're on Windows 11 Pro, build 26200."


def test_thinking_out_loud_falls_back_to_the_conclusion_not_the_preamble():
    """When a model does think aloud, the conclusion is the last thing
    it says and never the first."""
    from src.brain.agent import _answer_line

    raw = "We need to work out the version.\nSo: Windows 11 Pro."

    assert _answer_line(raw) == "So: Windows 11 Pro."


def test_a_straight_answer_is_left_alone():
    from src.brain.agent import _answer_line

    assert _answer_line("Yes, Steam is running.") == "Yes, Steam is running."


def test_nothing_said_is_nothing_returned():
    from src.brain.agent import _answer_line

    assert _answer_line("") == ""
    assert _answer_line("   \n  \n") == ""
