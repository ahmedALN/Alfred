import subprocess
import time
from collections import deque

import pytest

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

    def popen(cmd, cwd=None):
        launches.append(cmd)
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


def test_gives_up_after_too_many_restarts(monkeypatch):
    launches = _patch(monkeypatch, [1] * 20)
    assert watchdog.main() == 1
    assert len(launches) == watchdog._MAX_RESTARTS_PER_HOUR


def test_lock_conflict_code_3_waits_and_retries(monkeypatch):
    launches = _patch(monkeypatch, [3, 3, 0])
    assert watchdog.main() == 0
    assert len(launches) == 3
