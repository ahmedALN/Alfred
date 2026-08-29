from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, time as clock_time
from typing import Awaitable, Callable

from src.brain.audit import AuditLog
from src.brain.deliberation import SUPPRESS_PREFIX, Deliberator
from src.brain.perception import Perception
from src.brain.policy import Policy
from src.brain.types import Decision, Proposal, ProposalKind, Verdict
from src.memory.learner import MemoryLearner
from src.tools.registry import ToolRegistry
from src.windows.system_probe import is_fullscreen_foreground

SpeakFn = Callable[[str], Awaitable[None]]

_AFFIRMATIVE = re.compile(
    r"\b(yes|yeah|yep|sure|ok|okay|do it|go ahead|please do|"
    r"go for it|sounds good|proceed)\b",
    re.I,
)
_NEGATIVE = re.compile(
    r"\b(no|nope|don'?t|do not|leave it|cancel|skip it|not now)\b", re.I
)
_DND_ON = re.compile(
    r"\b(do not disturb|don'?t disturb|leave me alone|be quiet|"
    r"stop talking|quiet mode|shut up)\b",
    re.I,
)
_DND_OFF = re.compile(
    r"\b(you can talk|talk to me again|disturb me|end quiet|"
    r"resume notifications|stop being quiet)\b",
    re.I,
)
_PAUSE = re.compile(
    r"\b(alfred[, ]+stop|pause the brain|stop the brain|"
    r"stop watching|stand down)\b",
    re.I,
)
_RESUME = re.compile(
    r"\b(resume the brain|start the brain|start watching|"
    r"wake up|carry on)\b",
    re.I,
)
_SUPPRESS = re.compile(
    r"\b(?:stop (?:telling|reminding|bugging|nagging) me about|"
    r"don'?t (?:tell|remind|bug|nag) me about|"
    r"stop mentioning|quit bugging me about)\s+(.+)",
    re.I,
)


@dataclass
class _Pending:
    decision: Decision
    created_at: float


