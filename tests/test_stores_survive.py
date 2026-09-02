"""A durable store that gives up on the way out is not durable.

From logs/alfred.log, 2026-09-02 01:24, five in a row:

    [Tasks] persist failed: Cannot operate on a closed database.

Shutdown closed the task store while the worker was still finishing its
last job, so five status changes were lost from the record of what
Alfred had been asked to do - at the one moment the record matters,
which is the moment it has to survive a restart.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.brain.task_store import TaskStore


@pytest.fixture()
def store(tmp_path):
    s = TaskStore(tmp_path / "tasks.sqlite3")
    yield s
    s.close()


def test_a_write_after_close_still_lands(store):
    store.add("t1", "Open Notepad.")
    store.close()

    store.set_status("t1", "done", "Opened it.")

    assert store.recent(1)[0]["status"] == "done"


def test_a_read_after_close_still_works(store):
    store.add("t1", "Open Notepad.")
    store.close()

    assert store.recent(1)[0]["goal"] == "Open Notepad."


def test_the_record_survives_a_restart(tmp_path):
    """The whole reason the store exists."""
    path = tmp_path / "tasks.sqlite3"

    first = TaskStore(path)
    first.add("t1", "Organise Downloads.")
    first.set_status("t1", "running")
    first.close()

    second = TaskStore(path)
    try:
        assert [t["id"] for t in second.unfinished()] == ["t1"]
    finally:
        second.close()


def test_closing_twice_is_not_an_error(store):
    store.close()
    store.close()


def test_a_reopened_store_is_the_same_store(store, tmp_path):
    store.add("t1", "one")
    store.close()
    store.add("t2", "two")

    assert {t["id"] for t in store.recent(10)} == {"t1", "t2"}

    # ...and on disk, not merely in memory.
    conn = sqlite3.connect(tmp_path / "tasks.sqlite3")
    try:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
    finally:
        conn.close()


def test_the_source_column_migration_still_runs_on_an_old_database(tmp_path):
    """A database created before tasks had a `source` column."""
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, goal TEXT NOT NULL, "
        "status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "INSERT INTO tasks VALUES ('old', 'a goal', 'queued', '', "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');"
    )
    conn.commit()
    conn.close()

    store = TaskStore(path)
    try:
        assert store.recent(1)[0]["source"] == "voice"
    finally:
        store.close()


def test_reopening_does_not_lose_the_migration(tmp_path):
    """The reopen path has to build the schema too, not assume it."""
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    try:
        store.close()
        store.add("t1", "after the close", source="phone")
        assert store.recent(1)[0]["source"] == "phone"
    finally:
        store.close()
