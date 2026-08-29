from src.context import build_situation
from src.memory.episodes import EpisodeStore


class FakeRec:
    def __init__(self, goal, status):
        self.goal = goal
        self.status = status


class FakeQueue:
    def __init__(self, recs):
        self._recs = recs

    def recent(self, limit=8):
        return self._recs[:limit]


class FakeFact:
    def __init__(self, content):
        self.content = content


class FakeLearner:
    def __init__(self, goal=None, facts=()):
        self._goal = goal
        self._facts = list(facts)

    def active_goal(self):
        return self._goal

    def recent_facts(self, limit=4):
        return self._facts[:limit]


def test_situation_reports_foreground_and_activity():
    text = build_situation(
        foreground=lambda: "chrome.exe",
        idle=lambda: 2.0,
    )
    assert "foreground app chrome.exe" in text
    assert "user active" in text


def test_situation_reports_idle_minutes():
    text = build_situation(foreground=lambda: None, idle=lambda: 900.0)
    assert "user idle 15m" in text


def test_situation_includes_goal_tasks_and_learnings():
    q = FakeQueue([
        FakeRec("tidy downloads", "running"),
        FakeRec("audit firewall", "queued"),
    ])
    learner = FakeLearner(
        goal="set up a python dev environment",
        facts=[FakeFact("user prefers dark mode"), FakeFact("GPU is an RTX 4060")],
    )
    text = build_situation(
        task_queue=q, learner=learner,
        foreground=lambda: "code.exe", idle=lambda: 1.0,
    )
    assert "set up a python dev environment" in text
    assert "Working on: 'tidy downloads' (1 queued)." in text
    assert "user prefers dark mode" in text


def test_situation_includes_recent_episodes(tmp_path):
    eps = EpisodeStore(tmp_path / "ep.sqlite3")
    eps.record("task", "moved report files", outcome="done")
    text = build_situation(
        episodes=eps, foreground=lambda: None, idle=lambda: 1.0,
    )
    assert "moved report files (done)" in text
    eps.close()


def test_situation_is_robust_to_broken_inputs():
    class Boom:
        def recent(self, limit=8):
            raise RuntimeError("nope")

        in_game_mode = property(lambda self: 1 / 0)

    text = build_situation(
        task_queue=Boom(), resource_mode=Boom(),
        foreground=lambda: (_ for _ in ()).throw(OSError()),
        idle=lambda: 1.0,
    )
    assert isinstance(text, str)  # no exception


def test_situation_truncates_to_max_len():
    learner = FakeLearner(facts=[
        type("F", (), {"content": "x" * 200})() for _ in range(10)
    ])
    text = build_situation(
        learner=learner, max_len=120,
        foreground=lambda: None, idle=lambda: 1.0,
    )
    assert len(text) <= 120
