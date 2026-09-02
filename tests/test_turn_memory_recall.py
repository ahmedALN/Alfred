import asyncio
import os

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from src.ai.gemini import AlfredLiveSession
from src.tools.registry import ToolRegistry


class Fact:
    def __init__(self, fid, content):
        self.id = fid
        self.content = content


class FakeLearner:
    def __init__(self, core, relevant):
        self._core = core
        self._relevant = relevant
        self.recall_queries = []

    def core_fact_ids(self, max_facts=6):
        return set(self._core)

    def recall(self, query, top_k=5):
        self.recall_queries.append(query)
        return list(self._relevant)


def _session(learner):
    s = AlfredLiveSession(ToolRegistry(), learner=learner)
    s.session = object()  # pretend connected
    s._injected = []

    async def fake_inject(text):
        s._injected.append(text)

    s.inject_system_prompt = fake_inject
    return s


def test_surfaces_relevant_non_core_fact():
    learner = FakeLearner(
        core=[1, 2],
        relevant=[Fact(2, "core fact"), Fact(9, "User's router is a Netgear R7000.")],
    )
    s = _session(learner)

    asyncio.run(s._surface_relevant_memory("how do I forward a port on my router"))

    assert len(s._injected) == 1
    assert "Netgear R7000" in s._injected[0]
    assert "core fact" not in s._injected[0]  # already in system prompt
    assert 9 in s._surfaced_fact_ids


def test_ignores_short_utterances():
    learner = FakeLearner(core=[], relevant=[Fact(1, "x")])
    s = _session(learner)
    asyncio.run(s._surface_relevant_memory("thanks"))
    assert s._injected == []


def test_does_not_resurface_same_fact():
    learner = FakeLearner(core=[], relevant=[Fact(5, "User prefers PowerShell 7.")])
    s = _session(learner)

    asyncio.run(s._surface_relevant_memory("which shell should you use for scripts"))
    assert len(s._injected) == 1

    s._last_memory_surface = 0.0  # bypass the rate-limit gap
    asyncio.run(s._surface_relevant_memory("remind me about my shell preference"))
    assert len(s._injected) == 1  # still 1, already surfaced


def test_rate_limited_within_window():
    learner = FakeLearner(
        core=[],
        relevant=[Fact(1, "fact one"), Fact(2, "fact two")],
    )
    s = _session(learner)

    asyncio.run(s._surface_relevant_memory("tell me something about fact one please"))
    # second call immediately: rate-limited, even though fact two is new
    asyncio.run(s._surface_relevant_memory("now tell me about fact two as well"))
    assert len(s._injected) == 1
