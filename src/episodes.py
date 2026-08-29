"""
python -m src.episodes  -  browse Alfred's episodic history.

    recent [hours]       activity in the last N hours (default 24)
    search <text>        episodes mentioning <text>
    prune [days]         drop episodes older than N days (default 30)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from src.memory.episodes import EpisodeStore

_DB = Path(__file__).resolve().parent.parent / os.getenv(
    "ALFRED_EPISODE_DB", "alfred_episodes.sqlite3"
)


def _store() -> EpisodeStore:
    return EpisodeStore(_DB)


def _fmt(e: dict) -> str:
    when = e["at"].replace("T", " ")[:19]
    tail = f"  -> {e['outcome']}" if e["outcome"] else ""
    return f"  {when}  [{e['kind']}] {e['summary']}{tail}"


def cmd_recent(args: list[str]) -> int:
    hours = float(args[0]) if args else 24.0
    store = _store()
    rows = store.recent(hours=hours)
    store.close()
    print(f"{len(rows)} episode(s) in the last {hours:g}h:")
    for e in rows:
        print(_fmt(e))
    return 0


def cmd_search(args: list[str]) -> int:
    if not args:
        print("usage: search <text>")
        return 2
    store = _store()
    rows = store.search(" ".join(args))
    store.close()
    for e in rows:
        print(_fmt(e))
    return 0


def cmd_prune(args: list[str]) -> int:
    days = float(args[0]) if args else 30.0
    store = _store()
    n = store.prune(keep_days=days)
    store.close()
    print(f"pruned {n} episode(s) older than {days:g} days")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0

    cmd, rest = argv[0], argv[1:]
    handler = {
        "recent": cmd_recent, "search": cmd_search, "prune": cmd_prune,
    }.get(cmd)

    if handler is None:
        print(__doc__)
        return 2

    if not _DB.exists() and cmd != "recent":
        print(f"no episode database at {_DB}")
        return 1

    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
