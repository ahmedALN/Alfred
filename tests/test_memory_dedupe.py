import tempfile
from pathlib import Path

import pytest

from src.memory.learner import MemoryLearner
from src.memory.store import MemoryStore
from src.tools.memory_tools import ForgetTool


class VecEmbedder:
    """Maps a few phrases to close/far vectors deterministically."""

    def embed(self, text: str):
        t = text.lower()
        if "dark mode" in t or "dark theme" in t:
            return [1.0, 0.0, 0.0]
        if "firewall" in t or "port 3389" in t:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


@pytest.fixture()
def learner():
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp) / "m.sqlite3")
        yield MemoryLearner(store, embedder=VecEmbedder())
        store.close()


def _add(store, content, emb, reinforced=1):
    fid = store.add_fact(content, embedding=emb)
    for _ in range(reinforced - 1):
        store.reinforce_fact(fid)
    return fid


def test_dedupe_merges_near_duplicates(learner):
    # add directly so remember()'s insert-time dedup doesn't pre-collapse them
    _add(learner._store, "User prefers dark mode.", [1.0, 0.0, 0.0])
    _add(learner._store, "The user likes dark theme everywhere.", [1.0, 0.0, 0.0])
    _add(learner._store, "Firewall blocks port 3389.", [0.0, 1.0, 0.0])

    merged = learner.dedupe(threshold=0.9)
    facts = learner._store.all_facts()

    assert merged == 1
    contents = " ".join(f.content for f in facts).lower()
    assert "firewall" in contents
    assert sum(1 for f in facts if "dark" in f.content.lower()) == 1


def test_dedupe_sums_reinforcement(learner):
    _add(learner._store, "User prefers dark mode.", [1.0, 0.0, 0.0], reinforced=2)
    _add(learner._store, "The user likes dark theme everywhere.", [1.0, 0.0, 0.0])

    learner.dedupe(threshold=0.9)
    survivor = learner._store.all_facts()[0]
    assert survivor.times_reinforced >= 3


def test_forget_tool_two_step(learner):
    learner.remember("User's router is a Netgear R7000.")
    tool = ForgetTool(learner)

    first = tool.execute({"query": "router"})
    assert first["status"] == "needs_confirmation"

    second = tool.execute({"query": "router", "_confirmed": True})
    assert second["status"] == "success"
    assert learner._store.all_facts() == []


def test_forget_tool_no_match(learner):
    out = ForgetTool(learner).execute({"query": "something not stored"})
    assert out["status"] == "not_found"
