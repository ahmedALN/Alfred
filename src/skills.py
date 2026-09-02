"""
python -m src.skills  -  inspect Alfred's learned skill library.

    list                 every skill, newest-used first
    show <id>            full detail for one skill (steps, params, stats)
    forget <id>         delete a skill permanently
    disable <id>        keep it but stop matching it
    enable <id>         re-enable a disabled skill
    dedupe [--dry]      fold routines that do the same thing into one
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from src.brain.skill_store import SkillStore

_DB = Path(__file__).resolve().parent.parent / os.getenv(
    "ALFRED_SKILL_DB", "alfred_skills.sqlite3"
)


def _store() -> SkillStore:
    return SkillStore(_DB)


def _line(s: dict) -> str:
    flags = []
    if s["disabled"]:
        flags.append("disabled")
    if s["unconfirmed"]:
        flags.append("unconfirmed")
    if s["tier"] == "dangerous":
        flags.append("dangerous")
    tag = f"  [{', '.join(flags)}]" if flags else ""
    return (
        f"  {s['id']}  {s['name']:<28} "
        f"ok={s['success']} fail={s['fail']} conf={s['confidence']:.2f}"
        f"{tag}\n      \"{s['template']}\""
    )


def cmd_list(_args: list[str]) -> int:
    store = _store()
    skills = store.all(include_disabled=True)
    print(f"{len(skills)} skill(s):")
    for s in skills:
        print(_line(s))
    store.close()
    return 0


def cmd_show(args: list[str]) -> int:
    if not args:
        print("usage: show <id>")
        return 2
    store = _store()
    s = store.get(args[0])
    store.close()
    if s is None:
        print(f"no skill {args[0]!r}")
        return 1
    print(json.dumps(s, indent=2))
    return 0


def cmd_forget(args: list[str]) -> int:
    if not args:
        print("usage: forget <id>")
        return 2
    store = _store()
    ok = store.delete(args[0])
    store.close()
    print("deleted" if ok else f"no skill {args[0]!r}")
    return 0 if ok else 1


def _set_disabled(args: list[str], disabled: bool) -> int:
    if not args:
        print(f"usage: {'disable' if disabled else 'enable'} <id>")
        return 2
    store = _store()
    ok = store.set_disabled(args[0], disabled)
    store.close()
    print("done" if ok else f"no skill {args[0]!r}")
    return 0 if ok else 1


def cmd_disable(args: list[str]) -> int:
    return _set_disabled(args, True)


def cmd_enable(args: list[str]) -> int:
    return _set_disabled(args, False)


def cmd_dedupe(args: list[str]) -> int:
    """Two rows at one success each are worth less than one row at two.

    A skill is named from the words of the request, so the same routine
    banked from two phrasings becomes two skills - and neither ever
    gathers the evidence that would let it be trusted.
    `check-whether-steam-is` and `is-steam-running` were the same three
    calls. This finds routines that run the same tools with the same
    argument shape for a recognisably similar request, and keeps the one
    that has actually worked.
    """
    from src.brain.skills import SkillLibrary

    store = _store()
    library = SkillLibrary(store)

    if "--dry" in args or "-n" in args:
        pairs = library.find_duplicates()
        store.close()

        if not pairs:
            print("nothing to fold - every routine is distinct")
            return 0

        for dropped, kept in pairs:
            print(f"  would fold  {dropped}  ->  {kept}")

        print(f"\n{len(pairs)} duplicate routine(s); run without --dry to fold")
        return 0

    folded = library.prune_duplicates()
    store.close()

    if not folded:
        print("nothing to fold - every routine is distinct")
        return 0

    for dropped, kept in folded:
        print(f"  {dropped}  ->  {kept}")

    print(f"\nfolded {len(folded)} duplicate routine(s)")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0

    cmd, rest = argv[0], argv[1:]
    handler = {
        "list": cmd_list, "show": cmd_show, "forget": cmd_forget,
        "disable": cmd_disable, "enable": cmd_enable,
        "dedupe": cmd_dedupe,
    }.get(cmd)

    if handler is None:
        print(__doc__)
        return 2

    if not _DB.exists() and cmd != "list":
        print(f"no skill database at {_DB}")
        return 1

    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
