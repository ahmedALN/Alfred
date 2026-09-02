import asyncio
import os

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from src.ai.gemini import AlfredLiveSession, _is_connection_error
from src.tools.registry import ToolRegistry


class _GoneError(Exception):
    __module__ = "google.genai.errors"


_GoneError.__name__ = "APIError"


def test_connection_error_classifier():
    assert _is_connection_error(_GoneError("1008 None. The operation was aborted."))
    assert _is_connection_error(ConnectionResetError())
    assert not _is_connection_error(KeyError("missing"))
    assert not _is_connection_error(ValueError("parse error"))


def test_run_forever_reconnects_then_stops():
    session = AlfredLiveSession(ToolRegistry())
    session.session = object()
    session._running = True
    session._connection = None
    session._reconnect_backoff_base = 0.01  # no real waiting in the test

    reopens = {"n": 0}

    async def fake_reopen():
        reopens["n"] += 1
        session.session = object()

    session._reopen_session = fake_reopen  # type: ignore[assignment]

    calls = {"n": 0}

    async def fake_mic():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _GoneError("1008 None. The operation was aborted.")
        await asyncio.sleep(0.01)  # then a clean end -> loop exits

    async def fake_receive():
        await asyncio.sleep(30)

    session._stream_microphone = fake_mic  # type: ignore[assignment]
    session._receive = fake_receive  # type: ignore[assignment]

    asyncio.run(asyncio.wait_for(session.run_forever(), timeout=10))

    assert reopens["n"] == 1  # reconnected exactly once
    assert calls["n"] == 2  # mic restarted after reconnect


def test_run_forever_gives_up_on_real_bug(monkeypatch):
    session = AlfredLiveSession(ToolRegistry())
    session.session = object()
    session._running = True

    async def bad_mic():
        raise ValueError("this is a genuine bug, not a disconnect")

    async def idle_receive():
        await asyncio.sleep(60)

    monkeypatch.setattr(session, "_stream_microphone", bad_mic)
    monkeypatch.setattr(session, "_receive", idle_receive)

    import pytest

    with pytest.raises(ValueError):
        asyncio.run(asyncio.wait_for(session.run_forever(), timeout=5))
