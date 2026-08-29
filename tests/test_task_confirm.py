import json

from src.brain.agent import TaskAgent
from src.brain.policy import Policy


class Chat:
    name = "c"
    model = "c"

    def __init__(self, replies):
        self._r = list(replies)

    def generate(self, prompt, **kw):
        return self._r.pop(0) if self._r else json.dumps(
            {"action": "done", "summary": "ok"}
        )


class Reg:
    def __init__(self):
        self.executed = []

    def gemini_declarations(self):
        return [{"name": "powershell", "description": "run a command"}]

    def names(self):
        return ["powershell"]

    def execute(self, name, args):
        self.executed.append((name, args))
        return {"status": "success"}


KNOWN = {"powershell"}


def _agent(chat, reg):
    return TaskAgent(
        chat, reg,
        Policy("full", KNOWN, surface="brain"),
        policy_voice=Policy("full", KNOWN, surface="voice"),
        max_steps=6,
    )


DANGEROUS = json.dumps({
    "action": "use_tool", "tool": "powershell",
    "args": {"command": "Move-Item C:\\a C:\\b -Recurse"},
    "rationale": "move the files",
})
DONE = json.dumps({"action": "done", "summary": "moved them"})


def test_brain_task_skips_dangerous_step_silently():
    reg = Reg()
    _agent(Chat([DANGEROUS, DONE]), reg).run("move files", source="brain")
    assert reg.executed == []  # skipped, no ask_user given


def test_voice_task_asks_and_runs_on_yes():
    reg = Reg()
    asked = []
    agent = _agent(Chat([DANGEROUS, DONE]), reg)
    result = agent.run(
        "move files", source="voice",
        ask_user=lambda q: asked.append(q) or True,
    )
    assert asked and "risky" in asked[0].lower()
    assert reg.executed == [("powershell", {"command": "Move-Item C:\\a C:\\b -Recurse"})]


def test_voice_task_skips_on_no():
    reg = Reg()
    agent = _agent(Chat([DANGEROUS, DONE]), reg)
    result = agent.run("move files", source="voice", ask_user=lambda q: False)
    assert reg.executed == []
    assert result.skipped_confirmations and "you said no" in result.skipped_confirmations[0]


def test_catastrophic_still_refused_even_with_ask_user():
    reg = Reg()
    chat = Chat([
        json.dumps({"action": "use_tool", "tool": "powershell",
                    "args": {"command": "Format-Volume -DriveLetter D -Force"}}),
        DONE,
    ])
    agent = _agent(chat, reg)
    agent.run("wipe D", source="voice", ask_user=lambda q: True)
    assert reg.executed == []  # never asked, never run
