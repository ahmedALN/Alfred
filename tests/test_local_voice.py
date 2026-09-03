
from src.brain.policy import Policy
from src.voice.local_voice import LocalVoiceSession, _extract_tool_call


class ScriptChat:
    name = "s"
    model = "s"

    def __init__(self, replies):
        self._r = list(replies)

    def generate(self, prompt, **kw):
        return self._r.pop(0) if self._r else "done"


class Reg:
    def __init__(self):
        self.executed = []

    def gemini_declarations(self):
        return [{"name": "system_info", "description": "read system state"}]

    def names(self):
        return ["system_info"]

    def execute(self, name, args):
        self.executed.append((name, args))
        return {"status": "success", "data": {"FreeGB": 174}}


def _lv(chat, reg=None):
    reg = reg or Reg()
    return LocalVoiceSession(
        chat, reg, Policy("full", {"system_info"}, surface="voice")
    ), reg


# ---------------------------------------------------------------- parsing


def test_extract_tool_call():
    assert _extract_tool_call('{"tool": "system_info", "args": {"query": "disks"}}') == (
        "system_info",
        {"query": "disks"},
    )
    assert _extract_tool_call('```json\n{"tool":"x","args":{}}\n```') == ("x", {})
    assert _extract_tool_call("Paris is the capital.") is None
    assert _extract_tool_call('{"not": "a tool call"}') is None


# ---------------------------------------------------------------- respond


def test_respond_plain_answer():
    lv, _ = _lv(ScriptChat(["Paris is the capital of France."]))
    assert lv._respond("capital of France?") == "Paris is the capital of France."


def test_respond_uses_a_tool_then_answers():
    chat = ScriptChat([
        '{"tool": "system_info", "args": {"query": "disks"}}',
        "You have 174 GB free on C:.",
    ])
    lv, reg = _lv(chat)
    out = lv._respond("how much disk space?")
    assert reg.executed == [("system_info", {"query": "disks"})]
    assert "174 GB" in out


def test_respond_skips_a_non_auto_tool_offline():
    # a dangerous powershell call -> policy won't AUTO it -> skipped, not run
    chat = ScriptChat([
        '{"tool": "system_info", "args": {}}',  # ok
        '{"tool": "powershell", "args": {"command": "Stop-Service X"}}',
        "I did what I safely could.",
    ])
    reg = Reg()
    reg.names = lambda: ["system_info", "powershell"]
    reg.gemini_declarations = lambda: [
        {"name": "system_info", "description": "read"},
        {"name": "powershell", "description": "run"},
    ]
    lv = LocalVoiceSession(
        chat, reg, Policy("full", {"system_info", "powershell"}, surface="voice")
    )
    lv._respond("stop service X")
    assert ("powershell", {"command": "Stop-Service X"}) not in reg.executed


def test_respond_caps_iterations():
    chat = ScriptChat(['{"tool": "system_info", "args": {}}'] * 10)
    lv, reg = _lv(chat)
    out = lv._respond("loop")
    assert len(reg.executed) <= 3
    assert isinstance(out, str)


# ---------------------------------------------------------------- speak_only
#
# A dead turn on the live session (src/ai/gemini.py's watchdog) needs a
# fallback line said FAST - the full offline loop's _load() also pulls
# in faster-whisper, which nobody asked for just to say one sentence.


def test_speak_only_loads_piper_without_whisper(monkeypatch):
    lv, _ = _lv(ScriptChat([]))
    seen = {}

    def fake_load(need_stt=True):
        seen["need_stt"] = need_stt
        lv._piper = object()
        return True

    monkeypatch.setattr(lv, "_load", fake_load)
    monkeypatch.setattr(lv, "speak", lambda text: seen.setdefault("spoke", text))

    assert lv.speak_only("try again?") is True
    assert seen["need_stt"] is False
    assert seen["spoke"] == "try again?"


def test_speak_only_returns_false_if_it_cannot_load(monkeypatch):
    lv, _ = _lv(ScriptChat([]))
    monkeypatch.setattr(lv, "_load", lambda need_stt=True: False)
    spoke = []
    monkeypatch.setattr(lv, "speak", lambda text: spoke.append(text))

    assert lv.speak_only("hello?") is False
    assert spoke == []  # never got as far as trying to speak


def test_speak_only_does_not_reload_once_piper_is_already_up(monkeypatch):
    lv, _ = _lv(ScriptChat([]))
    lv._piper = object()  # as if a previous speak_only() already loaded it

    def _boom(need_stt=True):
        raise AssertionError("_load() should not be called again")

    monkeypatch.setattr(lv, "_load", _boom)
    spoke = []
    monkeypatch.setattr(lv, "speak", lambda text: spoke.append(text))

    assert lv.speak_only("still there?") is True
    assert spoke == ["still there?"]


def test_load_with_need_stt_false_never_touches_whisper():
    """The real _load(), not a stub - proves need_stt=False actually
    skips the whisper import/model rather than just being an unused
    parameter. Uses the Piper voice already on disk for this repo, so
    no network call and no whisper download either."""
    lv, _ = _lv(ScriptChat([]))
    ok = lv._load(need_stt=False)
    assert ok is True
    assert lv._whisper is None
    assert lv._piper is not None
