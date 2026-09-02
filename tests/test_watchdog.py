import subprocess
import time

from src import watchdog


class FakeChild:
    def __init__(self, code, run_seconds=0.0):
        self._code = code
        self._run = run_seconds
        self.polled = False

    def wait(self):
        time.sleep(self._run)
        return self._code

    def poll(self):
        return self._code

    def send_signal(self, s):
        pass


def _patch(monkeypatch, codes):
    """codes: list of exit codes to return for successive launches."""
    it = iter(codes)
    launches = []

    def popen(cmd, cwd=None, **kwargs):
        # **kwargs, not a fixed list: a fake narrower than the real
        # thing fails the moment production passes anything new, and
        # says "unexpected keyword argument" rather than what is wrong.
        launches.append(cmd)
        assert kwargs.get("creationflags") is not None, (
            "the watchdog must start Alfred without a console window"
        )
        return FakeChild(next(it))

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    return launches


def test_clean_exit_stops_watchdog(monkeypatch):
    launches = _patch(monkeypatch, [0])
    assert watchdog.main() == 0
    assert len(launches) == 1


def test_crash_then_clean_exit(monkeypatch):
    launches = _patch(monkeypatch, [1, 0])
    assert watchdog.main() == 0
    assert len(launches) == 2  # restarted once, then clean


def test_it_stops_hammering_but_does_not_give_up_for_good(monkeypatch):
    """Six failures in an hour means something is wrong.

    It used to return at that point, which left Alfred dead until the
    machine was rebooted - and the usual cause is an outage somewhere
    else that passes on its own. It sits out half an hour instead, so
    it stops hammering and still recovers without anybody noticing.
    """
    slept: list[float] = []
    launches = _patch(monkeypatch, [1] * watchdog._MAX_RESTARTS_PER_HOUR + [0])
    monkeypatch.setattr(watchdog.time, "sleep", slept.append)

    assert watchdog.main() == 0     # recovered on the run after the wait

    assert len(launches) == watchdog._MAX_RESTARTS_PER_HOUR + 1
    assert watchdog._COOL_OFF in slept, "it should sit out the cool-off"


def test_lock_conflict_code_3_waits_and_retries(monkeypatch):
    launches = _patch(monkeypatch, [3, 3, 0])
    assert watchdog.main() == 0
    assert len(launches) == 3
