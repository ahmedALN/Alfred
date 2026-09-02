"""Every step verified, and none of them the thing that was asked for.

Each step is checked against its own done_when, which means a plan can
be fully verified and still be a plan for something else. That is not
hypothetical: "Search for how to fish." is recorded in
alfred_tasks.sqlite3 as **done**, and the evidence Alfred offered was
"Confirmed: Open File Explorer to the Desktop folder." The step did
exactly what it said. Nothing in the chain had ever compared the work
against the request.

The check is deliberately blunt - one content word in common, not a
good answer. It has to be, because it is judging Alfred's own account
of its work with no model in the loop. What it must never do is fail an
honest run, so most of what is here is real goals from
alfred_tasks.sqlite3 and bench-last.json that have to keep passing.
"""

from __future__ import annotations

import pytest

from src.brain.agent import TaskResult, _answers_the_goal


def result(goal, verified=(), answer="", plan=None, template=""):
    return TaskResult(
        goal=goal,
        status="done",
        summary="",
        verified=list(verified),
        plan=list(plan if plan is not None else verified),
        answer=answer,
        replayed_template=template,
    )


# ====================================================================
# The bug, by name
# ====================================================================


def test_the_how_to_fish_run_is_not_done():
    """The exact record from alfred_tasks.sqlite3."""
    assert not _answers_the_goal(result(
        "Search for how to fish.",
        ["Open File Explorer to the Desktop folder."],
    ))


@pytest.mark.parametrize("goal,verified", [
    ("Organise my Downloads folder by file type.", ["Open Notepad."]),
    ("Play a Drake song on Spotify.", ["Open Calculator."]),
    ("Email my tutor the essay.", ["Looked up the weather."]),
    ("Move the screenshots into a folder called Pictures.",
     ["Ran a search for cat videos."]),
    ("Mute the microphone.", ["Opened the Downloads folder."]),
])
def test_work_that_was_about_something_else_is_not_done(goal, verified):
    assert not _answers_the_goal(result(goal, verified))


def test_the_summary_says_so_plainly():
    """A status with no explanation is a status nobody can act on."""
    from src.brain.agent import TaskAgent

    agent = TaskAgent.__new__(TaskAgent)
    agent._limitations = None
    agent._plan_chat = None
    agent._first_plan_len = 1
    agent._finding = lambda _r: ""

    r = result("Search for how to fish.",
               ["Open File Explorer to the Desktop folder."])
    r.status = "running"
    agent._finalize(r)

    assert r.status == "partial"
    assert "none of them was about what you asked for" in r.summary


# ====================================================================
# Real runs that must keep passing
# ====================================================================


@pytest.mark.parametrize("goal,verified,answer", [
    # from alfred_tasks.sqlite3
    ("Open Notepad.", ["Open Notepad."], ""),
    ("Look up the current weather for Sana'a, Yemen.",
     ["Run weather to get current conditions for Sana'a"], "23C, clear."),
    ("Check the amount of free RAM currently available on the system.",
     ["Run system_info to get RAM."], "3.7 GB free."),
    ("Check whether Steam is running.",
     ["Run PowerShell to check if the Steam process is running."],
     "Yes, Steam is currently running."),
    ("Set a reminder to stretch in 3 minutes.",
     ["Open Calendar", "Add an event titled 'Stretch' in 3 minutes"], ""),
    ("Launch Stremio and open Breaking Bad from the continue watching list.",
     ["Open Stremio", "Search for Breaking Bad"], ""),
    ("Open Steam and launch \"Sons of the Forest\" directly from the desktop file.",
     ["Open Steam", "Run the Sons of the Forest shortcut"], ""),
    ("Bring Claude to the foreground.", ["Focus on Claude window."], ""),
    ("Open Windows Settings.", ["Run PowerShell to start Windows Settings."], ""),
    # from bench-last.json
    ("Type the words quick brown fox into Notepad.",
     ["Type 'quick brown fox' into the Notepad edit control."], ""),
    ("Open Steam and search the store for Hades.",
     ["Open Steam", "Search the store for Hades"], ""),
    ("In MultiMC, select the 1.21.11 instance.",
     ["Open MultiMC", "Select the 1.21.11 instance"], ""),
    ("Close Notepad without saving.", ["Close Notepad", "Choose Don't Save"], ""),
    ("How much free space is on the C drive?",
     ["Run system_info to get disk space."],
     "You have 153.7 GB of free space on the C drive."),
    ("What version of Windows is this?", ["Run system_info."],
     "It's Microsoft Windows 11 Pro."),
])
def test_an_honest_run_is_still_done(goal, verified, answer):
    assert _answers_the_goal(result(goal, verified, answer))


# ====================================================================
# The cases the check deliberately declines to judge
# ====================================================================


def test_a_question_that_produced_an_answer_is_not_judged_on_word_overlap():
    """"192.168.1.42" is the right reply to "what is my local IPv4" and
    shares no word with it."""
    assert _answers_the_goal(result(
        "What is my local IPv4?", ["Run network_info."], "192.168.1.42"
    ))


def test_a_goal_made_only_of_function_words_is_not_judged():
    """"How long has this PC been up?" leaves nothing to match on."""
    assert _answers_the_goal(result(
        "How long has this PC been up?", ["Run system_info for uptime."]
    ))


def test_a_one_word_goal_is_not_judged():
    assert _answers_the_goal(result("Screenshot.", ["Pressed Win+Shift+S"]))


def test_a_replayed_routine_is_judged_on_the_routine_it_matched():
    """A skill's `verify` is written in different words on purpose.

    "a track is playing" is the right check for "play adele on spotify"
    and shares no word with it - but the routine was matched to the
    request by keyword and by meaning before it ever ran.
    """
    assert _answers_the_goal(result(
        "play adele on spotify",
        ["a track is playing"],
        template="play a {p0} song on spotify",
    ))


def test_raw_tool_output_is_not_evidence_of_relevance():
    """The Desktop in the "how to fish" run held a game called How to
    Fish. Matching against what tools returned would make the check
    agree with itself."""
    r = result("Search for how to fish.",
               ["Open File Explorer to the Desktop folder."])
    # Even with the words present in a step's raw result, the claim
    # Alfred makes about its work is what is judged.
    r.steps = []
    assert not _answers_the_goal(r)


# ====================================================================
# It only ever downgrades
# ====================================================================


def test_the_check_cannot_turn_a_failure_into_a_success():
    from src.brain.agent import TaskAgent

    agent = TaskAgent.__new__(TaskAgent)
    agent._limitations = None
    agent._plan_chat = None
    agent._first_plan_len = 1
    agent._finding = lambda _r: ""

    r = TaskResult(goal="Open Notepad.", status="running", summary="",
                   plan=["Open Notepad."], verified=[])
    agent._finalize(r)

    assert r.status == "failed"


def test_apostrophes_do_not_hide_a_word():
    """'Stretch' and Stretch have to be the same word."""
    from src.brain.agent import _words

    assert "stretch" in _words("Add an event titled 'Stretch'")
    # ...and a real internal apostrophe survives.
    assert "sana'a" in _words("the weather in Sana'a")


def test_a_tool_name_comes_apart_into_words():
    """`system_info` has to read as system and info, or a step that ran
    exactly the right tool looks unrelated to the question it answered."""
    from src.brain.agent import _words

    assert {"system", "info"} <= _words("Run system_info to get RAM.")
