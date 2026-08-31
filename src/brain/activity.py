"""What you have actually been doing.

Every collector Alfred had watched the machine: processor, memory, disk,
network, power, updates. All plumbing. So when the brain spoke unasked
it could tell you a disk was filling and could not tell you that you had
been in the same document for three hours, or that you open the same
three things every weekday morning. There was nothing personal for it to
be proactive about, which is why a proactive assistant felt like a
monitoring daemon with a nice voice.

This watches the foreground window - which app, which title, how long -
and keeps it locally, in a file of its own that never leaves the machine.
Enough to notice a long stretch of work or a daily habit. Not enough to
reconstruct what you typed, because that was not on offer.

Turn it off with ALFRED_WATCH_ME=false, and clear it with
    python -m src.watching forget
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.brain.types import Observation
from src.brain.signals import SignalCollector

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spells (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    app     TEXT NOT NULL,
    title   TEXT NOT NULL,
    started TEXT NOT NULL,
    ended   TEXT NOT NULL,
    seconds INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS spells_started ON spells(started);
CREATE INDEX IF NOT EXISTS spells_app ON spells(app);
"""

# Titles say more than they need to. A password manager naming the entry
# being viewed, a browser naming the page - none of that has to be kept
# to know you spent an hour in a browser.
_SENSITIVE = re.compile(
    r"(?:\b(?:password|passwd|bitwarden|1password|lastpass|keepass|"
    r"incognito|private browsing|banking|bank of|paypal|wallet|"
    r"seed phrase|recovery key|2fa|authenticator)\b"
    # Not word-bounded: Edge writes it "InPrivate", one word. A mode
    # whose entire purpose is not being recorded should not be recorded.
    r"|inprivate)",
    re.I,
)

# Below this, a window was passed through rather than used.
_WORTH_KEEPING = 20


def watching() -> bool:
    return os.getenv("ALFRED_WATCH_ME", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


class ActivityLog:
    """Stretches of time spent in one place."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(
        self, app: str, title: str, started: datetime, ended: datetime
    ) -> None:
        seconds = int((ended - started).total_seconds())
        if seconds < _WORTH_KEEPING:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO spells (app, title, started, ended, seconds) "
                "VALUES (?, ?, ?, ?, ?)",
                (app, title, started.isoformat(timespec="seconds"),
                 ended.isoformat(timespec="seconds"), seconds),
            )
            self._conn.commit()

    def today(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now()
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._lock:
            rows = self._conn.execute(
                "SELECT app, SUM(seconds) AS seconds, COUNT(*) AS spells "
                "FROM spells WHERE started >= ? GROUP BY app "
                "ORDER BY seconds DESC",
                (since.isoformat(timespec="seconds"),),
            ).fetchall()
        return [dict(r) for r in rows]

    def habits(self, days: int = 14, now: datetime | None = None) -> list[dict]:
        """Things done at about the same time on most days.

        Two mornings is a coincidence. The bar is deliberately high,
        because being told about a coincidence as though it were a habit
        is worse than being told nothing.
        """
        now = now or datetime.now()
        since = now - timedelta(days=days)
        with self._lock:
            rows = self._conn.execute(
                "SELECT app, started FROM spells WHERE started >= ?",
                (since.isoformat(timespec="seconds"),),
            ).fetchall()

        buckets: dict[tuple[str, int], set[str]] = {}
        for row in rows:
            when = datetime.fromisoformat(row["started"])
            key = (row["app"], when.hour)
            buckets.setdefault(key, set()).add(when.date().isoformat())

        out = []
        for (app, hour), on_days in buckets.items():
            if len(on_days) >= max(3, days // 3):
                out.append({
                    "app": app, "hour": hour, "days": len(on_days),
                })
        return sorted(out, key=lambda h: -h["days"])

    def forget(self) -> int:
        with self._lock:
            gone = self._conn.execute("DELETE FROM spells").rowcount
            self._conn.commit()
            # After the commit and outside the transaction: sqlite will
            # not vacuum inside one, and forgetting ought to shrink the
            # file rather than mark its pages reusable.
            try:
                self._conn.execute("VACUUM")
            except Exception:  # noqa: BLE001
                pass
        return gone

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


class ActivityCollector(SignalCollector):
    """Where you are now, and how long you have been there."""

    name = "activity"

    # A stretch worth mentioning rather than just recording.
    LONG_STRETCH = 90 * 60

    def __init__(
        self,
        log: ActivityLog,
        foreground=None,
        clock=None,
    ) -> None:
        self._log = log
        self._foreground = foreground or _foreground_window
        self._clock = clock or datetime.now
        self._where: tuple[str, str] | None = None
        self._since: datetime | None = None

    def collect(self) -> list[Observation]:
        if not watching():
            return []

        now = self._clock()
        app, title = self._foreground()
        if not app:
            return []

        title = _tidy(title)
        here = (app, title)

        if self._where != here:
            if self._where is not None and self._since is not None:
                self._log.record(self._where[0], self._where[1],
                                 self._since, now)
            self._where = here
            self._since = now

        minutes = int((now - (self._since or now)).total_seconds() // 60)
        return [
            Observation(
                source=self.name,
                key="activity.where",
                value={"app": app, "title": title, "minutes": minutes},
                summary=(
                    f"{app} for {minutes} min"
                    + (f" - {title}" if title else "")
                ),
            ),
            Observation(
                source=self.name,
                key="activity.long_stretch",
                # A key that only changes when it crosses the line, so
                # the brain notices the crossing rather than every tick.
                value=minutes >= self.LONG_STRETCH // 60,
                summary=(
                    f"You have been in {app} for {minutes} minutes without "
                    "a break."
                ),
            ),
        ]


# ------------------------------------------------------------- helpers


def _tidy(title: str) -> str:
    """What is safe and useful to keep of a window title."""
    title = (title or "").strip()
    if not title:
        return ""
    if _SENSITIVE.search(title):
        return "(private)"
    return title[:120]


def _foreground_window() -> tuple[str, str]:
    """The app and title of whatever is in front."""
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "", ""
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        app = psutil.Process(pid).name()
        if app.lower().endswith(".exe"):
            app = app[:-4]
        return app, title
    except Exception:  # noqa: BLE001
        return "", ""
