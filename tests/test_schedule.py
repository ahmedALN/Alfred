"""Things Alfred is supposed to do later.

Until this existed nothing survived the moment it was said: Alfred could
be asked to do something and would, but could not be asked to do it at
seven, because there was nowhere to write that down.
"""

from datetime import datetime, timedelta

from src.brain.schedule import ScheduleStore
from src.brain.when import read
from src.tools.schedule_tool import ScheduleTool

NOW = datetime(2026, 9, 1, 14, 30)          # a Tuesday afternoon


def _store(tmp_path):
    return ScheduleStore(tmp_path / "s.sqlite3")


def _tool(tmp_path, now=NOW):
    store = _store(tmp_path)
    return ScheduleTool(store, now=lambda: now), store


# ------------------------------------------------------------ writing


def test_a_reminder_is_kept(tmp_path):
    tool, store = _tool(tmp_path)

    answer = tool.execute({
        "action": "add", "when": "at 6pm", "what": "take the bins out",
    })

    assert answer["status"] == "success"
    assert answer["when"] == "today at 18:00"
    assert "bins" in answer["confirm"]
    assert len(store.pending()) == 1


def test_a_job_is_a_different_thing_from_a_reminder(tmp_path):
    """A reminder that quietly ran a task would be alarming; a task
    that only reminded you would be useless."""
    tool, store = _tool(tmp_path)

    tool.execute({"action": "add", "when": "every morning",
                  "what": "Summarise my inbox.", "kind": "do"})

    assert store.pending()[0]["kind"] == "do"


def test_a_time_it_cannot_read_is_refused_with_an_example(tmp_path):
    tool, _ = _tool(tmp_path)

    answer = tool.execute({"action": "add", "when": "at some point",
                           "what": "do the thing"})

    assert answer["status"] == "error"
    assert "6pm" in answer["error"]


def test_the_time_can_be_left_in_the_sentence(tmp_path):
    """People do not separate the two halves when they speak."""
    tool, _ = _tool(tmp_path)

    answer = tool.execute({
        "action": "add", "when": "", "what": "remind me at 6pm to eat",
    })

    assert answer["status"] == "success"
    assert answer["when"] == "today at 18:00"


def test_something_with_no_what_is_refused(tmp_path):
    tool, _ = _tool(tmp_path)

    assert tool.execute({"action": "add", "when": "at 6pm"})["status"] == "error"


# ------------------------------------------------------------ firing


def test_only_what_is_actually_due_comes_back(tmp_path):
    store = _store(tmp_path)
    store.add(read("at 6pm", NOW), "bins")
    store.add(read("tomorrow at 9am", NOW), "dentist")

    assert [r["goal"] for r in store.due(NOW + timedelta(hours=4))] == ["bins"]


def test_a_one_off_does_not_come_round_again(tmp_path):
    store = _store(tmp_path)
    row = store.add(read("at 6pm", NOW), "bins")

    store.ran(row["id"], NOW + timedelta(hours=4))

    assert store.pending() == []


def test_a_daily_one_moves_to_tomorrow(tmp_path):
    store = _store(tmp_path)
    row = store.add(read("every morning", NOW), "Summarise my inbox.", kind="do")
    first = store.get(row["id"])["due"]

    store.ran(row["id"], NOW + timedelta(days=1))
    second = store.get(row["id"])["due"]

    assert second > first
    assert len(store.pending()) == 1


def test_a_week_asleep_does_not_owe_seven_breakfasts(tmp_path):
    """The next one is worked out from now, not from the missed time."""
    store = _store(tmp_path)
    row = store.add(read("every morning", NOW), "brief me", kind="do")

    woke = NOW + timedelta(days=7)
    store.ran(row["id"], woke)

    due = datetime.fromisoformat(store.get(row["id"])["due"])
    assert due > woke
    assert (due - woke) < timedelta(days=1)


# ------------------------------------------------------------ managing


def test_what_is_pending_can_be_listed(tmp_path):
    tool, _ = _tool(tmp_path)
    tool.execute({"action": "add", "when": "at 6pm", "what": "bins"})

    listed = tool.execute({"action": "list"})

    assert listed["count"] == 1
    assert listed["scheduled"][0]["what"] == "bins"


def test_one_can_be_called_off(tmp_path):
    tool, store = _tool(tmp_path)
    added = tool.execute({"action": "add", "when": "at 6pm", "what": "bins"})

    assert tool.execute({"action": "cancel", "id": added["id"]})["status"] == "success"
    assert store.pending() == []


def test_calling_off_something_that_is_not_there_says_so(tmp_path):
    tool, _ = _tool(tmp_path)

    assert tool.execute({"action": "cancel", "id": "nope"})["status"] == "not_found"


def test_it_survives_being_reopened(tmp_path):
    """The whole point is outliving the moment it was said - and the
    process it was said to."""
    store = _store(tmp_path)
    store.add(read("every weekday at 9", NOW), "brief me", kind="do")
    store.close()

    again = ScheduleStore(tmp_path / "s.sqlite3")
    assert len(again.pending()) == 1
