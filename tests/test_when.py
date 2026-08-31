"""Reading a time out of the way people actually say one."""

from datetime import datetime

from src.brain.when import phrase, read

TUE_AFTERNOON = datetime(2026, 9, 1, 14, 30)


def _said(text, now=TUE_AFTERNOON):
    when = read(text, now)
    return phrase(when, now) if when else None


# ------------------------------------------------------- what it reads


def test_a_clock_time():
    assert _said("remind me at 6pm to take the bins out") == "today at 18:00"


def test_a_time_with_minutes():
    assert _said("wake me tomorrow at 08:15") == "tomorrow at 08:15"


def test_a_while_from_now():
    assert _said("in 20 minutes tell me to stretch") == "today at 14:50"
    assert _said("in an hour check the downloads") == "today at 15:30"


def test_a_part_of_the_day():
    assert _said("tomorrow morning remind me to call the bank") == "tomorrow at 08:00"


def test_every_day():
    assert _said("every morning summarise my inbox") == "every day at 08:00"


def test_every_weekday():
    assert _said("every weekday at 9 tell me what is on") == "every weekday at 09:00"


def test_a_particular_day_every_week():
    assert _said("every friday tidy my downloads") == "every Friday at 09:00"


def test_a_day_this_week():
    assert _said("on thursday remind me about the dentist") == "Thursday at 09:00"


def test_a_regular_interval():
    assert _said("every hour check my email") == "every 1 hour"


# ------------------------------- what it must refuse to read as a time


def test_a_number_in_a_task_is_not_a_time():
    """This is why there is no date library here. "the 5th instance" is
    exactly the kind of thing one would happily read as five o'clock."""
    assert read("open the 5th instance in multimc", TUE_AFTERNOON) is None


def test_arithmetic_is_not_a_time():
    assert read("what is 6 times 7", TUE_AFTERNOON) is None


def test_an_ordinary_request_is_not_a_time():
    assert read("search steam for hades", TUE_AFTERNOON) is None
    assert read("play track 3", TUE_AFTERNOON) is None


def test_nothing_at_all():
    assert read("", TUE_AFTERNOON) is None


# ------------------------------------------- the half of it that guesses


def test_six_said_in_the_afternoon_means_this_evening():
    """Both readings are legitimate. The one that comes round sooner is
    the one anybody means."""
    assert _said("remind me at 6", datetime(2026, 9, 1, 14, 30)) == "today at 18:00"


def test_six_said_at_night_means_the_morning():
    assert _said("remind me at 6", datetime(2026, 9, 1, 20, 0)) == "tomorrow at 06:00"


def test_a_time_said_plainly_is_not_second_guessed():
    assert _said("remind me at 6am", datetime(2026, 9, 1, 20, 0)) == "tomorrow at 06:00"
    assert _said("at 18:00", datetime(2026, 9, 1, 12, 0)) == "today at 18:00"
