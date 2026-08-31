"""Reading a time out of the way people actually say one.

"remind me at six", "tomorrow morning", "in twenty minutes", "every
weekday at 9", "every Friday". None of these is a timestamp and all of
them are how anybody asks for something to happen later.

Deliberately no dependency. A date library would parse more shapes than
this, and would also happily read "the 5" out of "open the 5th
instance", which is worse than not parsing it at all. This only claims
the phrasings that unambiguously mean a time, and returns nothing for
everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

_DAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

# What people mean by a part of the day, rather than a clock time.
_VAGUE = {
    "morning": 8, "the morning": 8, "first thing": 7,
    "lunchtime": 13, "lunch": 13, "midday": 12, "noon": 12,
    "afternoon": 14, "evening": 19, "tonight": 20, "night": 22,
    "bedtime": 23,
}

_UNITS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
}

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "fortyfive": 45, "half": 30,
}


@dataclass
class When:
    """When something should happen, and whether it happens again."""

    at: datetime
    repeat: str = ""          # "" | daily | weekdays | weekly | interval
    every: int = 0            # seconds, for interval
    weekday: int = -1         # for weekly
    said: str = ""            # the words it was read from

    def after(self, moment: datetime) -> "datetime | None":
        """The next time this comes round after ``moment``, or None if
        it was a one-off that has already happened."""
        if not self.repeat:
            return None

        if self.repeat == "interval":
            nxt = self.at
            while nxt <= moment:
                nxt += timedelta(seconds=self.every)
            return nxt

        nxt = self.at
        while nxt <= moment:
            if self.repeat == "daily":
                nxt += timedelta(days=1)
            elif self.repeat == "weekly":
                nxt += timedelta(days=7)
            elif self.repeat == "weekdays":
                nxt += timedelta(days=1)
                while nxt.weekday() >= 5:
                    nxt += timedelta(days=1)
            else:
                return None
        return nxt


# ------------------------------------------------------------ reading


def read(text: str, now: datetime | None = None) -> When | None:
    """The time in this sentence, if there plainly is one."""
    now = now or datetime.now()
    said = (text or "").strip().lower()
    if not said:
        return None

    # Order matters. "tomorrow at 08:15" contains a clock time, so
    # whichever reader runs first wins - and _at_a_time, reading only
    # the clock, would put it eight hours from now instead of tomorrow.
    # The readers that know which DAY is meant go first.
    for attempt in (_in_a_while, _every, _tomorrow, _on_a_day, _at_a_time):
        found = attempt(said, now)
        if found is not None:
            found.said = text.strip()
            return found
    return None


def _number(word: str) -> int | None:
    word = word.strip()
    if word.isdigit():
        return int(word)
    return _NUMBER_WORDS.get(word)


_IN_RE = re.compile(
    r"\bin\s+(?:(\d+)|([a-z-]+))\s+(second|seconds|sec|secs|minute|minutes|"
    r"min|mins|hour|hours|hr|hrs|day|days|week|weeks)\b"
)


def _in_a_while(said: str, now: datetime) -> When | None:
    m = _IN_RE.search(said)
    if not m:
        return None
    count = _number(m.group(1) or m.group(2) or "")
    if not count:
        return None
    return When(at=now + timedelta(seconds=count * _UNITS[m.group(3)]))


_EVERY_RE = re.compile(
    r"\bevery\s+(morning|day|weekday|weekdays|evening|night|afternoon|"
    r"lunchtime|hour|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"sunday|mon|tue|tues|wed|thu|thurs|fri|sat|sun)\b"
)


def _every(said: str, now: datetime) -> When | None:
    m = _EVERY_RE.search(said)
    if not m:
        return None
    word = m.group(1)

    if word == "hour":
        return When(at=now + timedelta(hours=1), repeat="interval", every=3600)

    clock = _hhmm(_clock(said))

    if word in _DAYS:
        hour, minute = clock or (9, 0)
        at = _next_weekday(now, _DAYS[word], hour, minute)
        return When(at=at, repeat="weekly", weekday=_DAYS[word])

    if word in ("weekday", "weekdays"):
        hour, minute = clock or (9, 0)
        at = _next_at(now, hour, minute)
        while at.weekday() >= 5:
            at += timedelta(days=1)
        return When(at=at, repeat="weekdays")

    hour, minute = clock or (_VAGUE.get(word, 9), 0)
    return When(at=_next_at(now, hour, minute), repeat="daily")


_ON_DAY_RE = re.compile(r"\b(?:on\s+)?(" + "|".join(_DAYS) + r")\b")


def _on_a_day(said: str, now: datetime) -> When | None:
    if "every" in said:
        return None
    m = _ON_DAY_RE.search(said)
    if not m:
        return None
    hour, minute = _hhmm(_clock(said)) or (9, 0)
    return When(at=_next_weekday(now, _DAYS[m.group(1)], hour, minute))


_TOMORROW_RE = re.compile(r"\btomorrow\b")


def _tomorrow(said: str, now: datetime) -> When | None:
    if not _TOMORROW_RE.search(said):
        return None
    hour, minute = _hhmm(_clock(said)) or _vague(said) or (9, 0)
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return When(at=at + timedelta(days=1))


def _at_a_time(said: str, now: datetime) -> When | None:
    found = _clock(said)
    if found is None:
        vague = _vague(said)
        if vague is None or not re.search(r"\b(this|at|by)\b", said):
            return None
        return When(at=_next_at(now, vague[0], vague[1]))

    hour, minute, certain = found
    if certain:
        return When(at=_next_at(now, hour, minute))

    # "at 6" at half past two in the afternoon means this evening,
    # not six tomorrow morning. Both readings are legitimate; the
    # one that comes round sooner is the one anybody means.
    morning = _next_at(now, hour, minute)
    evening = _next_at(now, (hour + 12) % 24, minute)
    return When(at=min(morning, evening))


_CLOCK_RE = re.compile(
    r"\b(at|by|around|about)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|o'?clock)?\b"
)


def _clock(said: str) -> tuple[int, int, bool] | None:
    """An actual clock time, but only when it is being used as one.

    A bare number is not a time. "open the 5th instance" must not become
    five o'clock and neither must "what is 6 times 7", so a number only
    counts when something around it says it is one: an "at" or a "by" in
    front, minutes after it, or am/pm.

    The third value says whether the hour was stated beyond doubt. "6pm"
    was. Plain "6" was not, and the caller decides what to make of that.
    """
    for m in _CLOCK_RE.finditer(said):
        prefix = m.group(1)
        hour = int(m.group(2))
        minute = int(m.group(3) or 0)
        suffix = (m.group(4) or "").replace("'", "")

        anchored = bool(prefix) or bool(suffix) or m.group(3) is not None
        if not anchored or hour > 23 or minute > 59:
            continue

        # A zero-padded hour is somebody writing 24-hour time, and means
        # what it says: "08:15" is the morning even when it is said in
        # the afternoon.
        padded = m.group(2).startswith("0") and len(m.group(2)) == 2
        certain = bool(suffix) or padded or hour == 0 or hour > 12
        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
        return hour, minute, certain
    return None


def _hhmm(found: "tuple[int, int, bool] | None") -> "tuple[int, int] | None":
    return (found[0], found[1]) if found else None


def _vague(said: str) -> tuple[int, int] | None:
    for word, hour in sorted(_VAGUE.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(word) + r"\b", said):
            return hour, 0
    return None


def _next_at(now: datetime, hour: int, minute: int) -> datetime:
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return at


def _next_weekday(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    ahead = (weekday - at.weekday()) % 7
    if ahead == 0 and at <= now:
        ahead = 7
    return at + timedelta(days=ahead)


def phrase(when: When, now: datetime | None = None) -> str:
    """How to say it back, so you can tell it understood."""
    now = now or datetime.now()
    at = when.at

    if when.repeat == "interval":
        every = when.every
        unit = "hour" if every % 3600 == 0 else "minute"
        count = every // (3600 if unit == "hour" else 60)
        return f"every {count} {unit}{'s' if count != 1 else ''}"

    clock = at.strftime("%H:%M")
    if when.repeat == "daily":
        return f"every day at {clock}"
    if when.repeat == "weekdays":
        return f"every weekday at {clock}"
    if when.repeat == "weekly":
        return f"every {at.strftime('%A')} at {clock}"

    if at.date() == now.date():
        return f"today at {clock}"
    if at.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    if (at - now) < timedelta(days=7):
        return f"{at.strftime('%A')} at {clock}"
    return at.strftime("%d %b at %H:%M")
