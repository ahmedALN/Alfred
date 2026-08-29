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
