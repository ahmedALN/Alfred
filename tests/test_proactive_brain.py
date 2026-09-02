"""The brain has to be able to notice something about you.

Three collectors were written to give it that - what you have been
doing, what is overdue, whether the mailbox link has lapsed. All three
were wired into main.py. All three ran every ninety seconds. And
`Perception._diff` had no branch for a single one of their keys, so
every reading they produced was collected and dropped.

The record of what that cost: across 1,338 ticks the brain produced
five proposals. All five were the same sentence about free RAM, because
free RAM was the only thing anything downstream could see.

These tests are about the readings reaching something that can act on
them, and about the structural guard that stops it happening again -
a collector wired in without a check is now a failure here rather than
a silence for a week.
"""

from __future__ import annotations

import pytest

from src.brain.perception import (
    CONTEXT_ONLY_KEYS,
    Perception,
    _is_handled,
)
from src.brain.types import Observation


class Scripted:
    """A collector that reads from a script, one batch per tick."""

    name = "scripted"

    def __init__(self, batches):
        self._batches = list(batches)

    def safe_collect(self):
        return self._batches.pop(0) if self._batches else []


def obs(key, value, summary="", source="test"):
    return Observation(source=source, key=key, value=value,
                       summary=summary or key)


def ticks(batches):
    """Every tick's notables, in order."""
    perception = Perception(collectors=[Scripted(batches)])
    return [perception.sense()[0] for _ in batches]


# ====================================================================
# The structural guard
# ====================================================================

# Every key any collector wired into main.py can emit.
EVERY_KEY = [
    # resources
    "memory.free_pct", "cpu.load_pct", "system.uptime_hours",
    "disk.C:.free_gb", "disk.D:.free_gb",
    # network
    "network.listening_ports", "firewall.Domain.enabled",
    "firewall.Private.enabled", "firewall.Public.enabled",
    # processes / power / updates
    "process.top_cpu", "power.on_battery", "power.percent",
    "updates.pending_reboot",
    # session context
    "session.foreground_app", "session.idle_seconds", "session.fullscreen",
    # and the personal ones, which is the whole point
    "activity.where", "activity.long_stretch",
    "world.overdue", "world.due_soon", "mail.linked",
]


@pytest.mark.parametrize("key", EVERY_KEY)
def test_every_reading_reaches_a_check_or_is_named_as_context(key):
    """A collector wired in without a check throws its work away."""
    assert _is_handled(key), (
        f"{key} is collected every tick and nothing consumes it - add a "
        "check in perception.py, or name it in CONTEXT_ONLY_KEYS"
    )


def test_the_personal_keys_are_events_not_merely_context():
    """Context is read; only a notable can make Alfred say something."""
    for key in ("activity.long_stretch", "world.overdue", "world.due_soon",
                "mail.linked"):
        assert key not in CONTEXT_ONLY_KEYS


def test_perception_reports_a_reading_nothing_claims():
    perception = Perception(collectors=[Scripted([[obs("brand.new.key", 1)]])])
    perception.sense()

    assert perception.unhandled_keys() == {"brand.new.key"}


def test_a_handled_reading_is_not_reported_as_unhandled():
    perception = Perception(collectors=[Scripted([[obs("memory.free_pct", 40.0)]])])
    perception.sense()

    assert perception.unhandled_keys() == set()


# ====================================================================
# You have been in the same window for a very long time
# ====================================================================


def test_a_long_stretch_is_raised_once_when_it_starts():
    fired = ticks([
        [obs("activity.long_stretch", False)],
        [obs("activity.long_stretch", True,
             "You have been in Word for 95 minutes without a break.")],
        [obs("activity.long_stretch", True, "still going")],
        [obs("activity.long_stretch", True, "still going")],
    ])

    assert [len(f) for f in fired] == [0, 1, 0, 0]
    assert "95 minutes" in fired[1][0].summary
    assert fired[1][0].source == "activity"


def test_a_long_stretch_can_be_raised_again_after_a_break():
    fired = ticks([
        [obs("activity.long_stretch", False)],
        [obs("activity.long_stretch", True, "two hours in Word")],
        [obs("activity.long_stretch", False)],      # got up
        [obs("activity.long_stretch", True, "two hours in Excel")],
    ])

    assert [len(f) for f in fired] == [0, 1, 0, 1]


def test_where_you_are_is_context_and_never_an_interruption():
    """Which window is in front changes constantly and is nobody's news."""
    fired = ticks([
        [obs("activity.where", {"app": "chrome", "minutes": 1})],
        [obs("activity.where", {"app": "code", "minutes": 1})],
        [obs("activity.where", {"app": "spotify", "minutes": 1})],
    ])

    assert [len(f) for f in fired] == [0, 0, 0]


