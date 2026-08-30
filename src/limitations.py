"""
python -m src.limitations  -  what Alfred keeps running into.

    list        walls hit, most frequent first
    unsolved    the ones nothing has got past yet
    clear       forget them all and start counting again

A wall hit once is bad luck. A wall hit repeatedly with something known
to get past it becomes a standing lesson on its own; the ones with no
way past are the honest list of what Alfred cannot currently do.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / os.getenv(
    "ALFRED_LIMITATIONS_DB", "alfred_limitations.sqlite3"
)


def _store():
    from src.brain.limitations import LimitationStore

    return LimitationStore(_DB)


def cmd_list(_args: list[str]) -> int:
    store = _store()
    rows = store.all()

    if not rows:
        print("nothing run into yet.")
        store.close()
        return 0

    print(f"{'hits':>4}  {'taught':>6}  where / what")
    for row in rows:
        where = row["app"] or "-"
        print(f"{row['hits']:>4}  {'yes' if row['taught'] else 'no':>6}  "
              f"{row['tool']} in {where}: {row['detail'][:70]}")
        if row["workaround"]:
            print(f"{'':>14}  -> got past with: {row['workaround']}")
    store.close()
    return 0


def cmd_unsolved(_args: list[str]) -> int:
    store = _store()
    rows = store.unsolved(min_hits=2)

    if not rows:
        print("nothing recurring that Alfred has no answer for.")
    else:
        print("hit more than once, with nothing known to get past them:")
        for row in rows:
            where = row["app"] or "-"
            print(f"  {row['hits']:>3}x  {row['tool']} in {where}: "
                  f"{row['detail'][:76]}")
    store.close()
    return 0


def cmd_clear(_args: list[str]) -> int:
    import sqlite3

    conn = sqlite3.connect(str(_DB))
    removed = conn.execute("DELETE FROM limitations").rowcount
    conn.commit()
    conn.close()
    print(f"forgot {removed} recorded wall(s).")
    return 0


_COMMANDS = {"list": cmd_list, "unsolved": cmd_unsolved, "clear": cmd_clear}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "list"

    if command not in _COMMANDS:
        print(__doc__)
        return 1

    return _COMMANDS[command](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
