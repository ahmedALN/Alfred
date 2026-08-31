"""An account of the day, from the record rather than from memory.

Everything was already written down and none of it was readable: tasks
in one file, what it said in another, what it learned in a third, what
it could not get past in a fourth. A person who wanted to know how the
day had gone could read six SQLite tables, or ask Alfred and get
whatever it happened to remember about the last few minutes.
"""

import sqlite3
from datetime import date, datetime, timedelta

from src.brain.diary import Day, gather, tell

TODAY = date(2026, 9, 1)


def _make(tmp_path, tasks=(), skills=(), walls=(), audit=()):
    def db(name, schema, rows, sql):
        conn = sqlite3.connect(tmp_path / name)
        conn.execute(schema)
        conn.executemany(sql, rows)
        conn.commit()
        conn.close()

    db("alfred_tasks.sqlite3",
       "CREATE TABLE tasks (id TEXT, goal TEXT, status TEXT, summary TEXT, "
       "source TEXT, created_at TEXT, updated_at TEXT)",
       tasks,
       "INSERT INTO tasks VALUES (?,?,?,?,?,?,?)")
    db("alfred_skills.sqlite3",
       "CREATE TABLE skills (template TEXT, created_at TEXT)",
       skills, "INSERT INTO skills VALUES (?,?)")
    db("alfred_limitations.sqlite3",
       "CREATE TABLE limitations (tool TEXT, detail TEXT, hits INT, "
       "workaround TEXT, last_seen TEXT)",
       walls, "INSERT INTO limitations VALUES (?,?,?,?,?)")
    db("alfred_brain_audit.sqlite3",
       "CREATE TABLE brain_audit (id INTEGER PRIMARY KEY, kind TEXT, "
       "payload TEXT, created_at TEXT)",
       audit, "INSERT INTO brain_audit (kind, payload, created_at) VALUES (?,?,?)")
    return tmp_path


def _at(hour=10):
    return datetime.combine(TODAY, datetime.min.time()).replace(
        hour=hour).isoformat()


# ------------------------------------------------------- what it gathers


def test_it_separates_what_you_asked_for_from_what_it_did_alone(tmp_path):
    root = _make(tmp_path, tasks=[
        ("1", "Open Steam", "done", "", "voice", _at(), _at()),
        ("2", "Tidy downloads", "done", "", "brain", _at(), _at()),
    ])

    day = gather(root, TODAY)

    assert [t["goal"] for t in day.asked] == ["Open Steam"]
    assert [t["goal"] for t in day.own] == ["Tidy downloads"]


def test_yesterday_is_not_today(tmp_path):
    root = _make(tmp_path, tasks=[
        ("1", "Old thing", "done", "", "voice",
         (datetime.combine(TODAY, datetime.min.time())
          - timedelta(days=1)).isoformat(), _at()),
    ])

    assert gather(root, TODAY).asked == []


def test_what_it_could_not_get_past_is_part_of_the_record(tmp_path):
    """An assistant that only reports its successes is not giving an
    account of itself."""
    root = _make(tmp_path, walls=[
        ("ui_control", "no control matches", 3, "", _at()),
    ])

    day = gather(root, TODAY)

    assert day.stuck[0]["hits"] == 3
    assert "still stuck" in day.facts()


def test_a_wall_it_got_round_is_described_as_such(tmp_path):
    root = _make(tmp_path, walls=[
        ("powershell", "bad path", 2, "used $HOME instead", _at()),
    ])

    assert "found a way round" in gather(root, TODAY).facts()


def test_routines_learned_are_in_it(tmp_path):
    root = _make(tmp_path, skills=[("Open MultiMC.", _at())])

    assert gather(root, TODAY).learned == ["Open MultiMC."]


def test_a_missing_store_does_not_take_the_account_down(tmp_path):
    """A day's account should not fail because one file is absent, or
    being written to, or older than the column being asked for."""
    day = gather(tmp_path, TODAY)      # nothing there at all

    assert day.quiet is True
    assert day.facts() == "Nothing on the record."


# --------------------------------------------------------- what it says


class _Voice:
    def __init__(self):
        self.prompt = ""

    def generate(self, prompt, **kw):
        self.prompt = prompt
        return "You asked me for one thing. It worked."


def test_the_model_is_told_the_facts_not_asked_to_remember_them(tmp_path):
    """An account assembled from memory is a story. This has to be a
    record."""
    root = _make(tmp_path, tasks=[
        ("1", "Open Steam", "done", "", "voice", _at(), _at()),
    ])
    voice = _Voice()

    tell(gather(root, TODAY), voice)

    assert "Open Steam" in voice.prompt
    assert "JOBS YOU ASKED FOR" in voice.prompt


def test_a_quiet_day_says_so_without_asking_anybody(tmp_path):
    said = tell(gather(tmp_path, TODAY), _Voice())

    assert "Nothing much" in said


def test_with_no_model_it_still_gives_the_record(tmp_path):
    root = _make(tmp_path, tasks=[
        ("1", "Open Steam", "done", "", "voice", _at(), _at()),
    ])

    assert "Open Steam" in tell(gather(root, TODAY), None)


def test_a_model_that_falls_over_falls_back_to_the_record(tmp_path):
    class _Broken:
        def generate(self, *a, **k):
            raise RuntimeError("no route to host")

    root = _make(tmp_path, tasks=[
        ("1", "Open Steam", "done", "", "voice", _at(), _at()),
    ])

    assert "Open Steam" in tell(gather(root, TODAY), _Broken())


def test_it_is_told_never_to_leave_a_failure_out():
    from src.brain.diary import _SYSTEM

    assert "never soften it" in _SYSTEM
    assert "worthless" in _SYSTEM
