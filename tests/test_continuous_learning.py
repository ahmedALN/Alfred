import asyncio
import os

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from src.ai.gemini import AlfredLiveSession  # noqa: E402
from src.tools.registry import ToolRegistry  # noqa: E402


class FakeStore:
    def __init__(self, turns):
        self._turns = turns

    def session_turns(self, _sid):
        return list(self._turns)


class FakeLearner:
    def __init__(self):
        self.distilled = []
        self.deduped = 0

    def distill_session(self, transcript):
        self.distilled.append(list(transcript))
        return 2

    def dedupe(self):
        self.deduped += 1
        return 0


def _session(store, learner):
    s = AlfredLiveSession(ToolRegistry(), store=store, learner=learner)
    s._distill_every_turns = 3
    return s


def test_incremental_distillation_uses_recent_tail():
    turns = [{"role": "user", "text": f"m{i}"} for i in range(20)]
    learner = FakeLearner()
    s = _session(FakeStore(turns), learner)

    asyncio.run(s._distill_incremental())

    assert len(learner.distilled) == 1
    assert len(learner.distilled[0]) == 6  # 2 * _distill_every_turns
    assert learner.deduped == 1


def test_incremental_distillation_noop_on_short_history():
    learner = FakeLearner()
    s = _session(FakeStore([{"role": "user", "text": "hi"}]), learner)
    asyncio.run(s._distill_incremental())
    assert learner.distilled == []


def test_situation_block_feeds_system_instruction():
    s = AlfredLiveSession(ToolRegistry(), situation_fn=lambda: "foreground app x")
    instr = s._system_instruction()
    assert "foreground app x" in instr
