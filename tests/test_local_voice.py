
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


# -------------------------------------------------------------- synthesize_pcm
#
# A dead turn on the live session (src/ai/gemini.py's watchdog) needs a
# fallback line said FAST, without pulling in faster-whisper just to
# say one sentence - and, critically, without opening a second,
# independent PortAudio stream of its own. That was the first version
# (speak_only(), via sd.play()) and it was enough to starve the live
# session's microphone callback thread: "[Microphone] input overflow"
# landed right as the fallback line tried to play, every time.
# synthesize_pcm() returns raw bytes instead, meant to be queued into
# an already-open stream - nothing here plays anything.


def test_synthesize_pcm_loads_piper_without_whisper(monkeypatch):
    lv, _ = _lv(ScriptChat([]))
    seen = {}

    def fake_load(need_stt=True):
        seen["need_stt"] = need_stt
        return False  # short-circuits before any real synthesis is attempted

    monkeypatch.setattr(lv, "_load", fake_load)

    assert lv.synthesize_pcm("try again?", target_rate=24000) is None
    assert seen["need_stt"] is False


def test_synthesize_pcm_returns_none_if_it_cannot_load(monkeypatch):
    lv, _ = _lv(ScriptChat([]))
    monkeypatch.setattr(lv, "_load", lambda need_stt=True: False)

    assert lv.synthesize_pcm("hello?", target_rate=24000) is None


def test_synthesize_pcm_does_not_reload_once_piper_is_already_up(monkeypatch):
    lv, _ = _lv(ScriptChat([]))
    lv._piper = object()  # as if a previous call already loaded it

    def _boom(need_stt=True):
        raise AssertionError("_load() should not be called again")

    monkeypatch.setattr(lv, "_load", _boom)

    # The fake _piper has no real synthesize_wav, so this fails past
    # the reload check - proving _load() genuinely wasn't the thing
    # that stopped it.
    assert lv.synthesize_pcm("still there?", target_rate=24000) is None


def test_synthesize_pcm_is_none_for_empty_text():
    lv, _ = _lv(ScriptChat([]))
    assert lv.synthesize_pcm("   ", target_rate=24000) is None


def test_synthesize_pcm_never_touches_sounddevice(monkeypatch):
    """The whole point: no playback, no second stream. If this ever
    imports sounddevice again, the fix it exists for has regressed."""
    import sys

    lv, _ = _lv(ScriptChat([]))
    monkeypatch.setattr(lv, "_load", lambda need_stt=True: False)
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)

    lv.synthesize_pcm("hello?", target_rate=24000)

    assert "sounddevice" not in sys.modules


def test_synthesize_pcm_end_to_end_with_the_real_piper_voice():
    """The real Piper voice already on disk for this repo (no network
    call), resampled from its native 22050 Hz to a different target -
    proves the whole pipeline, not just the load-skip logic."""
    lv, _ = _lv(ScriptChat([]))

    pcm = lv.synthesize_pcm("Testing, one two three.", target_rate=24000)

    assert pcm is not None
    assert len(pcm) > 0
    assert len(pcm) % 2 == 0  # whole int16 samples, nothing truncated oddly


def test_synthesize_pcm_at_the_native_rate_needs_no_resampling():
    lv, _ = _lv(ScriptChat([]))

    pcm = lv.synthesize_pcm("Hi.", target_rate=22050)

    assert pcm is not None
    assert len(pcm) > 0


# ---------------------------------------------------------------- _resample_int16


def test_resample_int16_is_a_no_op_at_the_same_rate():
    import numpy as np

    from src.voice.local_voice import _resample_int16

    pcm = np.array([1, 2, 3, 4, 5], dtype=np.int16)
    out = _resample_int16(pcm, 22050, 22050)

    assert out is pcm  # returned unchanged, not merely equal


def test_resample_int16_preserves_duration_not_sample_count():
    import numpy as np

    from src.voice.local_voice import _resample_int16

    one_second = np.zeros(22050, dtype=np.int16)
    out = _resample_int16(one_second, 22050, 24000)

    assert out.dtype == np.int16
    assert abs(len(out) - 24000) <= 1  # still ~one second, at the new rate


def test_resample_int16_handles_an_empty_array():
    import numpy as np

    from src.voice.local_voice import _resample_int16

    empty = np.array([], dtype=np.int16)
    assert len(_resample_int16(empty, 22050, 24000)) == 0


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
