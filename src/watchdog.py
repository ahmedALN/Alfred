"""
python -m src.watchdog

Keeps Alfred running: launches `python -m src.main` and restarts it if
it crashes, with backoff and a sanity cap. A clean exit (you quit from
the tray, or Ctrl+C) stops the watchdog too.

Point autostart at this instead of src.main for a truly always-on Alfred:
    python -m src.autostart install   (already does this)
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_MAX_RESTARTS_PER_HOUR = 6
_BACKOFF_START = 5.0
_BACKOFF_MAX = 120.0
_MIN_HEALTHY_RUN = 60.0  # ran at least this long -> reset backoff


def _python() -> str:
    exe = Path(sys.executable)
    pw = exe.with_name("pythonw.exe")
    return str(pw if pw.exists() else exe)


def main() -> int:
    cmd = [_python(), "-m", "src.main"]
    backoff = _BACKOFF_START
    restarts: deque[float] = deque()
    child: subprocess.Popen | None = None

    def _forward(signum, _frame):
        if child and child.poll() is None:
            child.send_signal(signal.SIGINT)

    signal.signal(signal.SIGINT, _forward)
    signal.signal(signal.SIGTERM, _forward)

    print(f"[watchdog] supervising: {' '.join(cmd)}")

    while True:
        now = time.monotonic()
        while restarts and now - restarts[0] > 3600:
            restarts.popleft()

        if len(restarts) >= _MAX_RESTARTS_PER_HOUR:
            print(
                f"[watchdog] {len(restarts)} restarts in the last hour - "
                "something is wrong. Stopping. Check logs/alfred.log."
            )
            return 1

        started = time.monotonic()
        child = subprocess.Popen(cmd, cwd=str(_ROOT))
        code = child.wait()
        ran_for = time.monotonic() - started

        if code == 0:
            print("[watchdog] Alfred exited cleanly. Done.")
            return 0

        if code == 3:
            print("[watchdog] another Alfred already holds the lock; waiting 30s.")
            time.sleep(30)
            continue

        restarts.append(time.monotonic())

        if ran_for >= _MIN_HEALTHY_RUN:
            backoff = _BACKOFF_START

        print(
            f"[watchdog] Alfred exited with code {code} after {ran_for:.0f}s; "
            f"restarting in {backoff:.0f}s"
        )
        time.sleep(backoff)
        backoff = min(backoff * 2, _BACKOFF_MAX)


if __name__ == "__main__":
    raise SystemExit(main())
