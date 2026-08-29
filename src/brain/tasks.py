from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.brain.agent import TaskAgent, TaskResult

SpeakFn = Callable[[str], Awaitable[None]]


@dataclass
class TaskRecord:
    id: str
    goal: str
    status: str = "queued"  # queued | running | done | gave_up | error | exhausted
    summary: str = ""
    source: str = "voice"  # "voice" (user asked) | "brain" (proactive)
    skipped_confirmations: list[str] = field(default_factory=list)


class TaskQueue:
    """
    Background job queue for longer autonomous work the user delegates
    ("sort my downloads", "audit my firewall and tighten it").

    One worker runs jobs serially so Alfred never fights itself over the
    mouse/keyboard. Results are announced through the live session.
    """

    def __init__(self, max_history: int = 50, store: Any = None) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._records: dict[str, TaskRecord] = {}
        self._order: list[str] = []
        self._max_history = max_history
        self._store = store
        self._gate = asyncio.Event()
        self._gate.set()  # not paused
        self._cancel = threading.Event()

        # Mid-task "go ahead, sir?" confirmations.
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._awaiting_confirm = False
        self._speak_fn: SpeakFn | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- interrupt --------------------------------------------------

    def cancel_current(self) -> None:
        """Ask the running task agent to stop between steps."""
        self._cancel.set()
        if self._awaiting_confirm:  # unblock a pending question as "no"
            self._confirm_result = False
            self._confirm_event.set()

    def _cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def answer_pending(self, text: str) -> bool:
        """
        Called from the voice loop on a user reply. If the task agent is
        blocked on a "go ahead, sir?" question, resolve it. Returns True
        if it consumed the reply.
        """
        if not self._awaiting_confirm:
            return False

        from src.brain.orchestrator import _AFFIRMATIVE, _NEGATIVE

        if _AFFIRMATIVE.search(text):
            self._confirm_result = True
        elif _NEGATIVE.search(text):
            self._confirm_result = False
        else:
            return False  # not a yes/no - let other handlers see it

        self._confirm_event.set()
        return True

    def _ask_user(self, question: str) -> bool:
        """Blocking (called from the agent's worker thread): speak the
        question, wait ~90s for a yes/no from the voice loop."""
        if self._speak_fn is None or self._loop is None:
            return False

        self._confirm_event.clear()
        self._confirm_result = False
        self._awaiting_confirm = True
        try:
            asyncio.run_coroutine_threadsafe(
                self._speak_fn(f"(System: proactive) {question}"), self._loop
            )
            self._confirm_event.wait(timeout=95.0)
            return self._confirm_result
        finally:
            self._awaiting_confirm = False

    # ----------------------------------------------------------------

    def pause(self) -> None:
        """Worker finishes its current job, then waits before the next."""
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    @property
    def is_paused(self) -> bool:
        return not self._gate.is_set()

    # ----------------------------------------------------------------

    def submit(
        self, goal: str, task_id: str | None = None, source: str = "voice"
    ) -> str:
        goal = goal.strip()
        task_id = task_id or uuid.uuid4().hex[:8]

        self._records[task_id] = TaskRecord(id=task_id, goal=goal, source=source)
        self._order.append(task_id)
        self._trim()

        if self._store is not None:
            try:
                self._store.add(task_id, goal, source)
            except Exception as exc:  # noqa: BLE001
                print(f"[Tasks] persist failed: {exc}")

        self._queue.put_nowait(task_id)
        return task_id

    def restore(self) -> int:
        """Re-enqueue tasks left unfinished by a previous run."""
        if self._store is None:
            return 0
        try:
            pending = self._store.unfinished()
        except Exception as exc:  # noqa: BLE001
            print(f"[Tasks] restore failed: {exc}")
            return 0
        for row in pending:
            self.submit(
                row["goal"], task_id=row["id"],
                source=row.get("source", "voice"),
            )
        return len(pending)

    def record(self, task_id: str) -> TaskRecord | None:
        return self._records.get(task_id)

    def recent(self, limit: int = 10) -> list[TaskRecord]:
        return [self._records[i] for i in self._order[-limit:]]

    def _trim(self) -> None:
        while len(self._order) > self._max_history:
            old = self._order.pop(0)
            self._records.pop(old, None)

    # ----------------------------------------------------------------

    async def run(
        self,
        agent: TaskAgent,
        speak: SpeakFn,
        get_session_id: Callable[[], str | None],
    ) -> None:
        """Worker loop. Launch as a background task alongside the session."""

        loop = asyncio.get_running_loop()
        self._loop = loop
        self._speak_fn = speak

        def _progress(text: str) -> None:
            asyncio.run_coroutine_threadsafe(
                _safe_speak(speak, f"(System: proactive) {text}"), loop
            )

        while True:
            await self._gate.wait()  # blocks while paused (game mode)
            task_id = await self._queue.get()
            record = self._records.get(task_id)

            if record is None:
                continue

            record.status = "running"
            self._cancel.clear()
            self._persist(task_id, "running")

            try:
                result: TaskResult = await asyncio.to_thread(
                    lambda r=record: agent.run(
                        r.goal, get_session_id(),
                        self._cancel_requested, _progress,
                        source=r.source, ask_user=self._ask_user,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                record.status = "error"
                record.summary = f"Task crashed: {exc}"
                self._persist(task_id, "error", record.summary)
                await _safe_speak(
                    speak, f"(System: proactive) That task failed: {exc}"
                )
                continue

            record.status = result.status
            record.summary = result.summary
            record.skipped_confirmations = result.skipped_confirmations
            self._persist(task_id, result.status, result.summary)

            await _safe_speak(speak, _announce(result))

    def _persist(self, task_id: str, status: str, summary: str = "") -> None:
        if self._store is not None:
            try:
                self._store.set_status(task_id, status, summary)
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------------------------------------------


async def _safe_speak(speak: SpeakFn, text: str) -> None:
    try:
        await speak(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[Tasks] could not announce result: {exc}")


def _announce(result: TaskResult) -> str:
    lead = {
        "done": "Finished",
        "gave_up": "I had to stop",
        "exhausted": "I ran out of steps",
        "error": "Something went wrong",
        "cancelled": "Stopped",
    }.get(result.status, "Update")

    msg = f"(System: proactive) {lead} on '{result.goal}': {result.summary}"

    if result.skipped_confirmations:
        msg += (
            " I left these for you to approve: "
            + "; ".join(result.skipped_confirmations)
            + "."
        )

    return msg
