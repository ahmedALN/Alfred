import tempfile
from pathlib import Path

import pytest

from src.memory.store import MemoryStore


@pytest.fixture()
def store() -> MemoryStore:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_memory.sqlite3"
        s = MemoryStore(db_path)
        yield s
        s.close()


def test_add_and_list_facts(store: MemoryStore) -> None:
    fact_id = store.add_fact(
        "The user's main dev machine is named RIG-01.",
        category="system",
        source="test",
    )

    facts = store.all_facts()

    assert len(facts) == 1
    assert facts[0].id == fact_id
    assert facts[0].category == "system"
    assert facts[0].times_reinforced == 1


def test_reinforce_fact_increments_count(store: MemoryStore) -> None:
    fact_id = store.add_fact("User prefers dark mode.", category="preference")

    store.reinforce_fact(fact_id)
    store.reinforce_fact(fact_id, confidence=0.95)

    facts = store.all_facts()

    assert facts[0].times_reinforced == 3
    assert facts[0].confidence == 0.95


def test_facts_filtered_by_category(store: MemoryStore) -> None:
    store.add_fact("Fact A", category="preference")
    store.add_fact("Fact B", category="system")

    preferences = store.all_facts(category="preference")

    assert len(preferences) == 1
    assert preferences[0].content == "Fact A"


def test_delete_fact(store: MemoryStore) -> None:
    fact_id = store.add_fact("Temporary fact")

    store.delete_fact(fact_id)

    assert store.all_facts() == []


def test_turns_are_recorded_per_session(store: MemoryStore) -> None:
    store.add_turn("session-1", "user", "What ports are blocked?")
    store.add_turn("session-1", "alfred", "Checking now.")
    store.add_turn("session-2", "user", "Unrelated session.")

    turns = store.session_turns("session-1")

    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "alfred"


def test_empty_turn_text_is_ignored(store: MemoryStore) -> None:
    store.add_turn("session-1", "user", "   ")

    assert store.session_turns("session-1") == []


def test_tool_events_and_failure_rate(store: MemoryStore) -> None:
    store.add_tool_event(
        "session-1", "powershell", {"command": "ls"}, {"status": "success"}, True
    )
    store.add_tool_event(
        "session-1", "powershell", {"command": "bad"}, {"status": "error"}, False
    )

    assert store.tool_failure_rate("powershell") == 0.5
    assert store.tool_failure_rate("unused_tool") == 0.0

    events = store.recent_tool_events()
    assert len(events) == 2
