"""Putting the machine to sleep, and working out how to do new things."""

from __future__ import annotations

import pytest

from src.brain.design import Impossible, design
from src.messaging.masking import mask, scrub
from src.tools.power import PowerTool
from src.tools.registry import ToolRegistry


# ------------------------------------------------------------------- power


@pytest.fixture()
def power():
    ran: list[str] = []
    return PowerTool(run=ran.append), ran


def test_sleeping_the_pc_actually_runs_the_command(power):
    """Asked to sleep the PC, Alfred said it was doing it and did not.

    There was no power tool at all - it had to compose a shell line and
    hope, twice, the second time with the exact command handed to it.
    """
    tool, ran = power
    out = tool.execute({"action": "sleep"})

    assert out["status"] == "success"
    assert ran == ["rundll32.exe powrprof.dll,SetSuspendState 0,1,0"]


def test_sleep_does_not_secretly_hibernate(power):
    """The first argument to SetSuspendState is "hibernate".

    Passing 1 there on a machine with hibernation enabled writes RAM to
    disk instead of suspending, which is not what anybody means.
    """
    tool, ran = power
    tool.execute({"action": "sleep"})
    assert ran[0].endswith("SetSuspendState 0,1,0")


def test_the_words_people_actually_use(power):
    tool, ran = power
    for said in ("suspend", "reboot", "power off", "log out"):
        tool.execute({"action": said, "confirm": True})
    assert ran == [
        "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "shutdown /r /t 0",
        "shutdown /s /t 0",
        "shutdown /l",
    ]


def test_losing_unsaved_work_is_asked_about_first(power):
    """Sleeping costs nothing. Shutting down closes what is open."""
    tool, ran = power
    for costly in ("shutdown", "restart", "signout"):
        out = tool.execute({"action": costly})
        assert out["status"] == "needs_confirmation", costly
    assert ran == []


def test_confirmed_it_goes_ahead(power):
    tool, ran = power
    assert tool.execute({"action": "shutdown", "confirm": True})["status"] == "success"
    assert ran == ["shutdown /s /t 0"]


def test_sleeping_and_locking_need_no_permission(power):
    """You lose nothing, and undoing it is a keypress."""
    tool, ran = power
    assert tool.execute({"action": "sleep"})["status"] == "success"
    assert tool.execute({"action": "lock"})["status"] == "success"


def test_an_action_it_does_not_have(power):
    tool, _ = power
    assert tool.execute({"action": "explode"})["status"] == "error"


def test_there_is_something_to_say_before_the_screen_goes(power):
    """This answer is the last thing out before the machine stops."""
    tool, _ = power
    assert "sleep" in tool.execute({"action": "sleep"})["said"].lower()


# ------------------------------------------------------------------ design


class _Designer:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def generate(self, *_a, **_k) -> str:
        return self.answer


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    reg.register(PowerTool(run=lambda _c: None))
    return reg


def test_a_tool_that_does_not_exist_is_refused(registry):
    """A model asked for a plan will cheerfully invent `sleep_pc`.

    A skill whose first step names a tool that is not there is worse
    than no skill: it fails later, further from the cause, and it fails
    every single time.
    """
    with pytest.raises(Impossible) as caught:
        design("sleep", _Designer('{"steps":[{"tool":"sleep_pc","args":{}}]}'),
               registry)
    assert "sleep_pc" in str(caught.value)


def test_arguments_are_checked_against_the_real_schema(registry):
    with pytest.raises(Impossible) as caught:
        design("sleep", _Designer(
            '{"steps":[{"tool":"power","args":{"mode":"sleep"}}]}'), registry)
    assert "mode" in str(caught.value)


def test_a_missing_required_argument_is_caught(registry):
    with pytest.raises(Impossible):
        design("sleep", _Designer('{"steps":[{"tool":"power","args":{}}]}'),
               registry)


def test_an_empty_or_enormous_routine_is_refused(registry):
    with pytest.raises(Impossible):
        design("x", _Designer('{"steps":[]}'), registry)

    many = ",".join(['{"tool":"power","args":{"action":"lock"}}'] * 12)
    with pytest.raises(Impossible):
        design("x", _Designer('{"steps":[' + many + "]}"), registry)


def test_a_designer_that_does_not_answer_in_json(registry):
    with pytest.raises(Impossible):
        design("x", _Designer("just press the power button"), registry)


def test_it_may_say_the_goal_cannot_be_reached(registry):
    with pytest.raises(Impossible) as caught:
        design("make tea", _Designer('{"impossible":"there is no kettle"}'),
               registry)
    assert "kettle" in str(caught.value)


def test_json_wrapped_in_a_code_fence_is_still_read(registry):
    fenced = (
        "Here you go:\n```json\n"
        '{"steps":[{"tool":"power","args":{"action":"lock"}}],'
        '"template":"lock the screen"}\n```'
    )
    skill = design("lock my screen", _Designer(fenced), registry)
    assert skill["steps"] == [{"tool": "power", "args": {"action": "lock"}}]


def test_a_designed_skill_is_never_trusted_like_a_run_one(registry):
    """It is a plan that has never been executed.

    Distilled skills come from something that demonstrably worked.
    This came from a model being confident, which is not the same.
    """
    skill = design("lock my screen", _Designer(
        '{"steps":[{"tool":"power","args":{"action":"lock"}}],'
        '"template":"lock the screen"}'), registry)

    assert skill["unconfirmed"] == 1
    assert skill["success"] == 0
    assert skill["confidence"] < 0.5


def test_the_variable_part_becomes_a_slot(registry):
    skill = design("lock the screen", _Designer(
        '{"steps":[{"tool":"power","args":{"action":"{p0}"}}],'
        '"template":"{p0} the machine"}'), registry)
    assert skill["params"] == ["p0"]


def test_an_empty_goal_is_not_designed_for(registry):
    with pytest.raises(Impossible):
        design("   ", _Designer("{}"), registry)


# ----------------------------------------------------------------- masking


def test_the_owners_number_is_not_written_down_in_full():
    """It was printed on every start, into a log that gets pasted about."""
    assert mask("+447435589157") == "+44...9157"
    assert "7435589" not in mask("+447435589157")


def test_enough_is_kept_to_tell_two_numbers_apart():
    assert mask("+447435589157") != mask("+447435580000")


def test_a_line_is_scrubbed_wherever_the_number_sits():
    line = "[Message] WhatsApp linked to +447435589157 - your own chat."
    assert "589157" not in scrub(line)
    assert "WhatsApp linked to" in scrub(line)


def test_things_that_are_not_phone_numbers_are_left_alone():
    assert mask("") == ""
    assert mask("1234") == "1234"