class BrainLoop:
    """
    Alfred's background awareness loop.

    Runs alongside the voice session: senses the machine on a timer,
    asks the reasoner whether anything is worth doing, runs the policy
    gate, then either speaks, acts (and reports), or asks for
    confirmation. Every step is written to the audit log.
    """

    PENDING_TTL_SECONDS = 180.0

    def __init__(
        self,
        perception: Perception,
        deliberator: Deliberator,
        policy: Policy,
        registry: ToolRegistry,
        audit: AuditLog,
        learner: MemoryLearner,
        speak: SpeakFn,
        get_session_id: Callable[[], str | None],
        *,
        tick_seconds: float = 90.0,
        min_speak_gap_seconds: float = 600.0,
        quiet_hours: str | None = None,
        heartbeat_ticks: int = 0,
        monotonic: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], datetime] = datetime.now,
        fullscreen_probe: Callable[[], bool] = is_fullscreen_foreground,
    ) -> None:
        self._perception = perception
        self._deliberator = deliberator
        self._policy = policy
        self._registry = registry
        self._audit = audit
        self._learner = learner
        self._speak = speak
        self._get_session_id = get_session_id

        self._tick_seconds = tick_seconds
        self._min_speak_gap = min_speak_gap_seconds
        self._quiet_window = _parse_quiet_hours(quiet_hours)
        self._heartbeat_ticks = heartbeat_ticks

        self._monotonic = monotonic
        self._wallclock = wallclock
        self._fullscreen_probe = fullscreen_probe

        self._paused = False
        self._session_dnd = False
        self._last_spoke_at = 0.0
        self._pending: _Pending | None = None
        self._deferred_summaries: list[str] = []
        self._tick_count = 0

    # ================================================================
    # Public runtime
    # ================================================================

    @property
    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, value: bool) -> None:
        self._paused = bool(value)
        self._audit.record(
            "tick",
            {"note": f"brain {'paused' if value else 'resumed'} (tray)"},
        )

    async def run(self) -> None:
        # Small stagger so the brain's first tick doesn't collide with
        # the startup greeting.
        await asyncio.sleep(min(15.0, self._tick_seconds))

        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - loop must not die
                print(f"[Brain] tick failed: {exc}")
                self._audit.record("error", {"error": repr(exc)})

            await asyncio.sleep(self._tick_seconds)

    async def run_once(self) -> None:
        self._tick_count += 1

        if self._paused:
            return

        notables, observations = await asyncio.to_thread(
            self._perception.sense
        )

        session_id = self._get_session_id()

        self._audit.record(
            "tick",
            {
                "tick": self._tick_count,
                "observations": len(observations),
                "notables": [n.summary for n in notables],
            },
            session_id,
        )

        dnd = self._is_dnd()

        # Flush any summaries that were held back during DND.
        if not dnd and self._deferred_summaries:
            held = self._deferred_summaries
            self._deferred_summaries = []
            for summary in held:
                await self._say(f"(System: proactive) {summary}", force=True)

        heartbeat = (
            self._heartbeat_ticks > 0
            and self._tick_count % self._heartbeat_ticks == 0
        )

        if not notables and not heartbeat:
            return

        for notable in notables:
            self._audit.record(
                "notable",
                {
                    "source": notable.source,
                    "key": notable.key,
                    "severity": notable.severity,
                    "summary": notable.summary,
                },
                session_id,
            )

        proposals = await asyncio.to_thread(
            self._deliberator.deliberate, notables, session_id
        )

        for proposal in proposals:
            await self._handle_proposal(proposal, session_id, dnd)

    # ================================================================
    # Proposal handling
    # ================================================================

    async def _handle_proposal(
        self,
        proposal: Proposal,
        session_id: str | None,
        dnd: bool,
    ) -> None:
        decision = self._policy.evaluate(proposal)

        self._audit.record(
            "decision",
            {
                "verdict": decision.verdict.value,
                "reason": decision.reason,
                "kind": proposal.kind.value,
                "message": proposal.message,
                "tool": proposal.tool,
                "args": proposal.args,
                "urgency": proposal.urgency,
            },
            session_id,
        )

        if decision.verdict is Verdict.FORBID:
            self._audit.record(
                "blocked",
                {"message": proposal.message, "reason": decision.reason},
                session_id,
            )
            return

        if decision.verdict is Verdict.CONFIRM:
            self._pending = _Pending(decision, self._monotonic())
            question = _as_question(proposal)
            await self._say(f"(System: proactive) {question}", force=False)
            return

        # AUTO
        if proposal.kind is ProposalKind.SPEAK:
            await self._say(
                f"(System: proactive) {proposal.message}",
                force=proposal.urgency == "high",
            )
            return

        await self._execute(proposal, session_id, dnd)

    async def _execute(
        self,
        proposal: Proposal,
        session_id: str | None,
        dnd: bool,
    ) -> None:
        self._audit.record(
            "action",
            {"tool": proposal.tool, "args": proposal.args,
             "message": proposal.message},
            session_id,
        )

        try:
            result = await asyncio.to_thread(
                self._registry.execute, proposal.tool, proposal.args
            )
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "error": repr(exc)}

        ok = not (isinstance(result, dict) and result.get("status") == "error")

        self._audit.record(
            "action_result",
            {"tool": proposal.tool, "success": ok, "result": result},
            session_id,
        )

        summary = (
            proposal.message
            if ok
            else f"I tried to {proposal.message} but it failed."
        )

        if dnd:
            self._deferred_summaries.append(summary)
        else:
            await self._say(f"(System: proactive) {summary}", force=True)

    # ================================================================
    # User replies (called from the voice receive loop, in-loop thread)
    # ================================================================

    async def note_user_reply(self, text: str) -> None:
        text = (text or "").strip()

        if not text:
            return

        if _PAUSE.search(text):
            self._paused = True
            self._audit.record("tick", {"note": "brain paused by user"})
            return

        if _RESUME.search(text):
            self._paused = False
            self._audit.record("tick", {"note": "brain resumed by user"})
            return

        if _DND_ON.search(text):
            self._session_dnd = True
            return

        if _DND_OFF.search(text):
            self._session_dnd = False
            return

        suppress_match = _SUPPRESS.search(text)
        if suppress_match:
            topic = suppress_match.group(1).strip().rstrip(".!?")
            if topic:
                self._remember_suppression(topic)
            return

        await self._maybe_resolve_pending(text)

    def _remember_suppression(self, topic: str) -> None:
        try:
            self._learner.remember(
                content=f"{SUPPRESS_PREFIX} {topic}",
                category="correction",
                source="brain_suppression",
            )
            self._audit.record("tick", {"note": f"suppressed topic: {topic}"})
        except Exception as exc:  # noqa: BLE001
            print(f"[Brain] failed to store suppression: {exc}")

    async def _maybe_resolve_pending(self, text: str) -> None:
        pending = self._pending

        if pending is None:
            return

        if self._monotonic() - pending.created_at > self.PENDING_TTL_SECONDS:
            self._pending = None
            return

        if _NEGATIVE.search(text):
            self._pending = None
            self._audit.record(
                "decision",
                {"verdict": "declined_by_user",
                 "message": pending.decision.proposal.message},
            )
            return

        if _AFFIRMATIVE.search(text):
            self._pending = None
            proposal = pending.decision.proposal
            self._audit.record(
                "decision",
                {"verdict": "approved_by_user", "message": proposal.message},
            )
            if proposal.kind is ProposalKind.ACT:
                await self._execute(proposal, self._get_session_id(), False)
            else:
                await self._say(
                    f"(System: proactive) {proposal.message}", force=True
                )

    # ================================================================
    # Helpers
    # ================================================================

    def _is_dnd(self) -> bool:
        if self._session_dnd:
            return True

        if self._quiet_window and _in_window(
            self._wallclock().time(), self._quiet_window
        ):
            return True

        try:
            return self._fullscreen_probe()
        except Exception:  # noqa: BLE001
            return False

    async def _say(self, text: str, *, force: bool) -> None:
        now = self._monotonic()

        if not force and now - self._last_spoke_at < self._min_speak_gap:
            self._audit.record(
                "spoken",
                {"suppressed": True, "reason": "rate limited", "text": text},
            )
            return

        if self._is_dnd() and not force:
            self._audit.record(
                "spoken",
                {"suppressed": True, "reason": "dnd", "text": text},
            )
            return

        try:
            await self._speak(text)
            self._last_spoke_at = now
            self._audit.record("spoken", {"text": text})
        except Exception as exc:  # noqa: BLE001
            print(f"[Brain] speak failed: {exc}")


# ====================================================================
# Module helpers
# ====================================================================


def _as_question(proposal: Proposal) -> str:
    message = proposal.message.strip()

    if message.endswith("?"):
        return message

    if proposal.kind is ProposalKind.ACT:
        return f"{message} Want me to go ahead?"

    return message


def _parse_quiet_hours(
    value: str | None,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if not value:
        return None

    match = re.match(
        r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", value
    )

    if not match:
        print(f"[Brain] ignoring malformed ALFRED_BRAIN_QUIET_HOURS: {value!r}")
        return None

    h1, m1, h2, m2 = (int(g) for g in match.groups())

    if not (0 <= h1 < 24 and 0 <= h2 < 24 and 0 <= m1 < 60 and 0 <= m2 < 60):
        return None

    return (h1, m1), (h2, m2)


def _in_window(
    now: clock_time,
    window: tuple[tuple[int, int], tuple[int, int]],
) -> bool:
    (h1, m1), (h2, m2) = window

    start = h1 * 60 + m1
    end = h2 * 60 + m2
    current = now.hour * 60 + now.minute

    if start <= end:
        return start <= current < end

    # Window wraps past midnight.
    return current >= start or current < end
