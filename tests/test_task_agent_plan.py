"""P4 contract: plan is spoken, reporting is honest, lies don't verify."""

from src.brain.agent import Step, TaskAgent, TaskResult
from src.brain.policy import Policy
from tests._taskfakes import KNOWN, DispatchChat, FakeRegistry


class RecordingLearner:
    def __init__(self):
        self.remembered = []

    def remember(self, content, category="general", source="conversation"):
        self.remembered.append(
            {"content": content, "category": category, "source": source}
        )
        return {"status": "stored"}


def _use(tool, args, why="do it"):
    return {"action": "use_tool", "tool": tool, "args": args, "rationale": why}


def _agent(chat, reg):
    return TaskAgent(
        chat, reg, Policy("full", KNOWN, surface="brain"),
        policy_voice=Policy("full", KNOWN, surface="voice"),
    )


def test_a_short_job_is_done_without_a_running_commentary():
    """Reading the plan out for "play drake" is noise, and it arrives
    before anything has happened - the least useful moment to be talked
    at."""
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

    assert said == []


def test_a_long_job_says_what_it_is_in_for():
    """Silence through something with many steps is worrying rather
    than restful."""
    said = []
    steps = ["open Spotify", "search drake", "play the track",
             "open Steam", "check the library"]
    chat = DispatchChat(
        plan=[[{"step": s, "done_when": "window exists"} for s in steps]],
        steps={s: [_use("ui_control", {"action": "tree"})] for s in steps},
        verify=True,
    )
    _agent(chat, FakeRegistry()).run(
        "sort out my whole desktop", source="voice", on_progress=said.append,
    )

    assert said and "5 steps" in said[0]
    assert any("Step 2/5" in line for line in said)


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


# --------------------------------------------------------------------
# P7: post-task reflection
# --------------------------------------------------------------------

def _result_with_steps():
    r = TaskResult(goal="open the report", status="failed", summary="")
    r.steps.append(
        Step(1, "click it", "ui_control", {"action": "click"}, "auto",
             {"status": "error"}, False)
    )
    return r


def test_reflection_stores_a_lesson_fact():
    learner = RecordingLearner()
    chat = DispatchChat()
    chat._plan = []  # planner path unused
    agent = TaskAgent(
        chat, FakeRegistry(), Policy("full", KNOWN, surface="brain"),
        learner=learner,
    )
    # reflection runs on the fast lane, not the planner
    agent._fast_chat = type("C", (), {
        "generate": lambda self, p, **k:
            "LESSON: Spotify search is opened with Ctrl+L, not a toolbar icon."
    })()

    line = agent.reflect(_result_with_steps())

    assert line.startswith("LESSON:")
    assert learner.remembered
    assert learner.remembered[0]["source"] == "task_reflection"
    assert "Ctrl+L" in learner.remembered[0]["content"]


def test_reflection_none_stores_nothing():
    learner = RecordingLearner()
    agent = TaskAgent(
        DispatchChat(), FakeRegistry(), Policy("full", KNOWN, surface="brain"),
        learner=learner,
    )
    agent._fast_chat = type("C", (), {
        "generate": lambda self, p, **k: "none"
    })()

    assert agent.reflect(_result_with_steps()) == "none"
    assert learner.remembered == []


def test_reflection_skipped_when_no_steps():
    agent = TaskAgent(
        DispatchChat(), FakeRegistry(), Policy("full", KNOWN, surface="brain"),
    )
    assert agent.reflect(TaskResult(goal="x", status="done", summary="")) == ""


# --------------------------------------------------------------------
# hardening: plan validation, loop guard, deterministic verify
# --------------------------------------------------------------------

def _agent2(chat, reg):
    return TaskAgent(chat, reg, Policy("full", KNOWN, surface="brain"),
                     policy_voice=Policy("full", KNOWN, surface="voice"))


def test_junk_plan_is_rejected_and_reasked():
    chat = DispatchChat(
        plan=[
            [{"step": "search_spotify_top_track", "done_when": "done"}],  # junk
            [{"step": "Open Spotify and play the top track",
              "done_when": "ui_control get shows a track playing"}],      # good
        ],
        steps={"Open Spotify": [_use("ui_control", {"action": "tree"})]},
        verify=True,
    )
    result = _agent2(chat, FakeRegistry()).run("play music", source="voice")
    assert result.plan == ["Open Spotify and play the top track"]
    assert chat.plan_calls == 2


def test_executor_loop_is_broken():
    # executor keeps proposing the exact same call forever
    same = _use("system_info", {"query": "disks"})
    chat = DispatchChat(
        plan=[[{"step": "check the disks", "done_when": "system_info returns disk data"}]],
        steps={"check the disks": [same] * 10},
        verify=False,
    )
    reg = FakeRegistry()
    result = _agent2(chat, reg).run("check disks", source="voice")
    # the same call runs at most twice before the loop guard abandons it
    assert len(reg.executed) <= 2


def test_deterministic_verify_passes_on_signal_match():
    agent = _agent2(DispatchChat(verify=False), FakeRegistry())
    hist = ['[step 1] system_info({"query":"disks"}) -> ok: {"status":"success",'
            ' "free_gb": 120, "disks": 2}']
    ok, why = agent._verify(
        {"step": "check disk space", "done_when": "system_info returns disks and free space"},
        hist,
    )
    assert ok and "successful tool result" in why


def test_rejects_tool_syntax_and_micro_actions_as_plan_steps():
    agent = _agent2(DispatchChat(), FakeRegistry())

    bad_tool_syntax = [{"step": "ui_control key keys='^a'",
                        "done_when": "text is selected"}]
    assert agent._plan_ok(bad_tool_syntax, "select all") is False
    assert "tool syntax" in agent._plan_gripe

    bad_micro = [{"step": "Find the edit control in Notepad",
                  "done_when": "the control is found"}]
    assert agent._plan_ok(bad_micro, "type in notepad") is False
    assert "micro-action" in agent._plan_gripe

    good = [
        {"step": "Open Notepad", "done_when": "open_app returns success"},
        {"step": "Type 'hello' into Notepad",
         "done_when": "ui_control get shows 'hello' in the editor"},
    ]
    assert agent._plan_ok(good, "type hello in notepad") is True
