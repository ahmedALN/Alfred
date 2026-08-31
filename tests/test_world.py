"""What is going on in your life, as opposed to on your machine.

Alfred remembered a hundred and twenty-seven things. Sixty-four were
about the computer, sixty-two were lessons about its own tools, and one
was about the person it works for: "user likes to listen to Drake on
Spotify". So when it spoke unprompted it talked about disk space,
because disk space was all it knew.
"""

from datetime import datetime, timedelta

from src.brain.world import Matter, World, _name_of, refresh
from src.brain.worldwatch import WorldCollector
from src.tools.world_tool import WorldTool

NOW = datetime(2026, 9, 1, 14, 30)


def _world(tmp_path):
    return World(tmp_path / "w.sqlite3")


def _due(world, name, days, source="classroom"):
    world.note(Matter(kind="due", name=name, due=NOW + timedelta(days=days),
                      source=source), NOW)


# ------------------------------------------------------- holding it


def test_a_deadline_is_kept_and_comes_back(tmp_path):
    world = _world(tmp_path)
    _due(world, "Physics problem set", 3)

    assert world.due_soon(7, NOW)[0]["name"] == "Physics problem set"


def test_something_late_is_told_apart_from_something_coming(tmp_path):
    world = _world(tmp_path)
    _due(world, "Essay", -2)
    _due(world, "Lab report", 3)

    assert [m["name"] for m in world.overdue(NOW)] == ["Essay"]
    assert "Lab report" in [m["name"] for m in world.due_soon(7, NOW)]


def test_the_same_thing_twice_is_one_thing_that_matters_more(tmp_path):
    world = _world(tmp_path)
    _due(world, "Essay", 3)
    _due(world, "Essay", 3)

    open_now = world.all_open()
    assert len(open_now) == 1
    assert open_now[0]["weight"] == 2


def test_something_finished_stops_being_on_the_books(tmp_path):
    world = _world(tmp_path)
    _due(world, "Essay", 3)

    world.settle(world.all_open()[0]["id"], "done")

    assert world.due_soon(7, NOW) == []


def test_what_has_gone_quiet_is_dropped(tmp_path):
    """A picture of now, not an archive. Something nobody has mentioned
    in a month with no deadline left is not part of your life."""
    world = _world(tmp_path)
    world.note(Matter(kind="doing", name="an old project", source="you"),
               NOW - timedelta(days=60))

    assert world.forget_stale(30, NOW) == 1
    assert world.all_open() == []


def test_a_future_deadline_is_not_dropped_for_being_quiet(tmp_path):
    world = _world(tmp_path)
    world.note(Matter(kind="due", name="Dissertation",
                      due=NOW + timedelta(days=40), source="you"),
               NOW - timedelta(days=60))

    assert world.forget_stale(30, NOW) == 0


# --------------------------------------------------------- saying it


def test_the_brief_leads_with_what_is_late(tmp_path):
    """Ordered by what would worry a person."""
    world = _world(tmp_path)
    _due(world, "Lab report", 3)
    _due(world, "Essay", -2)

    brief = world.brief(NOW)

    assert brief.index("Overdue") < brief.index("Due soon")
    assert "Essay" in brief


def test_it_says_when_in_words_a_person_uses(tmp_path):
    world = _world(tmp_path)
    _due(world, "Thing", 1)

    assert "tomorrow" in world.brief(NOW)


def test_an_empty_life_says_nothing_rather_than_an_empty_heading(tmp_path):
    assert _world(tmp_path).brief(NOW) == ""


# ------------------------------------------------------- gathering it


class _Classroom:
    def due(self, days=14, now=None):
        return [
            {"course": "Physics", "title": "Problem set 3",
             "due_iso": "2026-09-04T23:59", "handed_in": False},
            {"course": "Physics", "title": "Reading",
             "due_iso": "2026-09-02T23:59", "handed_in": True},
        ]


class _Mail:
    def unread(self, limit=10):
        return [{"from": "Sam Green <sam@example.com>", "subject": "Thursday?"}]


