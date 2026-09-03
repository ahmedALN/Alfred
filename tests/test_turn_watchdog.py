""""it keeps listening for so long after i finish speaking then it
just goes back to idle mode it doesnt do anything" - a live turn that
should have gotten a reply (or a tool call) sometimes got neither: no
text, no audio, no tool call, and no error either, just quiet until
the 30s idle timeout gave up on its own. Nothing ever told the user
what happened, because nothing in the code was watching for "a turn
started and then nothing else ever happened."

Real evidence from logs/alfred.log: "You: Uh can you open Steam and
then yeah open Steam." was heard and transcribed, then literally
nothing else printed for the rest of that listening window before
"[Activation] asleep (idle timeout)".

Can't reproduce a live Gemini hang from here any more than the VAD
timing or reasoning-leak tests elsewhere in this suite can reproduce
their real conditions - what's tested is the watchdog mechanism
itself: it only fires on genuine total silence, never mistakes a
slow-but-legitimate tool call for a dead turn, and actually gives the
user something back when it does fire.
"""

from __future__ import annotations

import asyncio
import os
import time

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from src.ai.gemini import AlfredLiveSession, _turn_watchdog_seconds
from src.tools.registry import ToolRegistry


def _session(**overrides) -> AlfredLiveSession:
    session = AlfredLiveSession(ToolRegistry())
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


class _FakeActivation:
    def __init__(self):
        self.noted = 0
        self.extended: list[float] = []

    def note_activity(self):
        self.noted += 1

    def extend(self, seconds: float):
        self.extended.append(seconds)


class _FakeLocalVoice:
    def __init__(self, ok: bool = True):
        self._ok = ok
        self.said: list[str] = []

    def speak_only(self, text: str) -> bool:
        self.said.append(text)
        return self._ok


# ====================================================================
# _turn_watchdog_seconds() - env override, same pattern as VAD's
# ====================================================================


def test_turn_watchdog_seconds_default():
    assert _turn_watchdog_seconds() == 12.0


def test_turn_watchdog_seconds_is_tunable(monkeypatch):
    monkeypatch.setenv("ALFRED_TURN_WATCHDOG_SECONDS", "5")
    assert _turn_watchdog_seconds() == 5.0


def test_turn_watchdog_seconds_survives_a_bad_value(monkeypatch):
    monkeypatch.setenv("ALFRED_TURN_WATCHDOG_SECONDS", "not-a-number")
    assert _turn_watchdog_seconds() == 12.0


# ====================================================================
# Fresh state
# ====================================================================


def test_a_new_session_has_no_turn_pending():
    session = _session()
    assert session._turn_pending is False
    assert session._tool_in_flight is False


# ====================================================================
# _turn_progress()
# ====================================================================


def test_turn_progress_marks_pending_and_stamps_the_clock():
    session = _session()
    before = time.monotonic()
    session._turn_progress()
    assert session._turn_pending is True
    assert before <= session._turn_started_at <= time.monotonic()


# ====================================================================
# _turn_watchdog() - the actual firing logic
# ====================================================================


def test_the_watchdog_fires_after_real_silence():
    session = _session(_turn_watchdog_seconds=0.02)
    session._turn_progress()

    fired = []

    async def fake_timed_out():
        fired.append(1)

    session._turn_timed_out = fake_timed_out  # type: ignore[assignment]

    async def run():
        try:
            await asyncio.wait_for(
                session._turn_watchdog(poll_seconds=0.01), timeout=0.3
            )
        except TimeoutError:
            pass

    asyncio.run(run())
    assert fired  # fired at least once


def test_the_watchdog_does_not_fire_before_its_deadline():
    session = _session(_turn_watchdog_seconds=5.0)
    session._turn_progress()

    fired = []
    session._turn_timed_out = lambda: fired.append(1)  # type: ignore[assignment]

    async def run():
        try:
            await asyncio.wait_for(
                session._turn_watchdog(poll_seconds=0.01), timeout=0.2
            )
        except TimeoutError:
            pass

    asyncio.run(run())
    assert fired == []


