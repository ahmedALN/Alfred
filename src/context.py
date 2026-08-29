from __future__ import annotations

from typing import Any, Callable

from src.windows.system_probe import foreground_app, idle_seconds


def _fg() -> str | None:
    try:
        return foreground_app()
    except Exception:  # noqa: BLE001
        return None


def _idle() -> float:
    try:
        return idle_seconds()
    except Exception:  # noqa: BLE001
        return 0.0


def build_situation(
    *,
    task_queue: Any = None,
    resource_mode: Any = None,
    learner: Any = None,
    episodes: Any = None,
    foreground: Callable[[], str | None] = _fg,
    idle: Callable[[], float] = _idle,
    max_len: int = 900,
) -> str:
    """
    A compact snapshot of what's going on right now, injected into the
    voice system prompt, the task planner, and the deliberator so Alfred
    reasons with awareness of the foreground app, running work, the
    user's stated goal, and what it did earlier.

    Every input is optional and guarded - never raises.
    """

    lines: list[str] = []

    # --- right now ------------------------------------------------
    now_bits: list[str] = []
    try:
        app = foreground()
    except Exception:  # noqa: BLE001
        app = None
    if app:
        now_bits.append(f"foreground app {app}")

    try:
        secs = idle()
    except Exception:  # noqa: BLE001
        secs = 0.0
    if secs >= 300:
        now_bits.append(f"user idle {int(secs // 60)}m")
    else:
        now_bits.append("user active")

    if resource_mode is not None:
        try:
            if getattr(resource_mode, "in_game_mode", False):
                now_bits.append("game/low-resource mode ON")
        except Exception:  # noqa: BLE001
            pass

    if now_bits:
        lines.append("Right now: " + "; ".join(now_bits) + ".")

    # --- active goal ---------------------------------------------
    if learner is not None:
        try:
            goal = learner.active_goal()
            if goal:
                lines.append(f"User's current goal: {goal}.")
        except Exception:  # noqa: BLE001
            pass

    # --- tasks in flight ----------------------------------------
    if task_queue is not None:
        try:
            recs = task_queue.recent(limit=8)
            running = [r for r in recs if r.status == "running"]
            queued = [r for r in recs if r.status == "queued"]
            if running:
                lines.append(
                    "Working on: "
                    + "; ".join(f"'{r.goal}'" for r in running)
                    + (f" ({len(queued)} queued)" if queued else "")
                    + "."
                )
            elif queued:
                lines.append(f"{len(queued)} task(s) queued.")
        except Exception:  # noqa: BLE001
            pass

    # --- recently learned --------------------------------------
    if learner is not None:
        try:
            facts = learner.recent_facts(limit=4)
            if facts:
                lines.append(
                    "Recently learned: "
                    + "; ".join(f.content for f in facts)
                    + "."
                )
        except Exception:  # noqa: BLE001
            pass

    # --- earlier today ----------------------------------------
    if episodes is not None:
        try:
            eps = episodes.recent(hours=12.0, limit=5)
            if eps:
                lines.append(
                    "Earlier: "
                    + "; ".join(
                        e["summary"]
                        + (f" ({e['outcome']})" if e.get("outcome") else "")
                        for e in eps
                    )
                    + "."
                )
        except Exception:  # noqa: BLE001
            pass

    text = "\n".join(lines).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
