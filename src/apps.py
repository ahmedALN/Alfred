"""
python -m src.apps  -  what Alfred has learned about working inside apps.

    list                 every app Alfred has used, most-used first
    show <app>           the full profile: window title, controls, notes
    note <app> <text>    teach it something about an app by hand
    forget <app>         drop everything learned about one app

This is separate from skills: a skill replays one whole task verbatim,
while app memory is knowledge about an app that helps with any request
inside it - the real window title, which control names work, quirks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from src.brain.app_memory import AppMemory, app_key

_DB = Path(__file__).resolve().parent.parent / os.getenv(
    "ALFRED_APP_DB", "alfred_apps.sqlite3"
)


def _store() -> AppMemory:
    return AppMemory(_DB)


def cmd_list(_args: list[str]) -> int:
    store = _store()
    keys = store.known_apps()
    print(f"{len(keys)} app(s):")
    for key in keys:
        data = store.app(key) or {}
        controls = len(data.get("controls", []))
        notes = len(data.get("notes", []))
        title = data.get("window_title") or ""
        print(
            f"  {key:<28} opened {data.get('opens', 0):>3}x  "
            f"{controls} control(s), {notes} note(s)"
            + (f'  window={title!r}' if title else "")
        )
    store.close()
    return 0


def cmd_show(args: list[str]) -> int:
    if not args:
        print("usage: show <app>")
        return 2
    store = _store()
    text = store.profile(" ".join(args))
    store.close()
    if not text:
        print(f"nothing learned about {' '.join(args)!r} yet")
        return 1
    print(text)
    return 0


def cmd_note(args: list[str]) -> int:
    if len(args) < 2:
        print('usage: note <app> <text>')
        return 2
    store = _store()
    store.note(args[0], " ".join(args[1:]), kind="manual")
    print(f"noted against {app_key(args[0])!r}")
    store.close()
    return 0


def cmd_forget(args: list[str]) -> int:
    if not args:
        print("usage: forget <app>")
        return 2
    key = app_key(" ".join(args))
    store = _store()
    with store._lock:
        for table in ("app_controls", "app_notes"):
            store._conn.execute(f"DELETE FROM {table} WHERE app_key = ?", (key,))  # noqa: S608
        cur = store._conn.execute("DELETE FROM apps WHERE key = ?", (key,))
        store._conn.commit()
    store.close()
    print("forgotten" if cur.rowcount else f"no app {key!r}")
    return 0 if cur.rowcount else 1


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    handler = {
        "list": cmd_list, "show": cmd_show,
        "note": cmd_note, "forget": cmd_forget,
    }.get(argv[0])
    if handler is None:
        print(__doc__)
        return 2
    if not _DB.exists() and argv[0] != "list":
        print(f"no app database at {_DB}")
        return 1
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
