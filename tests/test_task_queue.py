import asyncio

from src.brain.agent import Step, TaskResult
from src.brain.tasks import TaskQueue, _announce


class FakeAgent:
    def __init__(self, result: TaskResult, replay_result: TaskResult = None):
        self._result = result
        self._replay_result = replay_result
        self.calls = []
        self.replays = []

    def run(self, goal, session_id=None, cancel_check=None, on_progress=None,
            *, source="brain", ask_user=None):
        self.calls.append(goal)
        return self._result

    def replay(self, skill, request, session_id=None, cancel_check=None,
               on_progress=None, *, source="voice", ask_user=None):
        self.replays.append((skill["id"], request))
        return self._replay_result or self._result


class FakeSkills:
    def __init__(self, skill=None):
        self._skill = skill
        self.rewarded = []
        self.penalized = []
        self.learned = []

    def match(self, goal):
        return self._skill

    def reward(self, skill_id):
        self.rewarded.append(skill_id)

    def penalize(self, skill_id):
        self.penalized.append(skill_id)

    def distill(self, goal, trace, verify=""):
        skill = {"id": "new", "name": "learned", "steps": trace,
                 "tier": "ordinary", "danger_note": ""}
        return skill

    def needs_confirmation(self, skill):
        return False

    def save(self, skill):
        self.learned.append(skill)


def _run_worker(q, agent, tid, timeout=50):
    async def scenario():
        async def speak(_text):
            pass

        worker = asyncio.create_task(q.run(agent, speak, lambda: "sess"))
        for _ in range(timeout):
            await asyncio.sleep(0.01)
            rec = q.record(tid)
            if rec and rec.status not in ("queued", "running"):
                break
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        return q.record(tid)

    return asyncio.run(scenario())


def test_matching_skill_is_replayed_not_planned():
    q = TaskQueue()
    skills = FakeSkills(skill={"id": "s1"})
    q.attach_skills(skills)
    agent = FakeAgent(
        TaskResult(goal="g", status="done", summary="planned"),
        replay_result=TaskResult(goal="g", status="done", summary="replayed"),
    )
    tid = q.submit("play drake on spotify", source="voice")
    rec = _run_worker(q, agent, tid)

    assert rec.summary == "replayed"
    assert agent.replays == [("s1", "play drake on spotify")]
    assert agent.calls == []           # planner never ran
    assert skills.rewarded == ["s1"]


def test_failed_replay_falls_back_to_planning():
    q = TaskQueue()
    skills = FakeSkills(skill={"id": "s1"})
    q.attach_skills(skills)
    agent = FakeAgent(
        TaskResult(goal="g", status="done", summary="planned"),
        replay_result=TaskResult(goal="g", status="failed", summary="nope"),
    )
    tid = q.submit("play drake on spotify", source="voice")
    rec = _run_worker(q, agent, tid)

    assert rec.summary == "planned"
    assert skills.penalized == ["s1"]
    assert agent.calls == ["play drake on spotify"]


def test_verified_voice_task_is_distilled_into_a_skill():
    q = TaskQueue()
    skills = FakeSkills(skill=None)
    q.attach_skills(skills)
    result = TaskResult(goal="g", status="done", summary="done")
    result.steps.append(
        Step(1, "", "open_app", {"name": "spotify"}, "auto",
             {"status": "ok"}, True)
    )
    agent = FakeAgent(result)
    tid = q.submit("open spotify", source="voice")
    _run_worker(q, agent, tid)

    assert skills.learned and skills.learned[0]["name"] == "learned"


def test_brain_task_is_not_distilled():
    q = TaskQueue()
    skills = FakeSkills(skill=None)
    q.attach_skills(skills)
    agent = FakeAgent(TaskResult(goal="g", status="done", summary="done"))
    tid = q.submit("tidy things", source="brain")
    _run_worker(q, agent, tid)

    assert skills.learned == []


def test_submit_creates_queued_record():
    q = TaskQueue()
    tid = q.submit("do a thing")
    rec = q.record(tid)
    assert rec is not None
    assert rec.status == "queued"
    assert rec.goal == "do a thing"


def test_worker_runs_job_and_announces():
    async def scenario():
        q = TaskQueue()
        spoken = []

        async def speak(text):
            spoken.append(text)

        agent = FakeAgent(
            TaskResult(goal="tidy up", status="done", summary="tidied 3 files")
        )

        worker = asyncio.create_task(q.run(agent, speak, lambda: "sess"))

        tid = q.submit("tidy up")

        for _ in range(50):
            await asyncio.sleep(0.01)
            if q.record(tid).status == "done":
                break

        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

        return q.record(tid), spoken, agent.calls

    record, spoken, calls = asyncio.run(scenario())

    assert record.status == "done"
    assert record.summary == "tidied 3 files"
    assert calls == ["tidy up"]
    assert spoken and "tidied 3 files" in spoken[0]


def test_announce_mentions_skipped_confirmations():
    result = TaskResult(
        goal="secure the box",
        status="done",
        summary="closed 2 ports",
        skipped_confirmations=["powershell (disable SMBv1)"],
    )
    msg = _announce(result)
    assert "closed 2 ports" in msg
    assert "disable SMBv1" in msg


def test_recent_is_bounded():
    q = TaskQueue(max_history=3)
    ids = [q.submit(f"job {i}") for i in range(5)]
    recent = q.recent(limit=10)
    assert len(recent) == 3
    assert q.record(ids[0]) is None
    assert q.record(ids[-1]) is not None


# ------------------------------------------- asked from another thread


def test_a_task_submitted_from_another_thread_actually_starts():
    """A phone message arrives on the messaging library's own callback
    thread, not on the event loop. put_nowait from there does enqueue
    the job - it just never wakes the loop waiting on it, and the job
    sits for ever. From the outside that is Alfred saying "On it." and
    then nothing at all."""
    import threading

    queue = TaskQueue()
    started = asyncio.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        queue._loop = loop

        async def worker():
            await queue._queue.get()
            started.set()

        job = asyncio.create_task(worker())

        # Exactly how a WhatsApp message reaches it: off-loop, while the
        # loop is parked awaiting the queue.
        thread = threading.Thread(
            target=lambda: queue.submit("open notepad", source="voice")
        )
        thread.start()
        thread.join(2)

        try:
            await asyncio.wait_for(started.wait(), timeout=2)
        finally:
            job.cancel()

        return True

    assert asyncio.run(scenario()) is True


def test_a_task_submitted_on_the_loop_still_works():
    queue = TaskQueue()

    async def scenario():
        queue._loop = asyncio.get_running_loop()
        queue.submit("say hello")
        return await asyncio.wait_for(queue._queue.get(), timeout=1)

    assert asyncio.run(scenario())


def test_submitting_before_the_worker_runs_is_not_lost():
    """Restored jobs are enqueued at startup, before run() has set the
    loop."""
    queue = TaskQueue()
    task_id = queue.submit("something from last time")

    assert queue._queue.get_nowait() == task_id
