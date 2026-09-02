"""Working out how to do something, and keeping the answer.

Alfred already learned skills, but only by accident: do a job well
once, and the sequence that worked was distilled into a routine. That
is a good way to get better at what you already do and no way at all to
get better at what you have just been asked for the first time.

This is the deliberate version. Give it a goal and it designs the steps
- using only tools that actually exist, with arguments the schema
actually accepts - and hands back something in the same shape a
distilled skill has, so everything downstream treats the two alike.

The validation is the whole point. A model asked for a plan will
cheerfully invent a tool called `sleep_pc`, and a skill whose first
step names a tool that is not there is worse than no skill: it fails
later, further from the cause, and it fails every single time. So
nothing is saved that was not checked against the live registry first.

A designed skill is never trusted the way an executed one is. It is
saved unconfirmed and at low confidence: it is a plan that has never
been run, and the first real attempt is what earns it a promotion.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from src.brain.skills import _keywords, _slug

_MAX_STEPS = 8

_SYSTEM = """You design short, reusable routines for a Windows assistant.

Given a goal, write the sequence of tool calls that achieves it.

Rules:
- Use ONLY the tools listed. Never invent one.
- Use ONLY argument names the tool's schema lists.
- Keep it to the fewest steps that actually do the job.
- The routine must be REUSABLE, so put anything that changes between
  runs in {p0}, {p1} ... rather than hard-coding it. "Search Steam for
  Hades" becomes "Search Steam for {p0}".
- If the goal cannot be done with these tools, say so.

Answer with JSON only:

{"steps": [{"tool": "name", "args": {...}}, ...],
 "template": "the goal, with {p0} where the variable part goes",
 "verify": "how you would know it worked"}

Or, if it cannot be done:

{"impossible": "the reason, in one sentence"}
"""


class Impossible(RuntimeError):
    """The goal cannot be reached with the tools that exist."""


def catalogue(registry: Any, skip: tuple[str, ...] = ()) -> str:
    """The tools, as the designer is allowed to see them."""
    lines = []
    for name in sorted(registry.names()):
        if name in skip:
            continue
        try:
            tool = registry.get(name)
        except Exception:  # noqa: BLE001
            continue
        try:
            schema = tool.parameters_schema or {}
            args = ", ".join(sorted((schema.get("properties") or {}).keys()))
        except Exception:  # noqa: BLE001
            args = ""
        description = " ".join((getattr(tool, "description", "") or "").split())
        lines.append(f"- {name}({args}): {description[:200]}")
    return "\n".join(lines)


def _parse(raw: str) -> dict[str, Any]:
    """The model's answer, out of whatever it wrapped it in."""
    text = (raw or "").strip()
    if not text:
        raise Impossible("the designer said nothing")

    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise Impossible("the designer did not answer with a routine")

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise Impossible(f"the designer's answer was not readable: {exc}") from exc


def _check(steps: Any, registry: Any) -> list[dict[str, Any]]:
    """Every step must name a real tool and real arguments.

    This is where invented tools are caught. It is worth being strict:
    the cost of rejecting a good plan is that Alfred does the job the
    slow way once more, and the cost of accepting a bad one is a
    routine that fails for ever after.
    """
    if not isinstance(steps, list) or not steps:
        raise Impossible("the routine had no steps")
    if len(steps) > _MAX_STEPS:
        raise Impossible(f"the routine had {len(steps)} steps; that is too many")

    known = set(registry.names())
    checked: list[dict[str, Any]] = []

    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise Impossible(f"step {i} was not a tool call")

        tool = str(step.get("tool") or "").strip()
        if tool not in known:
            raise Impossible(
                f"step {i} uses '{tool}', which is not a tool Alfred has"
            )

        args = step.get("args") or {}
        if not isinstance(args, dict):
            raise Impossible(f"step {i} of '{tool}' had arguments in the wrong shape")

        instance = registry.get(tool)
        schema = (getattr(instance, "parameters_schema", None) or {})
        allowed = set((schema.get("properties") or {}).keys())
        if allowed:
            unknown = set(args) - allowed
            if unknown:
                raise Impossible(
                    f"step {i} passes {sorted(unknown)} to '{tool}', which "
                    f"only takes {sorted(allowed)}"
                )
            missing = set(schema.get("required") or []) - set(args)
            if missing:
                raise Impossible(
                    f"step {i} of '{tool}' is missing {sorted(missing)}"
                )

        checked.append({"tool": tool, "args": args})

    return checked


def design(
    goal: str,
    chat: Any,
    registry: Any,
    *,
    learner: Any = None,
    skip: tuple[str, ...] = ("run_task", "task_status", "interface"),
) -> dict[str, Any]:
    """Design a routine for ``goal``. Raises Impossible if it cannot.

    Returns an unsaved skill in the same shape distill() produces, so
    matching, replay and the interface need to know nothing about where
    a skill came from.
    """
    goal = (goal or "").strip()
    if not goal:
        raise Impossible("no goal given")

    prompt = (
        f"Tools available:\n{catalogue(registry, skip)}\n\n"
        f"Goal: {goal}\n\n"
        "Design the routine."
    )
    try:
        raw = chat.generate(prompt, system=_SYSTEM, temperature=0.2,
                            max_tokens=700)
    except Exception as exc:
        raise Impossible(f"could not reach the designer: {exc}") from exc

    answer = _parse(raw)
    if answer.get("impossible"):
        raise Impossible(str(answer["impossible"])[:200])

    steps = _check(answer.get("steps"), registry)
    template = str(answer.get("template") or goal).strip()

    params = sorted(set(re.findall(r"\{(p\d+)\}", json.dumps(steps) + template)))

    tier, note = "ordinary", ""
    if learner is not None:
        try:
            tier, note = learner._classify([(s["tool"], s["args"]) for s in steps])
        except Exception:  # noqa: BLE001
            pass

    return {
        "id": uuid.uuid4().hex[:10],
        "name": _slug(goal),
        "template": template,
        "keywords": [k for k in _keywords(goal) if not k.startswith("p")],
        "params": params,
        "steps": steps,
        "verify": str(answer.get("verify") or goal)[:300],
        "app": str(answer.get("app") or ""),
        "tier": tier,
        "danger_note": note,
        # It has never been run. Nothing about designing it says it works.
        "success": 0,
        "fail": 0,
        "confidence": 0.35,
        "unconfirmed": 1,
    }
