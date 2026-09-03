"""Wiring speaker verification into the wake listener: "only respond
to 'alfred' through my own voice alone" means a phrase match from
someone else's voice must not fire, but nobody who hasn't enrolled
should see any change at all - and a broken check should fail toward
"still works" for them, not toward "silently bricked".

Actually listening to a real voice needs a real microphone, which
this environment doesn't have - so, consistent with how VAD timing
and the reasoning-leak guard are tested elsewhere in this suite, what
is tested here is the gating logic itself: is_enrolled()/matches()
wired correctly into whether on_detect actually fires.
"""

from __future__ import annotations

import numpy as np

from src.voice import speaker_id
from src.voice.wake import WakeListener


def _listener(fired: list, **kwargs) -> WakeListener:
    kwargs.setdefault("debounce_seconds", 0.0)
    return WakeListener(on_detect=fired.append, **kwargs)


# ====================================================================
# Nobody enrolled: zero behaviour change
# ====================================================================


def test_not_enrolled_never_blocks_the_wake_word(monkeypatch):
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: False)
    fired: list = []
    _listener(fired, speaker_verify=True)._fire(0.9)
    assert fired == [0.9]


def test_speaker_verify_off_never_calls_is_enrolled(monkeypatch):
    calls = []
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: calls.append(1) or False)
    fired: list = []
    _listener(fired, speaker_verify=False)._fire(0.9)
    assert fired == [0.9]
    assert calls == []


# ====================================================================
# Enrolled: the match result decides
# ====================================================================


def test_enrolled_and_matching_fires(monkeypatch):
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: True)
    monkeypatch.setattr(speaker_id, "matches", lambda audio, threshold: True)
    fired: list = []
    _listener(fired, speaker_verify=True)._fire(0.9)
    assert fired == [0.9]


def test_enrolled_and_not_matching_is_ignored(monkeypatch):
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: True)
    monkeypatch.setattr(speaker_id, "matches", lambda audio, threshold: False)
    fired: list = []
    _listener(fired, speaker_verify=True)._fire(0.9)
    assert fired == []


def test_a_check_that_could_not_run_is_treated_as_a_mismatch(monkeypatch):
    """The deliberate fail-closed path: speaker_id.matches() returning
    None (missing model, missing dependency, unreadable voiceprint)
    must not be treated as a free pass once someone has enrolled."""
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: True)
    monkeypatch.setattr(speaker_id, "matches", lambda audio, threshold: None)
    fired: list = []
    _listener(fired, speaker_verify=True)._fire(0.9)
    assert fired == []


def test_speaker_verify_disabled_skips_the_check_even_if_enrolled(monkeypatch):
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: True)
    monkeypatch.setattr(speaker_id, "matches", lambda audio, threshold: False)
    fired: list = []
    _listener(fired, speaker_verify=False)._fire(0.9)
    assert fired == [0.9]


def test_the_configured_threshold_is_passed_through(monkeypatch):
    seen = {}

    def _record(audio, threshold):
        seen["threshold"] = threshold
        return True

    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: True)
    monkeypatch.setattr(speaker_id, "matches", _record)
    fired: list = []
    _listener(fired, speaker_verify=True, speaker_threshold=0.77)._fire(0.9)
    assert fired == [0.9]
    assert seen["threshold"] == 0.77


# ====================================================================
# A genuine bug in the check fails open, loudly - not closed, silently
# ====================================================================


def test_a_bug_in_the_check_itself_fails_open_not_closed(monkeypatch):
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(speaker_id, "is_enrolled", _boom)
    fired: list = []
    _listener(fired, speaker_verify=True)._fire(0.9)
    assert fired == [0.9]


# ====================================================================
# Debounce still applies with speaker gating in the mix
# ====================================================================


def test_debounce_still_blocks_a_second_call_regardless_of_match(monkeypatch):
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: True)
    monkeypatch.setattr(speaker_id, "matches", lambda audio, threshold: True)
    fired: list = []
    wl = _listener(fired, speaker_verify=True, debounce_seconds=999.0)
    wl._fire(0.9)
    wl._fire(0.9)
    assert fired == [0.9]


def test_a_rejected_attempt_still_starts_the_debounce_window(monkeypatch):
    """A mismatch (e.g. a TV repeating the phrase) shouldn't cause a
    fresh, uncooled speaker-check attempt on every single frame."""
    monkeypatch.setattr(speaker_id, "is_enrolled", lambda: True)
    calls = []
    monkeypatch.setattr(
        speaker_id, "matches", lambda audio, threshold: calls.append(1) and False
    )
    fired: list = []
    wl = _listener(fired, speaker_verify=True, debounce_seconds=999.0)
    wl._fire(0.9)
    wl._fire(0.9)
    assert fired == []
    assert len(calls) == 1


# ====================================================================
# The rolling capture buffer
# ====================================================================


def test_the_captured_audio_buffer_concatenates_recent_chunks():
    wl = WakeListener(on_detect=lambda s: None)
    wl._buffer.append(np.array([1, 2, 3], dtype=np.int16))
    wl._buffer.append(np.array([4, 5], dtype=np.int16))

    audio = wl._captured_audio()

    assert audio.tolist() == [1, 2, 3, 4, 5]
    assert audio.dtype == np.int16


def test_an_empty_buffer_is_empty_audio_not_a_crash():
    wl = WakeListener(on_detect=lambda s: None)
    audio = wl._captured_audio()
    assert audio.size == 0


def test_the_buffer_has_a_bounded_length():
    from src.voice.wake import _BUFFER_CHUNKS

    wl = WakeListener(on_detect=lambda s: None)
    for i in range(_BUFFER_CHUNKS + 50):
        wl._buffer.append(np.array([i], dtype=np.int16))

    assert len(wl._buffer) == _BUFFER_CHUNKS
    assert wl._buffer[-1].tolist() == [_BUFFER_CHUNKS + 49]  # oldest chunks fell off


# ====================================================================
# Defaults
# ====================================================================


def test_speaker_verify_defaults_to_on():
    wl = WakeListener(on_detect=lambda s: None)
    assert wl._speaker_verify is True


def test_speaker_threshold_has_a_sane_default():
    wl = WakeListener(on_detect=lambda s: None)
    assert 0.0 < wl._speaker_threshold < 1.0
