"""Alfred was heard, understood, and said nothing back - and kept
listening for seconds after the person had stopped talking.

Both are real, and both had concrete, checkable causes:

  - the end-of-speech silence window was left at whatever Google's
    server-side default is, which the person described as "keeps
    listening for like 5 seconds more"
  - the system instruction told the model to "do quick single actions
    yourself" without ever saying that opening an app, finding
    something in it and acting on it is NOT one quick action even
    though it reads as one English sentence - so a job needing four or
    five real tool calls (open, wait, search, click) was handled
    inline, in total silence, sometimes for tens of seconds, while the
    person repeated themselves to what sounded like nothing

Neither is testable by actually listening (that needs a live
microphone), so what is tested here is the configuration and the
instruction Alfred is given - the same level the codebase already
tests prompts at elsewhere.
"""

from __future__ import annotations

from google.genai import types

from src.ai.gemini import AlfredLiveSession, _vad_silence_ms


def _config_of(session):
    return AlfredLiveSession._config(session)


class _Bare:
    """Just enough of a session to build a config from."""

    _resume_handle = ""

    def _tool_declarations(self):
        return []

    def _system_instruction(self):
        return AlfredLiveSession._system_instruction(self)

    def _memory_context_block(self):
        return ""

    def _situation_block(self):
        return ""


# ====================================================================
# The silence tail after you stop talking
# ====================================================================


def test_end_of_speech_is_configured_rather_than_left_to_the_default():
    """Left unset, this is whatever Google's server default is - which
    is what the person described as a five-second tail."""
    config = _config_of(_Bare())
    vad = config.realtime_input_config.automatic_activity_detection

    assert vad.silence_duration_ms is not None
    assert vad.disabled is False


def test_the_silence_window_is_short_but_not_hair_trigger():
    """700ms: quick enough that a question doesn't sit waiting for a
    reply that was never coming, generous enough that a breath mid
    sentence does not read as the end of it."""
    config = _config_of(_Bare())
    vad = config.realtime_input_config.automatic_activity_detection

    assert 400 <= vad.silence_duration_ms <= 1200


def test_end_of_speech_sensitivity_is_set_to_commit_promptly():
    config = _config_of(_Bare())
    vad = config.realtime_input_config.automatic_activity_detection

    assert vad.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_HIGH


def test_the_silence_window_can_be_tuned_by_ear(monkeypatch):
    """This is an audio-timing parameter - what feels right can only be
    confirmed by a real spoken exchange, so it has to be adjustable
    without a code change."""
    monkeypatch.setenv("ALFRED_VAD_SILENCE_MS", "450")

    assert _vad_silence_ms() == 450

    config = _config_of(_Bare())
    vad = config.realtime_input_config.automatic_activity_detection

    assert vad.silence_duration_ms == 450


def test_a_bad_override_does_not_break_the_session(monkeypatch):
    monkeypatch.setenv("ALFRED_VAD_SILENCE_MS", "not-a-number")

    assert _vad_silence_ms() == 700


# ====================================================================
# Going silent while working
# ====================================================================


def test_a_multi_step_app_job_is_told_it_is_not_one_quick_action():
    """The exact shape that failed live: "open Spotify and play a
    song" is one sentence and four or five real tool calls - open,
    wait, search, pick a result - and nothing told the model that
    reading as one sentence does not make it one quick action."""
    instruction = _Bare()._system_instruction()

    assert "not one quick action" in instruction
    assert "Spotify" in instruction


def test_the_model_is_told_to_speak_before_a_chain_of_tool_calls():
    """The rule this is actually about: something has to be said before
    the model goes quiet to do several things in a row, whatever the
    job turns out to be - not just the Spotify-shaped ones the
    instruction happens to name as an example."""
    instruction = _Bare()._system_instruction()

    assert "never let more than one tool call go by" in instruction.lower()


def test_the_instruction_still_allows_a_genuinely_quick_action():
    """The fix must not swing the other way and make Alfred narrate
    before opening one app or answering one question."""
    instruction = _Bare()._system_instruction()

    assert "quick single action" in instruction.lower()