class _Activity:
    def today(self, now=None):
        return [{"app": "Code", "seconds": 7200},
                {"app": "Discord", "seconds": 300}]


def test_coursework_already_handed_in_is_not_hanging_over_you(tmp_path):
    world = _world(tmp_path)

    refresh(world, classroom=_Classroom(), now=NOW)

    assert [m["name"] for m in world.all_open()] == ["Problem set 3"]


def test_the_people_who_wrote_to_you_are_people(tmp_path):
    world = _world(tmp_path)

    refresh(world, mail=_Mail(), now=NOW)

    assert world.of_kind("person")[0]["name"] == "Sam Green"


def test_an_hour_of_your_day_is_a_thing_you_are_doing(tmp_path):
    """Ten minutes is not."""
    world = _world(tmp_path)

    refresh(world, activity=_Activity(), now=NOW)

    assert [m["name"] for m in world.of_kind("doing")] == ["Code"]


def test_one_bad_source_does_not_stop_the_others(tmp_path):
    class _Broken:
        def due(self, **kw):
            raise RuntimeError("classroom is down")

    world = _world(tmp_path)

    refresh(world, classroom=_Broken(), mail=_Mail(), now=NOW)

    assert world.of_kind("person")


def test_an_address_becomes_a_name():
    assert _name_of("Sam Green <sam@example.com>") == "Sam Green"
    assert _name_of("sam.green@example.com") == "Sam Green"
    assert _name_of("") == ""


# --------------------------------------------- the brain can see it


def test_the_brain_is_told_what_is_late(tmp_path):
    world = _world(tmp_path)
    _due(world, "Essay", -2)

    seen = WorldCollector(world, wallclock=lambda: NOW).collect()

    assert any(o.key == "world.overdue" for o in seen)
    assert "Essay" in seen[0].summary


def test_it_reports_the_names_not_the_count(tmp_path):
    """"2 things overdue" is the same sentence every day and stops being
    news. A new name in the list is the actual event."""
    world = _world(tmp_path)
    _due(world, "Essay", -2)

    value = WorldCollector(world, wallclock=lambda: NOW).collect()[0].value

    assert value == ["Essay"]


def test_a_quiet_life_gives_the_brain_nothing_to_say(tmp_path):
    assert WorldCollector(_world(tmp_path), wallclock=lambda: NOW).collect() == []


def test_the_sources_are_not_hammered(tmp_path):
    """Every ninety seconds would be rude to Google and would tell us
    nothing - deadlines move on the scale of hours at best."""
    calls = []
    clock = [1000.0]
    collector = WorldCollector(
        _world(tmp_path), refresh=lambda: calls.append(1),
        clock=lambda: clock[0], wallclock=lambda: NOW,
    )

    for _ in range(10):
        collector.collect()
        clock[0] += 90

    assert len(calls) == 1


# ---------------------------------------------------------- the tool


def test_you_can_tell_it_something_matters(tmp_path):
    world = _world(tmp_path)
    tool = WorldTool(world, now=lambda: NOW)

    answer = tool.execute({
        "action": "remember", "kind": "due",
        "name": "Dissertation draft", "when": "friday at 17:00",
    })

    assert answer["status"] == "success"
    assert world.due_soon(7, NOW)[0]["name"] == "Dissertation draft"


def test_you_can_say_something_is_finished(tmp_path):
    world = _world(tmp_path)
    _due(world, "Essay", 3)
    tool = WorldTool(world, now=lambda: NOW)

    assert tool.execute({"action": "done", "name": "essay"})["status"] == "success"
    assert world.due_soon(7, NOW) == []


def test_asking_what_is_on_gets_the_short_version(tmp_path):
    world = _world(tmp_path)
    _due(world, "Essay", -1)
    tool = WorldTool(world, now=lambda: NOW)

    answer = tool.execute({"action": "whats_on"})

    assert "Essay" in answer["brief"]
    assert answer["overdue"][0]["name"] == "Essay"
