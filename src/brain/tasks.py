from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src.brain.agent import TaskAgent, TaskResult
from src.brain.isolation import strip_isolation_phrase, wants_isolation
from src.brain.skills import SkillLibrary

SpeakFn = Callable[[str], Awaitable[None]]


def _current_loop() -> asyncio.AbstractEventLoop | None:
    """The loop running on this thread, if this thread is running one."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


@dataclass
class TaskRecord:
    id: str
    goal: str
    status: str = "queued"
    # queued | running | done | partial | failed | uncertain | gave_up
    # | exhausted | cancelled | error
    summary: str = ""
    source: str = "voice"  # "voice" (user asked) | "brain" (proactive)
    isolated: bool = False  # run on Alfred's own desktop, not the user's
    skipped_confirmations: list[str] = field(default_factory=list)


class TaskQueue:
    """
    Background job queue for longer autonomous work the user delegates
    ("sort my downloads", "audit my firewall and tighten it").

    One worker runs jobs serially so Alfred never fights itself over the
    mouse/keyboard. Results are announced through the live session.
    """

    def __init__(
        self,
        max_history: int = 50,
        store: Any = None,
        skills: SkillLibrary | None = None,
        episodes: Any = None,
        app_memory: Any = None,
    ) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._records: dict[str, TaskRecord] = {}
        self._order: list[str] = []
        self._max_history = max_history
        self._store = store
        self._skills: SkillLibrary | None = skills
        self._episodes = episodes
        self._app_memory = app_memory
        self._isolated_desktop: Any = None
        self._router: Any = None
        self._gate = asyncio.Event()
        self._gate.set()  # not paused
        self._cancel = threading.Event()

        # Mid-task "go ahead, sir?" confirmations.
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._awaiting_confirm = False
        self._speak_fn: SpeakFn | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_skills(self, skills: SkillLibrary) -> None:
        self._skills = skills

    def attach_episodes(self, episodes: Any) -> None:
        self._episodes = episodes

    def attach_app_memory(self, app_memory: Any) -> None:
        self._app_memory = app_memory

    def attach_isolated_desktop(self, desktop: Any, router: Any = None) -> None:
        self._isolated_desktop = desktop
        self._router = router

    def _record_episode(self, record: TaskRecord, result: TaskResult) -> None:
        if self._episodes is None:
            return
        try:
            self._episodes.record(
                "task",
                f"{'you asked' if record.source == 'voice' else 'on my own'}: "
                f"{record.goal}",
                outcome=result.status,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Tasks] episode log failed: {exc}")

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
        self, goal: str, task_id: str | None = None, source: str = "voice",
        isolated: bool | None = None,
    ) -> str:
        goal = goal.strip()
        task_id = task_id or uuid.uuid4().hex[:8]

        # "without disturbing me, do X" -> run it on Alfred's own desktop.
        if isolated is None:
            isolated = wants_isolation(goal)
        if isolated:
            goal = strip_isolation_phrase(goal)

        self._records[task_id] = TaskRecord(
            id=task_id, goal=goal, source=source, isolated=isolated,
        )
        self._order.append(task_id)
        self._trim()

        if self._store is not None:
            try:
                self._store.add(task_id, goal, source)
            except Exception as exc:  # noqa: BLE001
                print(f"[Tasks] persist failed: {exc}")

        self._enqueue(task_id)
        return task_id

    def _enqueue(self, task_id: str) -> None:
        """Wake the worker, whichever thread we are on.

        Voice arrives on the event loop's own thread. A phone message
        arrives on a callback thread belonging to the messaging library,
        and put_nowait from the wrong thread does put the item in the
        queue - it just never wakes the loop that is waiting on it. The
        job then sits there indefinitely, which from the outside looks
        exactly like Alfred saying "On it." and then nothing.
        """
        loop = self._loop
        if loop is None or _current_loop() is loop:
            self._queue.put_nowait(task_id)
            return

        try:
            loop.call_soon_threadsafe(self._queue.put_nowait, task_id)
        except RuntimeError:            # loop already closed; shutting down
            self._queue.put_nowait(task_id)

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

            # "without disturbing me" - bring up Alfred's own desktop and
            # work there. If it cannot be brought up we say so and carry
            # on here, which is worse but not a failure.
            isolated_ready = False
            if record.isolated and self._isolated_desktop is not None:
                sid = await asyncio.to_thread(self._isolated_desktop.ensure)
                isolated_ready = sid is not None
                if isolated_ready:
                    # Everything that drives mouse/keyboard/capture now
                    # acts on Alfred's desktop instead of the user's.
                    if self._router is not None:
                        self._router.use_isolated()
                    _progress(
                        f"Working on my own desktop (session {sid}) so this "
                        "stays out of your way."
                    )
                else:
                    await _safe_speak(
                        speak,
                        "(System: proactive) I couldn't open my own desktop, "
                        "so this will happen on your screen.",
                    )

            def _plan_run(r=record):
                return agent.run(
                    r.goal, get_session_id(),
                    self._cancel_requested, _progress,
                    source=r.source, ask_user=self._ask_user,
                )

            try:
                skill = None
                if self._skills is not None and record.source == "voice":
                    skill = await asyncio.to_thread(
                        self._skills.match, record.goal
                    )

                if skill is not None:
                    result: TaskResult = await asyncio.to_thread(
                        lambda s=skill, r=record: agent.replay(
                            s, r.goal, get_session_id(),
                            self._cancel_requested, _progress,
                            source=r.source, ask_user=self._ask_user,
                        )
                    )
                    if result.status in ("done", "partial"):
                        self._skills.reward(skill["id"])
                    else:
                        self._skills.penalize(skill["id"])
                        _progress(
                            "That saved routine didn't work - doing it the "
                            "long way."
                        )
                        result = await asyncio.to_thread(_plan_run)
                        await asyncio.to_thread(
                            self._maybe_learn,
                            record.goal, result, record.source,
                        )
                else:
                    result = await asyncio.to_thread(_plan_run)
                    await asyncio.to_thread(
                        self._maybe_learn,
                        record.goal, result, record.source,
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
            self._record_episode(record, result)
            self._learn_app_knowledge(result)

            # Point the tools back at the user's desktop before anything
            # else runs, whatever happened above.
            if self._router is not None:
                self._router.use_users_desktop()

            # Alfred tidies up after itself: close what it opened in the
            # isolated session so the next task starts from a clean desk.
            if isolated_ready and self._isolated_desktop is not None:
                try:
                    tidy = await asyncio.to_thread(
                        self._isolated_desktop.cleanup
                    )
                    closed = len(tidy.get("closed", []))
                    if closed:
                        print(f"[Tasks] cleaned up {closed} app(s).")
                except Exception as exc:  # noqa: BLE001
                    print(f"[Tasks] isolated cleanup failed: {exc}")

            await _safe_speak(speak, _announce(result))

            # Post-mortem: one cheap call that may bank a lesson for next
            # time. Fire-and-forget so it never delays the next job.
            if hasattr(agent, "reflect"):
                try:
                    line = await asyncio.to_thread(agent.reflect, result)
                    if line and line.lower() != "none":
                        print(f"[Task] reflection: {line}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[Task] reflection error: {exc}")

            # And separately: anything Alfred has now run into more than
            # once AND been seen to get past becomes a standing lesson,
            # so the same wall is not hit a third time.
            if hasattr(agent, "learn_workarounds"):
                try:
                    for lesson in await asyncio.to_thread(
                        agent.learn_workarounds
                    ):
                        print(f"[Task] learned a way round: {lesson[:150]}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[Task] could not learn a workaround: {exc}")

    def _learn_app_knowledge(self, result: TaskResult) -> None:
        """Record the window titles and control names that actually worked,
        so the next task in the same app skips the exploration.

        Runs for partial tasks too - a step that worked teaches something
        even when a later step didn't.
        """
        if self._app_memory is None:
            return
        try:
            n = self._app_memory.learn_from_steps(result.steps)
            if n:
                print(f"[Apps] learned {n} thing(s) about the apps involved.")
        except Exception as exc:  # noqa: BLE001
            print(f"[Apps] could not record app knowledge: {exc}")

    def _maybe_learn(
        self, goal: str, result: TaskResult, source: str
    ) -> None:
        """After a fully verified user-asked task, distil the tool sequence
        into a replayable skill. Dangerous routines are confirmed first."""
        if (
            self._skills is None
            or source != "voice"
            or result.status != "done"
        ):
            return

        trace = result.tool_trace()
        if not trace:
            return

        try:
            skill = self._skills.distill(
                goal, trace,
                verify="; ".join(result.verified) or goal,
            )
            if skill is None:
                return
            if self._skills.needs_confirmation(skill):
                note = skill.get("danger_note") or "a risky step"
                if not self._ask_user(
                    f"Sir, this routine includes {note} - "
                    "save it as a reusable skill?"
                ):
                    return
            self._skills.save(skill)
            print(
                f"[Skills] learned '{skill['name']}' "
                f"({len(skill['steps'])} steps, {skill['tier']})."
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Skills] could not distil a skill: {exc}")

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
    # result.summary is already an honest, self-contained sentence built by
    # TaskAgent._finalize ("Partly done on '...'. Confirmed: ... Couldn't
    # confirm: ... Left for you: ...").
    msg = f"(System: proactive) {result.summary}".rstrip()

    if result.skipped_confirmations and "Left for you" not in result.summary:
        msg += (
            " I left these for you to approve: "
            + "; ".join(result.skipped_confirmations)
            + "."
        )

    return msg