def test_the_watchdog_never_fires_with_nothing_pending():
    session = _session(_turn_watchdog_seconds=0.01)
    assert session._turn_pending is False  # never spoken to it at all

    fired = []
    session._turn_timed_out = lambda: fired.append(1)  # type: ignore[assignment]

    async def run():
        try:
            await asyncio.wait_for(
                session._turn_watchdog(poll_seconds=0.01), timeout=0.15
            )
        except TimeoutError:
            pass

    asyncio.run(run())
    assert fired == []


def test_a_tool_call_still_running_does_not_look_like_a_dead_turn():
    """A tool has its own 90s timeout and always reports back - the
    watchdog must never mistake "still running" for "died silently",
    no matter how long a legitimate tool call takes."""
    session = _session(_turn_watchdog_seconds=0.02, _tool_in_flight=True)
    session._turn_progress()

    fired = []

    async def fake_timed_out():
        fired.append(1)

    session._turn_timed_out = fake_timed_out  # type: ignore[assignment]

    async def run():
        try:
            await asyncio.wait_for(
                session._turn_watchdog(poll_seconds=0.01), timeout=0.3
            )
        except TimeoutError:
            pass

    asyncio.run(run())
    assert fired == []


def test_it_fires_at_most_once_per_dead_turn():
    session = _session(_turn_watchdog_seconds=0.02)
    session._turn_progress()

    fired = []

    async def fake_timed_out():
        fired.append(1)

    session._turn_timed_out = fake_timed_out  # type: ignore[assignment]

    async def run():
        try:
            await asyncio.wait_for(
                session._turn_watchdog(poll_seconds=0.01), timeout=0.4
            )
        except TimeoutError:
            pass

    asyncio.run(run())
    assert len(fired) == 1  # not once per poll tick after the first


def test_a_fresh_utterance_after_firing_can_trigger_it_again():
    """The clock and the pending flag are per-turn, not permanently
    spent after one dead turn."""
    session = _session(_turn_watchdog_seconds=0.02)
    session._turn_progress()

    fired = []

    async def fake_timed_out():
        fired.append(1)

    session._turn_timed_out = fake_timed_out  # type: ignore[assignment]

    async def run():
        try:
            await asyncio.wait_for(
                session._turn_watchdog(poll_seconds=0.01), timeout=0.1
            )
        except TimeoutError:
            pass

        session._turn_progress()  # a new utterance starts a new turn

        try:
            await asyncio.wait_for(
                session._turn_watchdog(poll_seconds=0.01), timeout=0.1
            )
        except TimeoutError:
            pass

    asyncio.run(run())
    assert len(fired) == 2


# ====================================================================
# _turn_timed_out() - what actually happens when it fires
# ====================================================================


def test_timed_out_bumps_activation_so_a_retry_has_room():
    activation = _FakeActivation()
    session = _session(_activation=activation, _local_voice_factory=None)

    asyncio.run(session._turn_timed_out())

    assert activation.noted == 1
    assert activation.extended == [20.0]


def test_timed_out_with_no_activation_does_not_crash():
    session = _session(_activation=None, _local_voice_factory=None)
    asyncio.run(session._turn_timed_out())  # just must not raise


def test_timed_out_speaks_a_fallback_through_local_voice():
    local = _FakeLocalVoice(ok=True)
    session = _session(
        _activation=_FakeActivation(),
        _local_voice_factory=lambda: local,
    )

    asyncio.run(session._turn_timed_out())

    assert len(local.said) == 1
    assert local.said[0].strip() != ""


def test_timed_out_with_no_local_voice_factory_just_logs(capsys):
    session = _session(_activation=_FakeActivation(), _local_voice_factory=None)
    asyncio.run(session._turn_timed_out())
    assert "no response arrived" in capsys.readouterr().out


def test_timed_out_survives_a_broken_local_voice_factory():
    def _boom():
        raise RuntimeError("piper is on fire")

    session = _session(_activation=_FakeActivation(), _local_voice_factory=_boom)
    asyncio.run(session._turn_timed_out())  # must not raise


def test_timed_out_survives_local_voice_reporting_failure(capsys):
    local = _FakeLocalVoice(ok=False)
    session = _session(
        _activation=_FakeActivation(),
        _local_voice_factory=lambda: local,
    )

    asyncio.run(session._turn_timed_out())

    assert local.said  # it tried
    assert "unavailable" in capsys.readouterr().out
