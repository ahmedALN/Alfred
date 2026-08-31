"""
python -m src.bench  -  run real goals against real apps and time them.

Accuracy and speed are claims until something measures them. This runs
a fixed battery through the actual task agent, on the actual desktop,
and reports what worked and how long it took, so a change can be shown
to have helped rather than assumed to have.

    python -m src.bench             the standard battery
    python -m src.bench quick       the short one
    python -m src.bench "a goal"    just this

Every check looks at the world - is the window there, is the text in it -
rather than at what Alfred said about it. Alfred's own account of its
work is one of the things being measured.

Nothing here plays audio: it is meant to be runnable overnight in a room
somebody is sleeping in.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


BATTERY: list[dict] = [
    {
        "goal": "Open Notepad.",
        "check": ("window", "Notepad"),
        "tags": ["launch"],
    },
    {
        "goal": "Type the words quick brown fox into Notepad.",
        "check": ("text_in_window", "Notepad", "quick brown fox"),
        "tags": ["in-app"],
    },
    {
        "goal": "Check whether Steam is running.",
        "check": ("answer_mentions", "steam"),
        "tags": ["query"],
    },
    {
        "goal": "What version of Windows is this?",
        "check": ("answer_mentions", "11"),
        "tags": ["query"],
    },
    {
        "goal": "How much free space is on the C drive?",
        "check": ("answer_mentions", "gb"),
        "tags": ["query"],
    },
    {
        "goal": "Open Steam and search the store for Hades.",
        "check": ("window", "Steam"),
        "tags": ["in-app", "slow"],
    },
    {
        "goal": "In MultiMC, select the 1.21.11 instance.",
        "check": ("window", "MultiMC"),
        "tags": ["in-app", "unnamed-controls"],
    },
    {
        "goal": "Close Notepad without saving.",
        "check": ("no_window", "Notepad"),
        "tags": ["cleanup"],
    },
]

QUICK = {
    "Open Notepad.",
    "What version of Windows is this?",
    "Close Notepad without saving.",
}


# ----------------------------------------------------------------- checks


def _windows(ui) -> list[str]:
    try:
        return [w["title"] for w in ui.execute({"action": "windows"})["windows"]]
    except Exception:  # noqa: BLE001
        return []


def verify(check, result, ui) -> tuple[bool, str]:
    kind = check[0]

    if kind == "window":
        titles = _windows(ui)
        hit = [t for t in titles if check[1].lower() in t.lower()]
        return bool(hit), (hit[0][:50] if hit else "no " + check[1] + " window")

    if kind == "no_window":
        titles = _windows(ui)
        hit = [t for t in titles if check[1].lower() in t.lower()]
        return (not hit), ("still open: " + hit[0][:40]) if hit else "gone"

    if kind == "text_in_window":
        try:
            got = ui.execute({"action": "get", "window": check[1]})
            text = str(got.get("value") or got.get("text") or "")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:60]
        return check[2].lower() in text.lower(), text[:60]

    if kind == "answer_mentions":
        said = (str(result.answer) + " " + str(result.summary)).lower()
        return check[1].lower() in said, (result.answer or result.summary)[:70]

    return False, "unknown check " + str(kind)


# ------------------------------------------------------------------ run


def build():
    """The same pieces main.py wires up, minus voice and the brain.

    Deliberately small. If this drifts from main the bench fails
    loudly, which is what a bench is for.
    """
    from google import genai

    from src.ai.providers.factory import build_providers
    from src.brain.agent import TaskAgent
    from src.brain.app_memory import AppMemory
    from src.brain.limitations import LimitationStore
    from src.brain.policy import Policy
    from src.config import load_settings
    from src.tools.open_app import OpenAppTool
    from src.tools.powershell import PowerShellTool
    from src.tools.registry import ToolRegistry
    from src.tools.system_info import SystemInfoTool
    from src.tools.ui_control import UIControlTool
    from src.tools.web import WebTool

    settings = load_settings()
    providers = build_providers(
        settings, genai.Client(api_key=settings.gemini_api_key)
    )

    app_memory = AppMemory(settings.app_db_path)
    ui = UIControlTool(memory=app_memory, vision=providers.vision)

    registry = ToolRegistry()
    for tool in (PowerShellTool(), OpenAppTool(), SystemInfoTool(),
                 WebTool(), ui):
        registry.register(tool)

    known = {d["name"] for d in registry.gemini_declarations()}
    agent = TaskAgent(
        providers.plan_chat, registry,
        Policy("full", known, surface="brain"),
        policy_voice=Policy("full", known, surface="voice"),
        plan_chat=providers.plan_chat,
        app_memory=app_memory,
        limitations=LimitationStore(_ROOT / "alfred_limitations.sqlite3"),
    )
    return agent, ui


def run(goals: list[dict]) -> list[dict]:
    agent, ui = build()
    rows = []

    for case in goals:
        started = time.time()
        try:
            result = agent.run(case["goal"], source="voice")
            ok, detail = verify(case["check"], result, ui)
            rows.append({
                "goal": case["goal"],
                "ok": ok,
                "status": result.status,
                "secs": round(time.time() - started, 1),
                "steps": len(result.steps),
                "failed_steps": sum(
                    1 for s in result.steps if s.tool and not s.ok
                ),
                "detail": str(detail),
                "tags": case.get("tags", []),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({
                "goal": case["goal"], "ok": False, "status": "crash",
                "secs": round(time.time() - started, 1), "steps": 0,
                "failed_steps": 0,
                "detail": (type(exc).__name__ + ": " + str(exc))[:80],
                "tags": case.get("tags", []),
            })

        row = rows[-1]
        mark = "OK " if row["ok"] else "BAD"
        print(
            mark
            + " {:6.1f}s {:>2} steps ({} failed)  ".format(
                row["secs"], row["steps"], row["failed_steps"]
            )
            + row["goal"][:44].ljust(44)
            + " "
            + row["detail"][:46],
            flush=True,
        )

    return rows


def report(rows: list[dict]) -> None:
    ok = sum(1 for r in rows if r["ok"])
    total_secs = sum(r["secs"] for r in rows)
    steps = sum(r["steps"] for r in rows)
    failed = sum(r["failed_steps"] for r in rows)
    mean = total_secs / max(len(rows), 1)

    print()
    print("  passed      {}/{}".format(ok, len(rows)))
    print("  total time  {:.0f}s   (mean {:.1f}s)".format(total_secs, mean))
    print("  tool calls  {}   of which failed: {}".format(steps, failed))
    slowest = sorted(rows, key=lambda r: -r["secs"])[:3]
    print("  slowest     " + ", ".join(
        "{} {:.0f}s".format(r["goal"][:28], r["secs"]) for r in slowest
    ))


def main(argv: list[str]) -> int:
    arg = argv[0] if argv else ""

    if arg == "quick":
        goals = [c for c in BATTERY if c["goal"] in QUICK]
    elif arg:
        goals = [{"goal": arg, "check": ("answer_mentions", ""), "tags": []}]
    else:
        goals = BATTERY

    rows = run(goals)
    report(rows)

    out = _ROOT / "bench-last.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("\n  written to " + out.name)
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
