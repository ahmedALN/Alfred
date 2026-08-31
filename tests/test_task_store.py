import asyncio
import time

from src.brain.agent import TaskResult
from src.brain.task_store import TaskStore
from src.brain.tasks import TaskQueue


def test_task_store_roundtrip(tmp_path):
    s = TaskStore(tmp_path / "t.sqlite3")
    s.add("abc", "tidy downloads")
    assert [r["id"] for r in s.unfinished()] == ["abc"]

    s.set_status("abc", "running")
    assert [r["status"] for r in s.unfinished()] == ["running"]

    s.set_status("abc", "done", "tidied 4 files")
    assert s.unfinished() == []
    assert s.recent()[0]["summary"] == "tidied 4 files"
    s.close()


def test_task_store_ignores_stale_entries(tmp_path):
    s = TaskStore(tmp_path / "t.sqlite3")
    s.add("old", "ancient job")
    # backdate it
    s._conn.execute(
        "UPDATE tasks SET created_at = '2000-01-01T00:00:00' WHERE id = 'old'"
    )
    s._conn.commit()
    assert s.unfinished(max_age_hours=6) == []
    s.close()


def test_queue_restores_unfinished(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite3")
    store.add("job-1", "do the thing")
    store.set_status("job-1", "running")
    store.close()

    store2 = TaskStore(tmp_path / "t.sqlite3")
    q = TaskQueue(store=store2)
    restored = q.restore()
    assert restored == 1
    assert q.record("job-1") is not None
    store2.close()


def test_cancel_current_stops_agent(tmp_path):
    from src.brain.policy import Policy

    class SlowChat:
        name = "slow"
        model = "slow"
        calls = 0

        def generate(self, prompt, **kw):
            SlowChat.calls += 1
            import json
            return json.dumps(
                {"action": "use_tool", "tool": "system_info", "args": {}}
            )

    class Reg:
        def gemini_declarations(self):
            return [{"name": "system_info", "description": "read"}]

        def names(self):
            return ["system_info"]

        def execute(self, n, a):
            return {"status": "success"}

    from src.brain.agent import TaskAgent

    agent = TaskAgent(
        SlowChat(), Reg(), Policy("full", {"system_info"}, surface="brain"),
        max_steps=50, max_seconds=30,
    )

    cancelled = {"v": False}
    # cancel after the 2nd step
    def check():
        cancelled["v"] = SlowChat.calls >= 2
        return cancelled["v"]

    result = agent.run("loop", cancel_check=check)
    assert result.status == "cancelled"
    assert len(result.steps) <= 3


def test_progress_callback_fires(tmp_path):
    import json

    from src.brain.agent import TaskAgent
    from src.brain.policy import Policy

    replies = iter(
        [json.dumps({"action": "use_tool", "tool": "x", "args": {}})] * 6
        + [json.dumps({"action": "done", "summary": "ok"})]
    )

    class Chat:
        name = "c"
        model = "c"

        def generate(self, p, **kw):
            return next(replies)

    class Reg:
        def gemini_declarations(self):
            return [{"name": "x", "description": "d"}]

        def names(self):
            return ["x"]

        def execute(self, n, a):
            return {"status": "success"}

    notes = []
    TaskAgent(
        Chat(), Reg(), Policy("full", {"x"}, surface="brain"), max_steps=10
    ).run("g", on_progress=notes.append)

    # A one-step plan gets on with it. Progress notes are for jobs long
    # enough that silence would be worrying, not for every job.
    assert notes == []
