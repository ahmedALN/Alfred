from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.ai.providers.base import ChatProvider
from src.brain.policy import Policy
from src.brain.skills import align, apply_params
from src.brain.types import Proposal, ProposalKind, Verdict
from src.tools.registry import ToolRegistry

_PLAN_SYSTEM = """You are Alfred's task planner. Break the user's goal into the \
fewest concrete steps that a tool-using agent can carry out on this Windows PC.

For each step give a 'done_when' that can be CHECKED from a tool result - name \
a specific observable thing (a control that appears, a value a tool returns, a \
file that exists, text shown in the UI). Vague checks like "it worked" are \
useless.

Prefer the ui_control tool (reads app controls by name, exact) over \
desktop_control (screenshot guessing). For file/system work prefer powershell \
and system_info.

Reply with ONLY this JSON:
{"plan":[{"step":"<imperative>","done_when":"<checkable condition>"}],
 "note":"<one line about anything risky or uncertain>"}
"""

_EXEC_SYSTEM = """You are Alfred's task executor, working through a plan one \
step at a time using tools.

Rules:
- Each reply is exactly ONE JSON object, nothing else.
- Only work on the CURRENT step. Do the smallest thing that makes its \
'done_when' true, then reply action=done.
- ui_control: call action='tree' first to see the real controls, then click / \
type by ref or name. Do NOT guess coordinates.
- If a call fails, try a different approach - never repeat the same failing \
call. If truly stuck on this step, action=give_up.
- Never claim done unless a tool result actually shows the 'done_when' is true.

Reply with one of:
{"action":"use_tool","tool":"<name>","args":{...},"rationale":"<short>"}
{"action":"done","evidence":"<the tool result that proves done_when>"}
{"action":"give_up","reason":"<what blocked you>"}
"""

_VERIFY_SYSTEM = """You check whether a task step really finished. Be strict: \
only say VERIFIED if the log contains a concrete tool result that shows the \
'done_when' condition is actually true right now. An executor SAYING it's done \
is not evidence. If you are unsure, say UNVERIFIED.

Reply with exactly one line: 'VERIFIED: <why>' or 'UNVERIFIED: <what's \
missing>'."""


@dataclass
class Step:
    index: int
    thought: str
    tool: str | None
    args: dict[str, Any]
    verdict: str
    result: Any
    ok: bool


@dataclass
class TaskResult:
    goal: str
    status: str  # done | partial | failed | uncertain | gave_up | exhausted
                 # | cancelled | error
    summary: str
    steps: list[Step] = field(default_factory=list)
    skipped_confirmations: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status,
            "summary": self.summary,
            "steps": len(self.steps),
            "plan": self.plan,
            "verified": self.verified,
            "unverified": self.unverified,
            "skipped_confirmations": self.skipped_confirmations,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }

    def tool_trace(self) -> list[tuple[str, dict[str, Any]]]:
        """The tool calls that actually ran and succeeded, in order -
        the raw material for distilling a reusable skill."""
        return [
            (s.tool, s.args)
            for s in self.steps
            if s.ok and s.verdict == "auto" and s.tool
        ]


