"""
python -m src.teach  -  show Alfred how to do useful things, once.

A skill is a route Alfred has walked and can walk again without
thinking: the tool calls that worked, kept, and replayed on request. It
turns a job that costs a planning call and several executor calls into
one that costs none of them.

Alfred already learns skills on its own, from tasks it happens to be
given. That leaves it good at whatever it has been asked recently and
blank on everything else. This walks it through a curriculum instead -
things worth being able to do on this machine, done once properly, kept
if they worked.

    python -m src.teach              teach everything not already known
    python -m src.teach --all        re-teach, including what is known
    python -m src.teach --list       what it can do without thinking
    python -m src.teach --time       how much a learned route actually saves

Nothing here plays audio, opens anything destructive, or touches a file
of the user's. It is meant to be runnable while somebody is asleep.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


# Each lesson is a real request, with a check that looks at the machine
# rather than at Alfred's account of itself.
CURRICULUM: list[dict] = [
    # -- knowing the machine -------------------------------------
    {
        "goal": "How much memory is in use right now?",
        # Answered in GB or in per cent - both are the answer.
        "check": ("answer_says_something",),
    },
    {
        "goal": "What is my computer's name?",
        "check": ("answer_says_something",),
    },
    {
        "goal": "How long has this PC been up?",
        "check": ("answer_says_something",),
    },
    {
        "goal": "What windows are open right now?",
        "check": ("answer_says_something",),
    },
    {
        "goal": "Is Steam running?",
        "check": ("answer_mentions", "steam"),
    },
    {
        "goal": "Which processes are using the most memory?",
        "check": ("answer_says_something",),
    },
    # -- getting about -------------------------------------------
    {
        "goal": "Open the Downloads folder.",
        "check": ("window", "Downloads"),
        "after": ("close", "Downloads"),
    },
    {
        "goal": "Open Notepad.",
        "check": ("window", "Notepad"),
        "after": ("close", "Notepad"),
    },
    # -- working inside apps -------------------------------------
    {
        "goal": "Open Steam and search the store for Hollow Knight.",
        "check": ("window", "Steam"),
    },
    {
        "goal": "In MultiMC, select the 1.21.11 instance.",
        "check": ("window", "MultiMC"),
    },
    {
        "goal": "Open MultiMC.",
        "check": ("window", "MultiMC"),
    },
    {
        "goal": "Close Notepad.",
        "check": ("no_window", "Notepad"),
        "before": ("open", "notepad.exe"),
    },
    # -- looking things up ---------------------------------------
    {
        "goal": "Search the web for the height of Ben Nevis.",
        "check": ("answer_says_something",),
    },
    {
        "goal": "Look up what the weather is in London today.",
        "check": ("answer_says_something",),
    },
    # -- the machine, in more detail -----------------------------
    {
        "goal": "What is my local IP address?",
        "check": ("answer_says_something",),
    },
    {
        "goal": "How much space is left on every drive?",
        "check": ("answer_says_something",),
    },
    {
        "goal": "What is my graphics card?",
        "check": ("answer_says_something",),
    },
]


# ------------------------------------------------------------ checking


def _windows(ui) -> list[str]:
    try:
        return [w["title"] for w in ui.execute({"action": "windows"})["windows"]]
    except Exception:  # noqa: BLE001
        return []


def verify(check, result, ui) -> tuple[bool, str]:
    kind = check[0]

    if kind == "window":
        hit = [t for t in _windows(ui) if check[1].lower() in t.lower()]
        return bool(hit), (hit[0][:46] if hit else "no " + check[1] + " window")

    if kind == "no_window":
        hit = [t for t in _windows(ui) if check[1].lower() in t.lower()]
        return (not hit), ("still open: " + hit[0][:36]) if hit else "gone"

    if kind == "answer_mentions":
        said = (str(result.answer) + " " + str(result.summary)).lower()
        return check[1].lower() in said, (result.answer or result.summary)[:60]

    if kind == "answer_says_something":
        said = str(result.answer).strip()
        return len(said) > 8, said[:60] or "(said nothing)"

    return False, "unknown check " + str(kind)


def tidy(after, ui) -> None:
    """Put the desk back. A lesson should not leave anything open."""
    if not after:
        return
    if after[0] == "close":
        try:
            ui.execute({"action": "close", "window": after[1]})
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------ teaching


def build():
    from src import bench

    agent, ui = bench.build()

    from src.ai.providers.factory import build_providers  # noqa: F401
    from src.brain.skills import SkillLibrary
    from src.brain.skill_store import SkillStore
    from src.config import load_settings

    settings = load_settings()
    library = SkillLibrary(SkillStore(settings.skill_db_path))
    return agent, ui, library


def already_known(library, goal: str) -> bool:
    try:
        return library.match(goal) is not None
    except Exception:  # noqa: BLE001
        return False


def setup(before) -> None:
    """Some lessons need something to act on - you cannot practise
    closing Notepad without a Notepad."""
    if not before:
        return
    if before[0] == "open":
        import subprocess

        subprocess.Popen([before[1]])
        time.sleep(3)


def teach_one(agent, ui, library, lesson: dict) -> dict:
    goal = lesson["goal"]
    setup(lesson.get("before"))
    started = time.time()

    try:
        result = agent.run(goal, source="voice")
    except Exception as exc:  # noqa: BLE001
        return {"goal": goal, "learned": False, "ok": False,
                "secs": round(time.time() - started, 1),
                "why": (type(exc).__name__ + ": " + str(exc))[:70]}

    ok, detail = verify(lesson["check"], result, ui)
    tidy(lesson.get("after"), ui)

    row = {
        "goal": goal, "ok": ok, "learned": False,
        "secs": round(time.time() - started, 1),
        "steps": len(result.steps),
        "why": str(detail)[:70],
    }

    if not ok:
        row["why"] = "did not work: " + row["why"]
        return row

    # Same rule the task queue uses: nothing is learned from a run that
    # stumbled, because replaying it means stumbling again on purpose.
    if any(s.tool and not s.ok for s in result.steps):
        row["why"] = "worked, but not cleanly - not worth keeping"
        return row

    trace = result.tool_trace()
    if not trace:
        row["why"] = "nothing to keep (no tool calls)"
        return row

    try:
        skill = library.distill(
            goal, trace, verify="; ".join(result.verified) or goal
        )
        if skill is None:
            row["why"] = "could not be turned into a routine"
            return row
        if library.needs_confirmation(skill):
            row["why"] = "involves something risky - left for you to approve"
            return row
        library.save(skill)
        row["learned"] = True
        row["name"] = skill["name"]
        row["why"] = str(skill.get("template", goal))[:70]
    except Exception as exc:  # noqa: BLE001
        row["why"] = "could not keep it: " + str(exc)[:50]

    return row


def cmd_teach(argv: list[str]) -> int:
    everything = "--all" in argv
    agent, ui, library = build()

    lessons = CURRICULUM
    if not everything:
        lessons = [l for l in CURRICULUM if not already_known(library, l["goal"])]
        skipped = len(CURRICULUM) - len(lessons)
        if skipped:
            print("  " + str(skipped) + " already known - use --all to redo them\n")

    rows = []
    for lesson in lessons:
        row = teach_one(agent, ui, library, lesson)
        rows.append(row)
        mark = "learned" if row["learned"] else ("ok     " if row["ok"] else "no     ")
        print(
            mark + " {:6.1f}s  ".format(row["secs"])
            + row["goal"][:44].ljust(44) + " " + row["why"][:52],
            flush=True,
        )

    learned = sum(1 for r in rows if r["learned"])
    worked = sum(1 for r in rows if r["ok"])
    print()
    print("  worked   {}/{}".format(worked, len(rows)))
    print("  learned  {}".format(learned))

    (_ROOT / "teach-last.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    return 0


def cmd_list(_argv: list[str]) -> int:
    _, _, library = build()
    skills = library._store.all()

    if not skills:
        print("nothing learned yet.")
        return 0

    print("what Alfred can do without thinking about it:\n")
    for s in sorted(skills, key=lambda x: -x["confidence"]):
        used = s["success"] + s["fail"]
        print(
            "  " + s["template"][:52].ljust(54)
            + "{} step(s)  used {}x  confidence {:.2f}".format(
                len(s["steps"]), used, s["confidence"]
            )
        )
    return 0


def cmd_time(_argv: list[str]) -> int:
    """What a learned route is actually worth, in seconds."""
    agent, ui, library = build()
    rows = []

    for lesson in CURRICULUM:
        goal = lesson["goal"]
        skill = library.match(goal)
        if skill is None:
            continue

        started = time.time()
        try:
            agent.replay(skill, goal, source="voice")
            took = time.time() - started
        except Exception as exc:  # noqa: BLE001
            print("  {:44} replay failed: {}".format(goal[:44], str(exc)[:40]))
            continue

        tidy(lesson.get("after"), ui)
        rows.append((goal, took))
        print("  {:46} {:5.1f}s from memory".format(goal[:46], took))

    if rows:
        mean = sum(t for _, t in rows) / len(rows)
        print("\n  {} learned routes, mean {:.1f}s".format(len(rows), mean))
    else:
        print("  nothing learned to time yet - run: python -m src.teach")
    return 0


def main(argv: list[str]) -> int:
    if "--list" in argv:
        return cmd_list(argv)
    if "--time" in argv:
        return cmd_time(argv)
    return cmd_teach(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
