"""What you have been doing, as opposed to what the machine has.

Every collector Alfred had watched the plumbing: processor, memory,
disk, network, power, updates. So the proactive loop could tell you a
disk was filling and could not tell you that you had been in the same
document for three hours. There was nothing personal for it to be
proactive about.
"""

from datetime import datetime, timedelta

from src.brain.activity import ActivityCollector, ActivityLog, _tidy, watching

MORNING = datetime(2026, 9, 1, 9, 0)


def _log(tmp_path):
    return ActivityLog(tmp_path / "a.sqlite3")


class _Clock:
    def __init__(self, start=MORNING):
        self.now = start

    def __call__(self):
        return self.now

    def on(self, minutes):
        self.now += timedelta(minutes=minutes)


# ------------------------------------------------------ what it keeps


def test_a_stretch_of_work_is_recorded_when_you_move_on(tmp_path):
    log = _log(tmp_path)
    clock = _Clock()
    where = ["Code", "alfred - main.py"]
    collector = ActivityCollector(log, foreground=lambda: tuple(where),
                                  clock=clock)

    collector.collect()
    clock.on(45)
    where[:] = ["chrome", "Gmail"]
    collector.collect()

    today = log.today(clock.now)
    assert today[0]["app"] == "Code"
    assert today[0]["seconds"] == 45 * 60


def test_a_window_passed_through_is_not_worth_recording(tmp_path):
    """Alt-tabbing past something is not time spent in it."""
    log = _log(tmp_path)
    clock = _Clock()
    where = ["Code", "main.py"]
    collector = ActivityCollector(log, foreground=lambda: tuple(where),
                                  clock=clock)

    collector.collect()
    clock.on(0)                      # straight past it
    where[:] = ["chrome", "Gmail"]
    collector.collect()

    assert log.today(clock.now) == []


def test_it_says_how_long_you_have_been_there(tmp_path):
    log = _log(tmp_path)
    clock = _Clock()
    collector = ActivityCollector(log, foreground=lambda: ("Code", "main.py"),
                                  clock=clock)

    collector.collect()
    clock.on(30)
    seen = collector.collect()

    where = [o for o in seen if o.key == "activity.where"][0]
    assert where.value["minutes"] == 30
    assert "Code" in where.summary


def test_a_long_stretch_is_something_the_brain_can_notice(tmp_path):
    """The value only changes when it crosses the line, so the brain
    notices the crossing rather than being told every ninety seconds."""
    log = _log(tmp_path)
    clock = _Clock()
    collector = ActivityCollector(log, foreground=lambda: ("Code", "main.py"),
                                  clock=clock)

    collector.collect()
    short = [o for o in collector.collect() if o.key == "activity.long_stretch"][0]
    clock.on(200)
    long = [o for o in collector.collect() if o.key == "activity.long_stretch"][0]

    assert short.value is False
    assert long.value is True


# -------------------------------------------------- what it will not keep


def test_a_title_that_names_something_private_is_not_written_down():
    """Knowing you spent an hour in a password manager does not require
    knowing which entry you opened."""
    for title in (
        "Bitwarden - Barclays login",
        "1Password",
        "PayPal - Send Money",
        "InPrivate - Microsoft Edge",
    ):
        assert _tidy(title) == "(private)", title


def test_an_ordinary_title_is_kept():
    assert _tidy("alfred - main.py - Visual Studio Code") == \
        "alfred - main.py - Visual Studio Code"


def test_a_very_long_title_is_cut():
    assert len(_tidy("x" * 400)) == 120


def test_it_can_be_turned_off(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRED_WATCH_ME", "false")
    collector = ActivityCollector(_log(tmp_path),
                                  foreground=lambda: ("Code", "main.py"))

    assert watching() is False
    assert collector.collect() == []


def test_it_is_on_unless_told_otherwise(monkeypatch):
    monkeypatch.delenv("ALFRED_WATCH_ME", raising=False)
    assert watching() is True


def test_all_of_it_can_be_forgotten(tmp_path):
    log = _log(tmp_path)
    log.record("Code", "main.py", MORNING, MORNING + timedelta(minutes=30))

    assert log.forget() == 1
    assert log.today(MORNING + timedelta(hours=1)) == []


# ---------------------------------------------------------- habits


def test_two_mornings_is_a_coincidence_not_a_habit(tmp_path):
    """Being told about a coincidence as though it were a habit is
    worse than being told nothing."""
    log = _log(tmp_path)
    for day in range(2):
        start = MORNING + timedelta(days=day)
        log.record("Outlook", "Inbox", start, start + timedelta(minutes=20))

    assert log.habits(days=14, now=MORNING + timedelta(days=14)) == []


def test_something_done_most_mornings_is(tmp_path):
    log = _log(tmp_path)
    for day in range(6):
        start = MORNING + timedelta(days=day)
        log.record("Outlook", "Inbox", start, start + timedelta(minutes=20))

    habits = log.habits(days=14, now=MORNING + timedelta(days=14))
    assert habits and habits[0]["app"] == "Outlook"
    assert habits[0]["hour"] == 9