class TaskAgent:
    """
    Bounded plan -> act -> observe -> retry loop.

    Runs synchronously (call it from a worker thread). Executes tool
    calls through the shared ToolRegistry, gated by the same Policy the
    background brain uses: safe/reversible steps run, dangerous ones are
    skipped and reported for the user to approve later.
    """

    def __init__(
        self,
        chat: ChatProvider,
        registry: ToolRegistry,
        policy: Policy,
        *,
        policy_voice: Policy | None = None,
        plan_chat: ChatProvider | None = None,
        max_steps: int = 20,
        max_seconds: float = 360.0,
        substep_max_calls: int = 5,
        audit: Any = None,
    ) -> None:
        self._chat = chat
        self._plan_chat = plan_chat or chat
        self._registry = registry
        self._policy_brain = policy
        self._policy_voice = policy_voice or policy
        self._policy = policy  # active policy, set per run()
        self._ask_user: "Callable[[str], bool] | None" = None
        self._max_steps = max_steps
        self._max_seconds = max_seconds
        self._substep_max_calls = substep_max_calls
        self._audit = audit
        self._catalogue = ""
        self._deadline = 0.0
        self._cancel_check: "Callable[[], bool]" = lambda: False

    # ----------------------------------------------------------------

    def run(
        self,
        goal: str,
        session_id: str | None = None,
        cancel_check: "Callable[[], bool] | None" = None,
        on_progress: "Callable[[str], None] | None" = None,
        *,
        source: str = "brain",
        ask_user: "Callable[[str], bool] | None" = None,
    ) -> TaskResult:
        goal = goal.strip()
        started = time.monotonic()
        self._deadline = started + self._max_seconds
        self._cancel_check = cancel_check or (lambda: False)

        self._policy = (
            self._policy_voice if source == "voice" else self._policy_brain
        )
        self._ask_user = ask_user

        result = TaskResult(goal=goal, status="failed", summary="")
        self._catalogue = "\n".join(
            f"- {t.get('name')}: {t.get('description', '')}"
            for t in self._registry.gemini_declarations()
        )
        history: list[str] = []
        self._log("task_start", {"goal": goal}, session_id)

        if self._cancel_check():
            result.status = "cancelled"
            result.elapsed_seconds = time.monotonic() - started
            self._finalize(result)
            return result

        # 1. PLAN
        plan = self._make_plan(goal)
        result.plan = [p["step"] for p in plan]
        if on_progress is not None:
            on_progress("Plan: " + "; ".join(result.plan[:6]))
        self._log("task_plan", {"goal": goal, "plan": plan}, session_id)

        replans = 0
        pi = 0
        total_calls = 0

        while pi < len(plan):
            if self._cancel_check():
                result.status = "cancelled"
                break
            if time.monotonic() > self._deadline:
                result.status = "exhausted"
                break
            if total_calls >= self._max_steps:
                break

            pstep = plan[pi]
            if on_progress is not None and pi > 0:
                on_progress(f"Step {pi + 1}/{len(plan)}: {pstep['step'][:70]}")

            before = len(result.steps)
            calls = self._execute_substep(
                goal, plan, pi, history, result, session_id,
                budget=self._max_steps - total_calls,
            )
            total_calls += calls
            progressed = any(s.ok for s in result.steps[before:])

            if not progressed:
                # The executor did nothing that worked - a "done" here is the
                # exact pattern behind the "it said it opened Drake" bug.
                ok, evidence = False, "no successful tool action for this step"
            else:
                ok, evidence = self._verify(pstep, history)
            self._log(
                "task_verify",
                {"step": pstep["step"], "verified": ok, "evidence": evidence},
                session_id,
            )

            if ok:
                result.verified.append(pstep["step"])
                pi += 1
                continue

            if replans < 2 and total_calls < self._max_steps:
                replans += 1
                history.append(
                    f"[replan {replans}] step '{pstep['step']}' not verified: "
                    f"{evidence}"
                )
                remainder = self._make_plan(
                    goal,
                    extra=(
                        f"Done so far: {result.verified or 'nothing'}. "
                        f"Stuck on: {pstep['step']} - {evidence}. "
                        "Give the remaining steps only."
                    ),
                )
                plan = plan[:pi] + remainder
                result.plan = [p["step"] for p in plan]
                continue

            result.unverified.append(f"{pstep['step']} ({evidence})")
            pi += 1

        # 4. REPORT - only from what was verified
        result.elapsed_seconds = time.monotonic() - started
        self._finalize(result)
        self._log("task_end", result.as_dict(), session_id)
        return result

    # ----------------------------------------------------------------

    def replay(
        self,
        skill: dict[str, Any],
        request: str,
        session_id: str | None = None,
        cancel_check: "Callable[[], bool] | None" = None,
        on_progress: "Callable[[str], None] | None" = None,
        *,
        source: str = "voice",
        ask_user: "Callable[[str], bool] | None" = None,
    ) -> TaskResult:
        """Run a learned skill's steps directly - no planning call. Params
        are filled from ``request``; the skill's ``verify`` is still checked
        so a stale skill can't lie."""

        goal = request.strip()
        started = time.monotonic()
        self._deadline = started + self._max_seconds
        self._cancel_check = cancel_check or (lambda: False)
        self._policy = (
            self._policy_voice if source == "voice" else self._policy_brain
        )
        self._ask_user = ask_user

        done_when = str(skill.get("verify") or goal)
        result = TaskResult(goal=goal, status="failed", summary="")
        result.plan = [done_when]

        values = align(str(skill.get("template", "")), goal) or {}
        missing = [p for p in skill.get("params", []) if p not in values]
        if missing:
            result.unverified.append(
                f"{done_when} (couldn't read {', '.join(missing)} from '{goal}')"
            )
            result.elapsed_seconds = time.monotonic() - started
            self._finalize(result)
            self._log("skill_replay", result.as_dict(), session_id)
            return result

        steps = apply_params(list(skill.get("steps", [])), values)
        history: list[str] = []
        if on_progress is not None:
            on_progress(f"Doing '{goal}' from a saved routine.")

        for i, st in enumerate(steps, 1):
            if self._cancel_check():
                result.status = "cancelled"
                break
            if time.monotonic() > self._deadline:
                result.status = "exhausted"
                break
            decision = {
                "tool": st.get("tool"),
                "args": st.get("args", {}),
                "rationale": f"replay step {i} of '{skill.get('name', 'skill')}'",
            }
            step = self._run_tool_step(
                len(result.steps) + 1, decision, history, result, session_id
            )
            result.steps.append(step)

        progressed = any(s.ok for s in result.steps)

        if result.status not in ("cancelled", "exhausted"):
            if not progressed:
                result.unverified.append(f"{done_when} (skill ran no actions)")
            else:
                ok, evidence = self._verify(
                    {"step": skill.get("name", goal), "done_when": done_when},
                    history,
                )
                if ok:
                    result.verified.append(done_when)
                else:
                    result.unverified.append(f"{done_when} ({evidence})")

        result.elapsed_seconds = time.monotonic() - started
        self._finalize(result)
        self._log("skill_replay", result.as_dict(), session_id)
        return result

    # ----------------------------------------------------------------

    def _make_plan(self, goal: str, extra: str = "") -> list[dict[str, str]]:
        prompt = (
            f"{_PLAN_SYSTEM}\n\nGOAL: {goal}\n\nTOOLS:\n{self._catalogue}\n"
            + (f"\nCONTEXT: {extra}\n" if extra else "")
            + "\nYour JSON:"
        )
        try:
            raw = self._plan_chat.generate(prompt, temperature=0.2)
        except Exception as exc:  # noqa: BLE001
            print(f"[Task] planner failed ({exc}); using a single step.")
            return [{"step": goal, "done_when": goal}]

        obj = _parse(raw) or {}
        plan = obj.get("plan")
        steps: list[dict[str, str]] = []
        if isinstance(plan, list):
            for item in plan:
                if isinstance(item, dict) and item.get("step"):
                    steps.append({
                        "step": str(item["step"]).strip(),
                        "done_when": str(item.get("done_when", "")).strip()
                        or str(item["step"]).strip(),
                    })
        return steps or [{"step": goal, "done_when": goal}]

    def _execute_substep(
        self,
        goal: str,
        plan: list[dict[str, str]],
        pi: int,
        history: list[str],
        result: TaskResult,
        session_id: str | None,
        *,
        budget: int,
    ) -> int:
        pstep = plan[pi]
        plan_view = "\n".join(
            f"  {'>' if j == pi else ' '} {p['step']}"
            for j, p in enumerate(plan)
        )
        calls = 0
        for _ in range(max(0, min(self._substep_max_calls, budget))):
            if self._cancel_check() or time.monotonic() > self._deadline:
                break

            prompt = (
                f"{_EXEC_SYSTEM}\n\nOVERALL GOAL: {goal}\n\nPLAN:\n{plan_view}\n\n"
                f"CURRENT STEP: {pstep['step']}\nDONE WHEN: {pstep['done_when']}\n\n"
                f"TOOLS:\n{self._catalogue}\n\n"
                f"HISTORY:\n" + ("\n".join(history[-14:]) or "(nothing yet)")
                + "\n\nYour next JSON:"
            )
            try:
                raw = self._chat.generate(prompt, temperature=0.2)
            except Exception as exc:  # noqa: BLE001
                history.append(f"[system] executor model error: {exc}")
                break

            decision = _parse(raw)
            if decision is None:
                history.append(f"[system] unparseable: {raw[:120]}")
                continue

            action = decision.get("action")
            if action == "done":
                history.append(
                    f"[step {pi + 1} executor claims done] "
                    f"{decision.get('evidence', '')}"
                )
                break
            if action == "give_up":
                history.append(
                    f"[step {pi + 1} executor gave up] {decision.get('reason', '')}"
                )
                break
            if action != "use_tool":
                history.append(f"[system] unknown action {action!r}")
                continue

            step = self._run_tool_step(
                len(result.steps) + 1, decision, history, result, session_id
            )
            result.steps.append(step)
            calls += 1

        return calls

    def _verify(
        self, pstep: dict[str, str], history: list[str]
    ) -> tuple[bool, str]:
        prompt = (
            f"{_VERIFY_SYSTEM}\n\nSTEP: {pstep['step']}\n"
            f"DONE WHEN: {pstep['done_when']}\n\n"
            f"LOG:\n" + "\n".join(history[-16:]) + "\n\nYour one line:"
        )
        try:
            raw = self._chat.generate(prompt, temperature=0.0).strip()
        except Exception as exc:  # noqa: BLE001
            return False, f"could not verify: {exc}"

        line = raw.splitlines()[0] if raw else ""
        if line.upper().startswith("VERIFIED"):
            return True, line.split(":", 1)[-1].strip()[:200]
        return False, line.split(":", 1)[-1].strip()[:200] or "no evidence"

    def _finalize(self, result: TaskResult) -> None:
        n_plan = len(result.plan)
        n_ok = len(result.verified)

        if result.status in ("cancelled", "exhausted", "error"):
            base = {
                "cancelled": "Stopped at your request",
                "exhausted": "Ran out of time",
                "error": "Hit an error",
            }[result.status]
        elif n_ok == n_plan and n_plan:
            result.status = "done"
            base = "Done"
        elif n_ok:
            result.status = "partial"
            base = "Partly done"
        else:
            result.status = "failed"
            base = "Couldn't do it"

        parts = [f"{base} on '{result.goal}'."]
        if result.verified:
            parts.append("Confirmed: " + "; ".join(result.verified) + ".")
        if result.unverified:
            parts.append(
                "Couldn't confirm: " + "; ".join(result.unverified) + "."
            )
        if result.skipped_confirmations:
            parts.append(
                "Left for you: " + "; ".join(result.skipped_confirmations) + "."
            )
        result.summary = " ".join(parts)

    # ----------------------------------------------------------------

    def _run_tool_step(
        self,
        index: int,
        decision: dict[str, Any],
        history: list[str],
        result: TaskResult,
        session_id: str | None,
    ) -> Step:
        tool = decision.get("tool")
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        thought = str(decision.get("rationale", "")).strip()

        proposal = Proposal(
            kind=ProposalKind.ACT, message=thought or f"use {tool}",
            tool=tool, args=args,
        )
        verdict = self._policy.evaluate(proposal)

        if verdict.verdict is Verdict.FORBID:
            history.append(
                f"[step {index}] REFUSED {tool}: {verdict.reason}"
            )
            return Step(index, thought, tool, args, "forbid",
                        {"refused": verdict.reason}, False)

        if verdict.verdict is Verdict.CONFIRM:
            if self._ask_user is not None:
                question = (
                    f"Sir, this step ({thought or tool}) is a bit risky - "
                    f"{verdict.reason}. Do you want me to go ahead?"
                )
                try:
                    approved = bool(self._ask_user(question))
                except Exception as exc:  # noqa: BLE001
                    print(f"[Task] ask_user failed: {exc}")
                    approved = False

                if not approved:
                    result.skipped_confirmations.append(
                        f"{tool} ({thought or 'no rationale'}) - you said no"
                    )
                    history.append(
                        f"[step {index}] {tool}: user declined. Skip it."
                    )
                    return Step(index, thought, tool, args, "declined",
                                {"declined": verdict.reason}, False)
                history.append(f"[step {index}] {tool}: user approved.")
                # fall through and execute
            else:
                note = f"{tool} ({thought or 'no rationale'})"
                result.skipped_confirmations.append(note)
                history.append(
                    f"[step {index}] SKIPPED {tool}: needs the user's OK "
                    f"({verdict.reason}). Continue with other steps."
                )
                return Step(index, thought, tool, args, "confirm",
                            {"skipped": verdict.reason}, False)

        try:
            outcome = self._registry.execute(tool, args)
        except Exception as exc:  # noqa: BLE001
            outcome = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        ok = not (isinstance(outcome, dict) and outcome.get("status") == "error")

        summary = _short(outcome)
        history.append(
            f"[step {index}] {tool}({_short(args)}) -> "
            f"{'ok' if ok else 'FAILED'}: {summary}"
        )

        self._log(
            "task_step",
            {"index": index, "tool": tool, "args": args, "ok": ok,
             "result": summary},
            session_id,
        )

        return Step(index, thought, tool, args, "auto", outcome, ok)

    def _log(self, kind: str, payload: dict[str, Any], session_id: str | None):
        if self._audit is not None:
            try:
                self._audit.record(kind, payload, session_id)
            except Exception:  # noqa: BLE001
                pass


# ====================================================================
# helpers
# ====================================================================


def _parse(raw: str) -> dict[str, Any] | None:
    text = raw.strip()

    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.lower().startswith("json"):
            text = text[4:]

    text = text.strip()

    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _short(value: Any, limit: int = 240) -> str:
    try:
        text = json.dumps(value, default=str) if not isinstance(value, str) else value
    except Exception:  # noqa: BLE001
        text = str(value)

    return text if len(text) <= limit else text[:limit] + "…"
