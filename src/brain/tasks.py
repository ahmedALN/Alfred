from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from src.brain.agent import TaskAgent, TaskResult

SpeakFn = Callable[[str], Awaitable[None]]


@dataclass
class TaskRecord:
    id: str
    goal: str
    status: str = "queued"  # queued | running | done | gave_up | error | exhausted
    summary: str = ""
    skipped_confirmations: list[str] = field(default_factory=list)


class TaskQueue:
    """
    Background job queue for longer autonomous work the user delegates
    ("sort my downloads", "audit my firewall and tighten it").

    One worker runs jobs serially so Alfred never fights itself over the
    mouse/keyboard. Results are announced through the live session.
    """

    def __init__(self, max_history: int = 50) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._records: dict[str, TaskRecord] = {}
        self._order: list[str] = []
        self._max_history = max_history
        self._gate = asyncio.Event()
        self._gate.set()  # not paused

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

    def submit(self, goal: str) -> str:
        goal = goal.strip()
        task_id = uuid.uuid4().hex[:8]

        self._records[task_id] = TaskRecord(id=task_id, goal=goal)
        self._order.append(task_id)
        self._trim()

        self._queue.put_nowait(task_id)
        return task_id

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

        while True:
            await self._gate.wait()  # blocks while paused (game mode)
            task_id = await self._queue.get()
            record = self._records.get(task_id)

            if record is None:
                continue

            record.status = "running"

            try:
                result: TaskResult = await asyncio.to_thread(
                    agent.run, record.goal, get_session_id()
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                record.status = "error"
                record.summary = f"Task crashed: {exc}"
                await _safe_speak(
                    speak, f"(System: proactive) That task failed: {exc}"
                )
                continue

            record.status = result.status
            record.summary = result.summary
            record.skipped_confirmations = result.skipped_confirmations

            await _safe_speak(speak, _announce(result))

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
    }.get(result.status, "Update")

    msg = f"(System: proactive) {lead} on '{result.goal}': {result.summary}"

    if result.skipped_confirmations:
        msg += (
            " I left these for you to approve: "
            + "; ".join(result.skipped_confirmations)
            + "."
        )

    return msg
