"""The bill, and the two ways of getting it wrong in your own favour."""

from __future__ import annotations

import json

from src import costs


def _shape(**kw):
    base = dict(
        name="x", tasks=0, speaking_minutes=0, listening_minutes=0,
        photos=0, awake_hours=0,
    )
    base.update(kw)
    return costs.Shape(**base)


def test_a_task_is_costed_as_the_seven_calls_it_actually_is():
    """The first version costed a task as one model call.

    A task is a plan, three executor steps, a verification and a
    reflection. Costing it as one call quietly under-read the bill by
    most of its size, in the direction that makes the answer nicer.
    """
    one = costs.day_cost(_shape(tasks=1))["thinking"]
    assert one > costs.calls_cost(1) * 5


def test_alfred_costs_money_on_a_day_you_never_speak_to_it():
    """The brain thinks every 90 seconds whether or not you are there."""
    idle = costs.day_cost(_shape(awake_hours=24))
    assert idle["idling"] > 0
    assert idle["thinking"] == 0
    # And it scales with how long it is left running, which is the one
    # dial that changes it.
    half = costs.day_cost(_shape(awake_hours=12))["idling"]
    assert abs(half * 2 - idle["idling"]) < 1e-9


def test_talking_costs_more_than_listening():
    """Audio out is the expensive direction; the model should show it."""
    day = costs.day_cost(_shape(speaking_minutes=10, listening_minutes=10))
    assert day["talking"] > day["hearing"] * 3


def test_nothing_at_all_costs_nothing():
    assert sum(costs.day_cost(_shape()).values()) == 0


def test_the_recorded_days_are_read_as_they_were_billed(tmp_path):
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps({
        "2026-08-31": {
            "requests": 100, "input_tokens": 1_000_000,
            "output_tokens": 100_000, "errors": {"plan_failover:a": 7},
        },
        "2026-09-01": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
    }), encoding="utf-8")

    days = costs.observed(usage)
    # The empty day is not a day.
    assert [d for d, _, _ in days] == ["2026-08-31"]
    _, calls, cost = days[0]
    assert calls == 100
    # 1M in at 0.30 + 0.1M out at 2.50
    assert abs(cost - (0.30 + 0.25)) < 1e-9
    assert costs.failovers(usage) == 7


def test_a_missing_usage_file_is_not_a_crash(tmp_path):
    assert costs.observed(tmp_path / "nope.json") == []
    assert costs.failovers(tmp_path / "nope.json") == 0


def test_it_runs():
    assert costs.main([]) == 0
