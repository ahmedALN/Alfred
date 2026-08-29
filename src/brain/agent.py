from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.ai.providers.base import ChatProvider
from src.brain.policy import Policy
from src.brain.types import Proposal, ProposalKind, Verdict
from src.tools.registry import ToolRegistry

_SYSTEM = """You are Alfred's task executor. You carry out one goal for the \
user by using tools, one step at a time, on Alfred's own isolated desktop.

Rules:
- Each reply is exactly ONE JSON object, nothing else.
- Prefer the smallest number of steps. Stop as soon as the goal is met.
- Use action='look' style inspection before you click or type.
- If a step fails, try a different approach; do not repeat the same failing
  call. If you are truly stuck, give up and say why.

Reply with one of:
{"action":"use_tool","tool":"<name>","args":{...},"rationale":"<short>"}
{"action":"done","summary":"<what you accomplished, one or two sentences>"}
{"action":"give_up","reason":"<what blocked you>"}
"""


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
    status: str  # "done" | "gave_up" | "exhausted" | "error"
    summary: str
    steps: list[Step] = field(default_factory=list)
    skipped_confirmations: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status,
            "summary": self.summary,
            "steps": len(self.steps),
            "skipped_confirmations": self.skipped_confirmations,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }


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
        plan_chat: ChatProvider | None = None,
        max_steps: int = 20,
        max_seconds: float = 360.0,
        audit: Any = None,
    ) -> None:
        self._chat = chat
        self._plan_chat = plan_chat or chat
        self._registry = registry
        self._policy = policy
        self._max_steps = max_steps
        self._max_seconds = max_seconds
        self._audit = audit

    # ----------------------------------------------------------------

    def run(
        self,
        goal: str,
        session_id: str | None = None,
        cancel_check: "Callable[[], bool] | None" = None,
        on_progress: "Callable[[str], None] | None" = None,
    ) -> TaskResult:
        goal = goal.strip()
        started = time.monotonic()
        last_progress = started

        result = TaskResult(goal=goal, status="exhausted", summary="")

        catalogue = "\n".join(
            f"- {t.get('name')}: {t.get('description', '')}"
            for t in self._registry.gemini_declarations()
        )

        history: list[str] = []

        self._log("task_start", {"goal": goal}, session_id)

        for i in range(1, self._max_steps + 1):
            if cancel_check is not None and cancel_check():
                result.status = "cancelled"
                result.summary = f"Stopped at your request after {i - 1} steps."
                break

            if time.monotonic() - started > self._max_seconds:
                result.status = "exhausted"
                result.summary = (
                    f"Ran out of time after {i - 1} steps on: {goal}"
                )
                break

            if on_progress is not None and i > 1 and (
                (i - 1) % 3 == 0 or time.monotonic() - last_progress > 45
            ):
                last_progress = time.monotonic()
                done_ok = sum(1 for s in result.steps if s.ok)
                on_progress(
                    f"Still on '{goal[:60]}' - {done_ok} steps done so far."
                )

            prompt = (
                f"{_SYSTEM}\n\nGOAL: {goal}\n\nTOOLS:\n{catalogue}\n\n"
                f"HISTORY:\n" + ("\n".join(history) or "(nothing yet)")
                + "\n\nYour next JSON:"
            )

            try:
                raw = self._chat.generate(prompt, temperature=0.2)
            except Exception as exc:  # noqa: BLE001
                result.status = "error"
                result.summary = f"Reasoning model failed: {exc}"
                break

            decision = _parse(raw)

            if decision is None:
                history.append(f"[system] Unparseable reply, retrying: {raw[:120]}")
                continue

            action = decision.get("action")

            if action == "done":
                result.status = "done"
                result.summary = str(decision.get("summary", "")).strip() or (
                    f"Completed: {goal}"
                )
                break

            if action == "give_up":
                result.status = "gave_up"
                result.summary = str(decision.get("reason", "")).strip() or (
                    f"Could not complete: {goal}"
                )
                break

            if action != "use_tool":
                history.append(f"[system] Unknown action {action!r}.")
                continue

            step = self._run_tool_step(i, decision, history, result, session_id)
            result.steps.append(step)

        result.elapsed_seconds = time.monotonic() - started

        if not result.summary:
            result.summary = (
                f"Stopped after {len(result.steps)} steps without finishing: "
                f"{goal}"
            )

        self._log("task_end", result.as_dict(), session_id)
        return result

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
