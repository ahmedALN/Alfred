import asyncio

from src.brain.agent import TaskResult
from src.brain.tasks import TaskQueue, _announce


class FakeAgent:
    def __init__(self, result: TaskResult):
        self._result = result
        self.calls = []

    def run(self, goal, session_id=None):
        self.calls.append(goal)
        return self._result


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
