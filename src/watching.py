"""
python -m src.watching  -  what Alfred has noticed about your day.

    today     where the time went
    habits    things you do at about the same time most days
    forget    delete all of it

Alfred keeps the app and window title of whatever is in front, and how
long it was there. Titles that look like they name something private -
a password manager, a banking page - are stored as "(private)" instead.

None of it leaves this machine. Stop it entirely with ALFRED_WATCH_ME=false.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _log():
    from src.brain.activity import ActivityLog

    return ActivityLog(_ROOT / os.getenv(
        "ALFRED_ACTIVITY_DB", "alfred_activity.sqlite3"
    ))


def _spell(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def cmd_today(_argv: list[str]) -> int:
    rows = _log().today()
    if not rows:
        print("Nothing noted today.")
        return 0

    print("where today went:\n")
    for row in rows:
        print(f"  {row['app'][:26]:28} {_spell(row['seconds']):>8}"
              f"   {row['spells']} spell(s)")
    return 0


def cmd_habits(_argv: list[str]) -> int:
    rows = _log().habits()
    if not rows:
        print("Nothing regular enough to call a habit yet.")
        print("It needs a couple of weeks to have an opinion.")
        return 0

    print("things you tend to do:\n")
    for row in rows:
        print(f"  {row['app'][:26]:28} around {row['hour']:02d}:00"
              f"   on {row['days']} days")
    return 0


def cmd_forget(_argv: list[str]) -> int:
    gone = _log().forget()
    print(f"Forgot {gone} recorded spell(s).")
    print("To stop it recording any more: ALFRED_WATCH_ME=false in .env")
    return 0


_COMMANDS = {"today": cmd_today, "habits": cmd_habits, "forget": cmd_forget}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "today"
    if command not in _COMMANDS:
        print(__doc__)
        return 1
    return _COMMANDS[command](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
