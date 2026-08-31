"""A parameter at the end of a sentence is where parameters usually are.

"Open Steam and {p0} for {p1}." was learned from a real request and then
failed on the spot, no steps run, whenever it was asked again - because
"{p1}." with the sentence's full stop attached was not recognised as a
slot. It was looked for as the literal text "{p1}.", never found, and
the value never filled. Every skill whose last word is its parameter was
broken the same way, which is most of the ones worth having.
"""

from src.brain.skills import align


def test_a_parameter_at_the_end_of_a_sentence_is_filled():
    filled = align(
        "Open Steam and {p0} for {p1}.",
        "Open Steam and search the store for Celeste.",
    )

    assert filled == {"p0": "search the store", "p1": "Celeste"}


def test_the_value_does_not_keep_the_sentence_s_full_stop():
    """The name of the game is "Celeste", not "Celeste."."""
    filled = align("Open {app}.", "Open Discord.")

    assert filled == {"app": "Discord"}


def test_a_request_without_the_full_stop_works_too():
    filled = align(
        "Open Steam and {p0} for {p1}.", "Open Steam and look for Hades"
    )

    assert filled == {"p0": "look", "p1": "Hades"}


def test_a_slot_in_the_middle_still_works():
    filled = align(
        "In MultiMC, select the {v} instance.",
        "In MultiMC, select the 1.20.1 instance.",
    )

    assert filled == {"v": "1.20.1"}


def test_a_multi_word_value_is_kept_whole():
    filled = align("Open {app}.", "Open Visual Studio Code.")

    assert filled == {"app": "Visual Studio Code"}


def test_something_unrelated_still_does_not_match():
    assert align("Open Steam and {p0} for {p1}.", "What time is it") is None


# ------------------------------------ what belongs in a routine at all

from src.brain.skills import _without_trailing_reads as trim


def _actions(trace):
    return [(t, (a or {}).get("action", "-")) for t, a in trace]


def test_the_checking_it_worked_steps_are_not_part_of_the_routine():
    """A rerun typed "Celeste" into the search box and then read back
    the control called "Hollow Knight" - the literal it was taught
    with. Those steps do nothing on replay but take time and lie."""
    steam = [
        ("open_app", {"name": "Steam"}),
        ("ui_control", {"action": "wait_ready", "window": "Steam"}),
        ("ui_control", {"action": "search", "query": "Hollow Knight"}),
        ("ui_control", {"action": "get", "name": "Hollow Knight"}),
        ("ui_control", {"action": "tree", "contains": "Hollow Knight"}),
    ]

    assert _actions(trim(steam)) == [
        ("open_app", "-"),
        ("ui_control", "wait_ready"),
        ("ui_control", "search"),
    ]


def test_a_routine_whose_whole_job_is_looking_keeps_looking():
    """"What windows are open right now?" is all read, and that is the
    job. Trimming it would leave nothing."""
    assert _actions(trim([("ui_control", {"action": "windows"})])) == [
        ("ui_control", "windows")
    ]


def test_reads_in_the_middle_are_left_alone():
    """Reading the tree before clicking is how the clicking works."""
    trace = [
        ("ui_control", {"action": "tree", "window": "MultiMC"}),
        ("ui_control", {"action": "click", "name": "1.21.11"}),
    ]

    assert _actions(trim(trace)) == _actions(trace)


def test_a_query_routine_is_untouched():
    trace = [("powershell", {"command": "Get-Process"})]

    assert trim(trace) == trace


# ------------------------------ a typo it corrected is not a bad run


from src.brain.agent import Step
from src.brain.skills import stumbled


def _step(tool, ok):
    return Step(1, "", tool, {}, "auto", {}, ok)


def test_a_slip_the_same_tool_corrected_is_not_a_stumble():
    """The executor opens with open_app {"target": "current"} - no app
    named - on nearly every task, is told so, and gets it right next
    call. Counting that as a bad run meant the Steam routine could
    never be learned, however cleanly the actual work went."""
    assert stumbled([_step("open_app", False), _step("open_app", True)]) is False


def test_a_failure_nothing_ever_answered_is_a_stumble():
    """Replaying it means walking into the same wall on purpose."""
    assert stumbled([_step("ui_control", False), _step("open_app", True)]) is True


def test_a_clean_run_is_clean():
    assert stumbled([_step("open_app", True), _step("ui_control", True)]) is False


def test_a_run_that_ends_on_a_failure_is_a_stumble():
    assert stumbled([_step("open_app", True), _step("ui_control", False)]) is True
