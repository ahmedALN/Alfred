import os
import queue

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from src.ai.gemini import AlfredLiveSession  # noqa: E402
from src.tools.registry import ToolRegistry  # noqa: E402
from src.voice.activation import ActivationController  # noqa: E402


def _session(**kw):
    session = AlfredLiveSession(ToolRegistry(), **kw)
    # Night mode is read from the environment, and these tests are
    # about the speaker: they must not quietly pass or fail depending
    # on whether the machine they run on happens to be muted.
    session._muted = False
    return session


def test_speaking_flag_tracks_queue_and_tail(monkeypatch):
    s = _session(half_duplex=True)
    t = [1000.0]
    monkeypatch.setattr("src.ai.gemini.time.monotonic", lambda: t[0])

    assert s._alfred_is_speaking() is False

    s._queue_audio(b"\x00\x01")  # queue has data, timestamp set
    assert s._alfred_is_speaking() is True

    # drain the queue; still "speaking" during the 0.35s tail
    s._speaker_queue.get_nowait()
    assert s._alfred_is_speaking() is True

    t[0] += 0.4  # tail elapsed
    assert s._alfred_is_speaking() is False


def test_activation_gate_state():
    a = ActivationController(always_on=False)
    s = _session(activation=a, half_duplex=True)

    assert s._activation.is_listening is False
    a.wake()
    assert s._activation.is_listening is True


def test_no_activation_means_always_listening():
    s = _session()
    assert s._activation is None  # -> _stream_microphone never gates
