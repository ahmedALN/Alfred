"""What Alfred says when it starts, and when it keeps that to itself."""

from __future__ import annotations

from datetime import datetime

from src.voice.greeting import (
    enabled, greeting, may_speak, part_of_day, phrase,
)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 1, hour, minute)


def test_the_hour_decides_what_it_says():
    assert part_of_day(_at(7)) == "morning"
    assert part_of_day(_at(13)) == "afternoon"
    assert part_of_day(_at(20)) == "evening"
    assert part_of_day(_at(3)) == "night"


def test_a_machine_started_at_four_in_the_morning_says_nothing_aloud():
    """Alfred starts with the PC, and PCs get turned on at odd hours.

    The greeting is the point of contact that tells you it is running,
    so it is not dropped - it is written into the interface instead.
    """
    line, aloud = greeting(_at(4))
    assert line                      # there is still something to show
    assert aloud is False


def test_it_speaks_during_the_day():
    for hour in (8, 12, 17, 22):
        assert may_speak(_at(hour)) is True, f"{hour}:00 should be audible"


def test_the_quiet_window_wraps_past_midnight():
    """The only case that matters - nobody sets quiet hours 10am to 2pm."""
    assert may_speak(_at(23, 30), hours="23:00-07:00") is False
    assert may_speak(_at(2), hours="23:00-07:00") is False
    assert may_speak(_at(6, 59), hours="23:00-07:00") is False
    assert may_speak(_at(7), hours="23:00-07:00") is True
    assert may_speak(_at(22, 59), hours="23:00-07:00") is True


def test_a_window_that_does_not_wrap_still_works():
    assert may_speak(_at(13), hours="12:00-14:00") is False
    assert may_speak(_at(15), hours="12:00-14:00") is True


def test_an_empty_or_broken_window_means_no_restriction():
    assert may_speak(_at(3), hours="") is True
    assert may_speak(_at(3), hours="not a time") is True


def test_it_does_not_say_the_same_thing_every_morning():
    said = {phrase(_at(9)) for _ in range(60)}
    assert len(said) > 1


def test_greeting_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("ALFRED_GREET_ON_START", "false")
    assert enabled() is False
    monkeypatch.setenv("ALFRED_GREET_ON_START", "true")
    assert enabled() is True


def test_it_is_on_unless_you_say_otherwise(monkeypatch):
    monkeypatch.delenv("ALFRED_GREET_ON_START", raising=False)
    assert enabled() is True