# ====================================================================
# Something of yours is overdue
# ====================================================================


def test_an_overdue_item_is_raised():
    fired = ticks([
        [obs("world.overdue", [])],
        [obs("world.overdue", ["Physics essay"])],
    ])

    assert len(fired[1]) == 1
    assert "Physics essay" in fired[1][0].summary
    assert fired[1][0].severity == "warn"


def test_the_same_overdue_item_is_not_raised_twice():
    """It stays overdue for days. Saying so daily is how you get muted."""
    fired = ticks([
        [obs("world.overdue", ["Physics essay"])],
        [obs("world.overdue", ["Physics essay"])],
        [obs("world.overdue", ["Physics essay"])],
    ])

    assert [len(f) for f in fired] == [1, 0, 0]


def test_a_new_item_joining_an_overdue_list_is_raised_on_its_own():
    fired = ticks([
        [obs("world.overdue", ["Physics essay"])],
        [obs("world.overdue", ["Physics essay", "Car insurance"])],
    ])

    assert len(fired[1]) == 1
    assert "Car insurance" in fired[1][0].summary
    # ...and does not drag the old one back through.
    assert "Physics" not in fired[1][0].summary


def test_something_due_soon_is_gentler_than_something_overdue():
    fired = ticks([
        [obs("world.due_soon", [])],
        [obs("world.due_soon", ["Dentist"])],
    ])

    assert fired[1][0].severity == "info"


def test_an_item_that_cleared_can_be_news_again_if_it_returns():
    fired = ticks([
        [obs("world.overdue", ["Rent"])],
        [obs("world.overdue", [])],          # paid
        [obs("world.overdue", ["Rent"])],    # next month
    ])

    assert [len(f) for f in fired] == [1, 0, 1]


def test_a_long_overdue_list_says_how_many_more():
    fired = ticks([
        [obs("world.overdue", [])],
        [obs("world.overdue", ["a", "bb", "ccc", "dddd", "eeeee", "ffffff"])],
    ])

    assert "+2 more" in fired[1][0].summary


# ====================================================================
# The mailbox link lapsed
# ====================================================================


def test_a_lapsed_google_link_is_raised_once():
    fired = ticks([
        [obs("mail.linked", True, "connected")],
        [obs("mail.linked", False, "Alfred has lost access - run: "
                                   "python -m src.workspace link")],
        [obs("mail.linked", False, "still gone")],
    ])

    assert [len(f) for f in fired] == [0, 1, 0]
    assert "workspace link" in fired[1][0].summary
    assert fired[1][0].severity == "warn"


def test_relinking_lets_the_next_lapse_be_raised():
    fired = ticks([
        [obs("mail.linked", False, "gone")],
        [obs("mail.linked", True, "back")],
        [obs("mail.linked", False, "gone again")],
    ])

    assert [len(f) for f in fired] == [1, 0, 1]


# ====================================================================
# And the noise that was crowding all of it out
# ====================================================================


def test_a_port_that_flickers_is_never_mentioned():
    """148 of 210 notables were "an app opened a port"."""
    fired = ticks([
        [obs("network.listening_ports", ["80/nginx"])],
        [obs("network.listening_ports", ["80/nginx", "57621/Spotify"])],
        [obs("network.listening_ports", ["80/nginx"])],
        [obs("network.listening_ports", ["80/nginx"])],
    ])

    assert [len(f) for f in fired] == [0, 0, 0, 0]


def test_a_port_that_stays_is_mentioned_once():
    fired = ticks([
        [obs("network.listening_ports", ["80/nginx"])],
        [obs("network.listening_ports", ["80/nginx", "3389/svchost"])],
        [obs("network.listening_ports", ["80/nginx", "3389/svchost"])],
        [obs("network.listening_ports", ["80/nginx", "3389/svchost"])],
    ])

    assert [len(f) for f in fired] == [0, 0, 1, 0]


# ====================================================================
# A broken collector must never take the brain down with it
# ====================================================================


def test_a_collector_that_throws_does_not_stop_the_others():
    class Broken:
        name = "broken"

        def safe_collect(self):
            return []

    perception = Perception(collectors=[
        Broken(),
        Scripted([[obs("world.overdue", ["Rent"])]]),
    ])

    assert perception.sense()[0]


def test_speech_is_on_by_default():
    """A brain that never speaks is a monitoring daemon with a nice voice."""
    import os

    from src.config import _get_bool

    was = os.environ.pop("ALFRED_BRAIN_SPEAK_PROACTIVE", None)
    try:
        assert _get_bool("ALFRED_BRAIN_SPEAK_PROACTIVE", True) is True
    finally:
        if was is not None:
            os.environ["ALFRED_BRAIN_SPEAK_PROACTIVE"] = was
