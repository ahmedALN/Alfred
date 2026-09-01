"""What Alfred says when it comes up.

Alfred starts with the machine, which means it usually starts when you
have just sat down - and an assistant that boots in total silence gives
you no way to know it is there short of testing it.

So it says something. Not a fanfare: one short line, suited to the hour,
and different enough each time that it does not become wallpaper.

The one rule that matters is the hour. Alfred autostarts, and machines
get turned on at four in the morning by people who did not intend to
wake the house. Between the small hours it still greets you - in the
interface, in writing - and does not say a word out loud.
"""

from __future__ import annotations

import os
import random
from datetime import datetime, time as clock

# Enough that you will not hear the same one twice in a week, and all
# short enough to talk over.
_MORNING = [
    "Morning. Everything's running.",
    "Good morning. I'm up and listening.",
    "Morning. All set whenever you are.",
    "Morning. Nothing's broken overnight.",
]
_AFTERNOON = [
    "Afternoon. I'm here.",
    "Good afternoon. Ready when you are.",
    "Afternoon. Everything's running.",
    "Back up and listening.",
]
_EVENING = [
    "Evening. I'm here if you need me.",
    "Good evening. All running.",
    "Evening. Ready when you are.",
    "Up and listening.",
]
_NIGHT = [
    "I'm up.",
    "Running, and keeping quiet.",
    "Here if you need me.",
]


def part_of_day(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def phrase(now: datetime | None = None) -> str:
    """One line, suited to the hour."""
    return random.choice({
        "morning": _MORNING,
        "afternoon": _AFTERNOON,
        "evening": _EVENING,
        "night": _NIGHT,
    }[part_of_day(now)])


def _window(spec: str) -> tuple[clock, clock] | None:
    """"23:00-07:00" -> the two ends of it."""
    try:
        start, end = spec.split("-", 1)
        sh, sm = (int(x) for x in start.strip().split(":"))
        eh, em = (int(x) for x in end.strip().split(":"))
        return clock(sh, sm), clock(eh, em)
    except Exception:  # noqa: BLE001
        return None


def may_speak(now: datetime | None = None, hours: str | None = None) -> bool:
    """Is this an hour at which a voice in the room is welcome?

    The window wraps past midnight, which is the only case that
    matters: nobody sets quiet hours from ten in the morning.
    """
    hours = os.getenv("ALFRED_GREET_QUIET_HOURS", "23:00-07:00") if hours is None else hours
    if not hours.strip():
        return True

    pair = _window(hours)
    if pair is None:
        return True

    start, end = pair
    at = (now or datetime.now()).time()
    if start <= end:
        return not (start <= at < end)
    # wraps midnight
    return not (at >= start or at < end)


def enabled() -> bool:
    return os.getenv("ALFRED_GREET_ON_START", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def greeting(now: datetime | None = None) -> tuple[str, bool]:
    """The line, and whether it should be said out loud.

    Written down either way: the interface shows it, so starting in the
    small hours still leaves a trace that Alfred came up, without
    anything being audible.
    """
    return phrase(now), may_speak(now)
