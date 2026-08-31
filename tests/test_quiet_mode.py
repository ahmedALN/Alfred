"""Alfred lives in a bedroom.

The work of testing it does not stop when the person in that room goes
to sleep, so it can be told to do everything it normally does except
make a sound. Everything else stays on: it still hears, still acts,
still answers the phone.
"""

import queue
import time

from src.ai.gemini import AlfredLiveSession, _is_muted


class _Quiet:
    """Just the speaker end of a session."""

    def __init__(self, muted):
        self._muted = muted
        self._speaker_queue = queue.Queue()
        self._last_audio_queued_at = 0.0
        self._audio_output = None


def _queue(session, data=b"audio"):
    AlfredLiveSession._queue_audio(session, data)


def test_quiet_mode_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("ALFRED_QUIET", raising=False)
    assert _is_muted() is False


def test_the_usual_spellings_all_turn_it_on(monkeypatch):
    for value in ("1", "true", "yes", "on", "TRUE", "On"):
        monkeypatch.setenv("ALFRED_QUIET", value)
        assert _is_muted() is True, value


def test_speech_still_reaches_the_speaker_normally():
    session = _Quiet(muted=False)
    _queue(session)

    assert session._speaker_queue.qsize() == 1


def test_nothing_is_queued_when_it_is_meant_to_be_quiet():
    session = _Quiet(muted=True)
    for _ in range(50):
        _queue(session)

    assert session._speaker_queue.empty()


def test_being_quiet_does_not_make_alfred_think_it_is_talking():
    """With a queue nothing drains, Alfred would believe it was
    speaking for ever and stop listening to the room."""
    session = _Quiet(muted=True)
    session._last_audio_queued_at = time.monotonic() - 10
    _queue(session)

    assert AlfredLiveSession._alfred_is_speaking(session) is False


def test_an_empty_chunk_is_still_ignored():
    session = _Quiet(muted=False)
    _queue(session, b"")

    assert session._speaker_queue.empty()
