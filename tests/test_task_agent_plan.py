"""P4 contract: plan is spoken, reporting is honest, lies don't verify."""

from src.brain.agent import TaskAgent
from src.brain.policy import Policy
from tests._taskfakes import KNOWN, DispatchChat, FakeRegistry


def _use(tool, args, why="do it"):
    return {"action": "use_tool", "tool": tool, "args": args, "rationale": why}


def _agent(chat, reg):
    return TaskAgent(
        chat, reg, Policy("full", KNOWN, surface="brain"),
        policy_voice=Policy("full", KNOWN, surface="voice"),
    )


def test_plan_summary_and_step_progress_are_spoken():
    said = []
    chat = DispatchChat(
        plan=[[
            {"step": "open Spotify", "done_when": "window exists"},
            {"step": "search drake", "done_when": "results shown"},
        ]],
        steps={
            "open Spotify": [_use("ui_control", {"action": "tree"})],
            "search drake": [_use("ui_control", {"action": "type"})],
        },
        verify=True,
    )
    _agent(chat, FakeRegistry()).run(
        "play drake", source="voice", on_progress=said.append,
    )

    assert said and said[0].startswith("Plan:")
    assert "open Spotify" in said[0] and "search drake" in said[0]
    assert any("Step 2/2" in line for line in said)


def test_zero_action_claim_never_verifies_even_if_model_would_pass():
    """Executor says 'done' without a single working tool call -> not verified,
    regardless of what the verify model says."""
    chat = DispatchChat(
        plan=[
            [{"step": "play the top track", "done_when": "audio is playing"}],
            [{"step": "play the top track", "done_when": "audio is playing"}],
            [{"step": "play the top track", "done_when": "audio is playing"}],
        ],
        steps={"play the top track": [
            {"action": "done", "evidence": "trust me, it's playing"},
        ]},
        verify=True,  # even though the (fake) verifier would say VERIFIED
    )
    result = _agent(chat, FakeRegistry()).run("play drake", source="voice")

    assert result.status == "failed"
    assert result.verified == []
    assert chat.verify_calls == 0  # short-circuited, no model call wasted
    assert "Done" not in result.summary


def test_as_dict_exposes_plan_verified_unverified():
    chat = DispatchChat(
        plan=[
            [{"step": "a", "done_when": "x"}, {"step": "b", "done_when": "y"}],
            [{"step": "b", "done_when": "y"}],
            [{"step": "b", "done_when": "y"}],
        ],
        steps={
            "a": [_use("system_info", {})],
            "b": [_use("system_info", {})] * 6,
        },
        verify=lambda step: step == "a",
    )
    result = _agent(chat, FakeRegistry()).run("thing", source="voice")
    d = result.as_dict()

    assert d["plan"] == ["a", "b"]
    assert d["verified"] == ["a"]
    assert d["unverified"] and "b" in d["unverified"][0]
    assert d["status"] == "partial"
