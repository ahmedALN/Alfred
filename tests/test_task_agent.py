import json

from src.brain.agent import TaskAgent, _parse
from src.brain.policy import Policy


class ScriptChat:
    name = "script"
    model = "script"

    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts = []

    def generate(self, prompt, *, system=None, temperature=0.4, max_tokens=None):
        self.prompts.append(prompt)
        if self._replies:
            r = self._replies.pop(0)
            return r if isinstance(r, str) else json.dumps(r)
        return json.dumps({"action": "done", "summary": "ran out of script"})


class FakeRegistry:
    def __init__(self, results=None):
        self.results = results or {}
        self.executed = []

    def gemini_declarations(self):
        return [
            {"name": "desktop_control", "description": "see and click"},
            {"name": "system_info", "description": "read system"},
            {"name": "powershell", "description": "run powershell"},
        ]

    def names(self):
        return ["desktop_control", "system_info", "powershell"]

    def execute(self, name, args):
        self.executed.append((name, args))
        return self.results.get(name, {"status": "success"})


KNOWN = {"desktop_control", "system_info", "powershell"}


def _agent(chat, registry, **kw):
    return TaskAgent(
        chat, registry, Policy("full", KNOWN, surface="brain"), **kw
    )


def test_parse_handles_fences_and_prose():
    assert _parse('```json\n{"action":"done","summary":"x"}\n```')["action"] == (
        "done"
    )
    assert _parse('here you go: {"action":"done"} thanks')["action"] == "done"
    assert _parse("not json") is None
    assert _parse('["a","b"]') is None


def test_agent_runs_steps_then_finishes():
    chat = ScriptChat([
        {"action": "use_tool", "tool": "system_info", "args": {"query": "disks"}},
        {"action": "use_tool", "tool": "desktop_control", "args": {"action": "look"}},
        {"action": "done", "summary": "Checked disks and looked at the screen."},
    ])
    reg = FakeRegistry()
    result = _agent(chat, reg).run("check things")

    assert result.status == "done"
    assert [c[0] for c in reg.executed] == ["system_info", "desktop_control"]
    assert len(result.steps) == 2


def test_agent_skips_dangerous_step_and_keeps_going():
    chat = ScriptChat([
        {"action": "use_tool", "tool": "powershell",
         "args": {"command": "Stop-Service -Name Spooler"}},
        {"action": "done", "summary": "Did what I safely could."},
    ])
    reg = FakeRegistry()
    result = _agent(chat, reg).run("disable the print spooler")

    assert reg.executed == []  # dangerous step never ran
    assert result.skipped_confirmations
    assert "powershell" in result.skipped_confirmations[0]
    assert result.status == "done"


def test_agent_gives_up():
    chat = ScriptChat([{"action": "give_up", "reason": "no way to do this"}])
    result = _agent(chat, FakeRegistry()).run("impossible thing")
    assert result.status == "gave_up"
    assert "no way" in result.summary


def test_agent_recovers_from_bad_json():
    chat = ScriptChat([
        "garbage not json",
        {"action": "done", "summary": "ok"},
    ])
    result = _agent(chat, FakeRegistry()).run("do it")
    assert result.status == "done"


def test_agent_respects_max_steps():
    loop_reply = {"action": "use_tool", "tool": "system_info", "args": {}}
    chat = ScriptChat([loop_reply] * 20)
    result = _agent(chat, FakeRegistry(), max_steps=3).run("loop forever")
    assert result.status == "exhausted"
    assert len(result.steps) == 3


def test_agent_reports_failed_tool_but_continues():
    chat = ScriptChat([
        {"action": "use_tool", "tool": "system_info", "args": {}},
        {"action": "done", "summary": "handled it"},
    ])
    reg = FakeRegistry(results={"system_info": {"status": "error", "error": "boom"}})
    result = _agent(chat, reg).run("x")
    assert result.steps[0].ok is False
    assert result.status == "done"
