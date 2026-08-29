import tempfile
from pathlib import Path

import pytest

from src.memory.learner import MemoryLearner
from src.memory.store import MemoryStore


class FakeEmbedder:
    """Deterministic fake embedder: same text -> same vector."""

    def embed(self, text: str) -> list[float]:
        text = text.strip().lower()

        # Crude but deterministic: bag-of-words presence vector over
        # a fixed vocabulary drawn from the test fixtures below.
        vocab = [
            "dark", "mode", "prefers", "user", "firewall",
            "blocks", "port", "3389", "rdp", "chrome", "browser",
        ]

        return [1.0 if word in text else 0.0 for word in vocab]


@pytest.fixture()
def learner() -> MemoryLearner:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_memory.sqlite3"
        store = MemoryStore(db_path)

        l = MemoryLearner(store=store, embedder=FakeEmbedder())  # type: ignore[arg-type]

        yield l
        store.close()


def test_remember_stores_new_fact(learner: MemoryLearner) -> None:
    result = learner.remember("User prefers dark mode.", category="preference")

    assert result["status"] == "stored"
    assert learner._store.all_facts()[0].content == "User prefers dark mode."


def test_remember_reinforces_near_duplicate(learner: MemoryLearner) -> None:
    learner.remember("User prefers dark mode.")
    second = learner.remember("The user prefers dark mode always.")

    facts = learner._store.all_facts()

    assert second["status"] == "reinforced"
    assert len(facts) == 1
    assert facts[0].times_reinforced == 2


def test_remember_keeps_distinct_facts_separate(learner: MemoryLearner) -> None:
    learner.remember("User prefers dark mode.")
    learner.remember("Firewall blocks port 3389 RDP.")

    facts = learner._store.all_facts()

    assert len(facts) == 2


def test_recall_context_returns_readable_block(learner: MemoryLearner) -> None:
    learner.remember("User prefers dark mode.")

    context = learner.recall_context()

    assert "User prefers dark mode." in context
    assert "Alfred already knows" in context


def test_recall_context_empty_when_no_facts(learner: MemoryLearner) -> None:
    assert learner.recall_context() == ""


def test_recall_filters_by_relevance(learner: MemoryLearner) -> None:
    learner.remember("User prefers dark mode.")
    learner.remember("Firewall blocks port 3389 RDP.")

    results = learner.recall("firewall port")

    assert len(results) == 1
    assert "Firewall" in results[0].content
