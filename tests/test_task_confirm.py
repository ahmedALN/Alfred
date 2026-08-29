"""Mid-task voice confirmation: dangerous steps ask the user out loud."""

from src.brain.agent import TaskAgent
from src.brain.policy import Policy
from tests._taskfakes import KNOWN, DispatchChat, FakeRegistry


def _use(tool, args, why="do it"):
    return {"action": "use_tool", "tool": tool, "args": args, "rationale": why}


def _agent(chat, reg):
    return TaskAgent(
        chat, reg,
        Policy("full", KNOWN, surface="brain"),
        policy_voice=Policy("full", KNOWN, surface="voice"),
        max_steps=6,
    )


def _dangerous_plan():
    return DispatchChat(
        plan=[[{"step": "move the files", "done_when": "files moved"}]],
        steps={"move the files": [
            _use("powershell", {"command": "Move-Item C:\\a C:\\b -Recurse"},
                 "move the files"),
            {"action": "done", "evidence": "moved"},
        ]},
        verify=True,
    )


def test_brain_task_skips_dangerous_step_silently():
    reg = FakeRegistry()
    _agent(_dangerous_plan(), reg).run("move files", source="brain")
    assert reg.executed == []  # no ask_user on the brain surface -> skipped


def test_voice_task_asks_and_runs_on_yes():
    reg = FakeRegistry()
    asked = []
    agent = _agent(_dangerous_plan(), reg)
    agent.run(
        "move files", source="voice",
        ask_user=lambda q: asked.append(q) or True,
    )
    assert asked and "risky" in asked[0].lower()
    assert reg.executed == [
        ("powershell", {"command": "Move-Item C:\\a C:\\b -Recurse"})
    ]


def test_voice_task_skips_on_no():
    reg = FakeRegistry()
    agent = _agent(_dangerous_plan(), reg)
    result = agent.run("move files", source="voice", ask_user=lambda q: False)
    assert reg.executed == []
    assert result.skipped_confirmations
    assert "you said no" in result.skipped_confirmations[0]


def test_catastrophic_still_refused_even_with_ask_user():
    reg = FakeRegistry()
    chat = DispatchChat(
        plan=[[{"step": "wipe D", "done_when": "empty"}]],
        steps={"wipe D": [
            _use("powershell", {"command": "Format-Volume -DriveLetter D -Force"}),
        ]},
        verify=False,
    )
    agent = _agent(chat, reg)
    asked = []
    agent.run("wipe D", source="voice", ask_user=lambda q: asked.append(q) or True)
    assert reg.executed == []  # never asked, never run
    assert asked == []
