"""Who is in your life, and what time it is - two things Alfred had wrong."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.brain.world import Matter, World, _is_a_person, refresh
from src.mail.gmail import _is_bulk
from src.workspace.calendar import _aware


# ---------------------------------------------------------------- calendar


def test_a_naive_time_is_given_an_offset_before_google_sees_it():
    """Google answers 400 to a timestamp with no offset.

    Every caller reaches for datetime.now(), which is naive, so the
    read side had never once worked - and returned no error anybody
    saw, just an empty diary.
    """
    fixed = _aware(datetime(2026, 9, 1, 1, 52))
    assert fixed.tzinfo is not None
    assert fixed.isoformat() != datetime(2026, 9, 1, 1, 52).isoformat()


def test_a_time_that_already_knows_its_offset_is_left_alone():
    already = datetime(2026, 9, 1, 1, 52, tzinfo=timezone.utc)
    assert _aware(already) is already


# -------------------------------------------------------------------- mail


def test_gmails_own_sorting_is_believed():
    assert _is_bulk({}, ["INBOX", "CATEGORY_PROMOTIONS"])
    assert _is_bulk({}, ["CATEGORY_UPDATES"])
    assert not _is_bulk({}, ["INBOX", "UNREAD"])


def test_a_sender_who_offers_to_stop_emailing_is_not_a_friend():
    assert _is_bulk({"list-unsubscribe": "<https://x/u>"}, ["INBOX"])


# ------------------------------------------------------------------ people


def test_shops_that_set_no_headers_are_still_not_people():
    assert not _is_a_person("QUICK & EASY NHS PRESCRIPTIONS 31/08/2026 <a@b.c>")
    assert not _is_a_person("UNiDAYS <no@unidays.com>")
    assert not _is_a_person("noreply@github.com")
    assert not _is_a_person("notifications@slack.com")


def test_the_rule_is_shy_because_a_dropped_person_never_returns():
    assert _is_a_person("Sara Ahmed <sara@gmail.com>")
    assert _is_a_person("Dan at Young Professionals <dan@yp.com>")
    assert _is_a_person("mum@hotmail.com")
    # No display name at all is not evidence of anything.
    assert _is_a_person("h.mahmood@work.co.uk")


# ------------------------------------------------------------- end to end


class _Inbox:
    def unread(self, limit=10):
        return [
            {"from": "Uber Eats <no@ubereats.com>", "subject": "50% off",
             "bulk": True},
            {"from": "UNiDAYS <hi@unidays.com>", "subject": "Reverify",
             "bulk": False},
            {"from": "Sara Ahmed <sara@gmail.com>", "subject": "sunday?",
             "bulk": False},
        ]


def test_the_picture_of_your_life_holds_people_and_not_brands(tmp_path):
    world = World(tmp_path / "w.sqlite3")
    refresh(world, mail=_Inbox())

    names = {m["name"] for m in world.of_kind("person")}
    assert names == {"Sara Ahmed"}


def test_a_deadline_that_has_passed_stops_being_part_of_your_life(tmp_path):
    world = World(tmp_path / "w.sqlite3")
    now = datetime(2026, 9, 1, 12, 0)
    world.note(Matter(kind="due", name="old essay", source="classroom",
                      due=now - timedelta(days=40)), now - timedelta(days=40))
    assert world.forget_stale(days=30, now=now) == 1
    assert world.all_open() == []


def test_the_world_is_a_picture_of_now_and_actually_forgets(tmp_path):
    """forget_stale existed from day one and nothing ever called it.

    Every passed deadline and every person who went quiet stayed for
    good, so the brief that makes Alfred proactive slowly filled with
    things that had stopped being true.
    """
    from src.main import _refresh_world_and_forget

    world = World(tmp_path / "w.sqlite3")
    long_ago = datetime(2026, 1, 1, 12, 0)

    world.note(Matter(kind="person", name="Someone Forgotten",
                      source="mail"), long_ago)
    world.note(Matter(kind="due", name="an essay from January",
                      source="classroom", due=long_ago), long_ago)
    assert len(world.all_open()) == 2

    # A refresh with no sources at all: nothing new arrives, and the
    # stale things should still go.
    _refresh_world_and_forget(world, None, None, None, None)

    assert world.all_open() == []


def test_forgetting_does_not_touch_something_still_due(tmp_path):
    from src.main import _refresh_world_and_forget

    world = World(tmp_path / "w.sqlite3")
    old_but_pending = datetime.now() + timedelta(days=5)

    world.note(Matter(kind="due", name="a deadline next week",
                      source="calendar", due=old_but_pending),
               datetime.now() - timedelta(days=90))

    _refresh_world_and_forget(world, None, None, None, None)

    # Quiet for ninety days, but it has not happened yet.
    assert [m["name"] for m in world.all_open()] == ["a deadline next week"]
