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


# ====================================================================
# Finding a thing is not opening it
# ====================================================================


class _Screen:
    """A registry that answers ui_control windows and nothing else."""

    def __init__(self, titles):
        self.titles = list(titles)
        self.asked = 0

    def execute(self, name, args):
        assert name == "ui_control"
        self.asked += 1
        return {
            "status": "success",
            "windows": [{"title": t} for t in self.titles],
        }


def _verifier(titles):
    from src.brain.agent import TaskAgent

    agent = TaskAgent.__new__(TaskAgent)
    agent._registry = _Screen(titles)
    agent._VERIFY_STOP = TaskAgent._VERIFY_STOP
    return agent


def test_a_window_that_is_not_there_is_a_definite_no():
    """The real run: "Open research.txt from my Desktop." Alfred ran
    ui_control find, saw the filename inside File Explorer's listing,
    opened nothing, and reported done. The words matched; the window
    was never there; nothing had looked."""
    agent = _verifier(["Desktop - File Explorer", "Alfred"])

    ok, why = agent._deterministic_verify(
        "research.txt is open in a text editor",
        ["ui_control({'action': 'find'}) -> ok: found research.txt"],
    )

    assert ok is False
    assert "nothing was opened" in why


def test_a_window_that_is_there_is_a_definite_yes():
    agent = _verifier(["research.txt - Notepad", "Alfred"])

    ok, why = agent._deterministic_verify("research.txt is open", [])

    assert ok is True
    assert "research.txt - Notepad" in why


def test_it_looks_for_a_program_by_name_too():
    agent = _verifier(["Spotify Premium"])

    ok, _why = agent._deterministic_verify("the Spotify window is open", [])
    assert ok is True

    agent = _verifier(["Desktop - File Explorer"])
    ok, _why = agent._deterministic_verify("the Spotify window is open", [])
    assert ok is False


def test_an_unreadable_screen_is_not_read_as_an_empty_one():
    """No windows at all means the reading failed, not that the
    desktop is bare - and a failed reading must not become a verdict."""

    class _Broken:
        def execute(self, name, args):
            raise RuntimeError("the accessibility layer is down")

    from src.brain.agent import TaskAgent

    agent = TaskAgent.__new__(TaskAgent)
    agent._registry = _Broken()
    agent._VERIFY_STOP = TaskAgent._VERIFY_STOP

    assert agent._deterministic_verify("Notepad is open", []) is None


def test_a_done_when_naming_nothing_is_left_to_the_model():
    agent = _verifier(["Notepad"])

    assert agent._deterministic_verify("the window is open", []) is None


def test_a_step_that_is_not_about_opening_does_not_go_looking():
    agent = _verifier(["Notepad"])

    agent._deterministic_verify("the total is written down", [])

    assert agent._registry.asked == 0


# ====================================================================
# Asked a question, done means answered
# ====================================================================


def _finalized(goal, verified, answer, steps=()):
    from src.brain.agent import TaskAgent, TaskResult

    agent = TaskAgent.__new__(TaskAgent)
    agent._limitations = None
    agent._plan_chat = None
    agent._first_plan_len = len(verified)
    agent._finding = lambda _r: answer

    r = TaskResult(
        goal=goal, status="running", summary="",
        plan=list(verified), verified=list(verified), steps=list(steps),
    )
    agent._finalize(r)
    return r


def test_a_question_with_no_answer_is_not_done():
    """The real run. "Is there a folder on my Desktop, and what is in
    it?" ran four PowerShell calls, every one successful, produced no
    finding at all, and was reported as done - so the user is told the
    question has been looked into, and never what the answer is."""
    r = _finalized(
        "Is there a folder on my Desktop, and what is in it?",
        ["Run PowerShell to list folders on Desktop and their contents"],
        answer="",
    )

    assert r.status == "partial"
    assert any("could not get an answer" in u for u in r.unverified)


def test_a_question_with_an_answer_is_done():
    r = _finalized(
        "Is there a folder on my Desktop, and what is in it?",
        ["Run PowerShell to list folders on Desktop"],
        answer='Yes - "New folder", which is empty.',
    )

    assert r.status == "done"


def test_an_instruction_needs_no_answer_to_be_done():
    """"Open Notepad" is finished when Notepad is open, and has no
    finding to report."""
    r = _finalized("Open Notepad.", ["Open Notepad."], answer="")

    assert r.status == "done"


@pytest.mark.parametrize("goal", [
    "What is on my Desktop?",
    "Is there a folder on my Desktop?",
    "How many files are in Downloads",
    "Are there any .txt files on my Desktop?",
    "which games do I have",
    "Does Steam start with Windows?",
])
def test_these_read_as_questions(goal):
    from src.brain.agent import _was_a_question

    assert _was_a_question(goal)


@pytest.mark.parametrize("goal", [
    "Open Notepad.",
    "Make a folder called Projects on my Desktop.",
    "Close Notepad without saving.",
    "Move the screenshots into a folder.",
    "Play a Drake song on Spotify.",
])
def test_these_read_as_instructions(goal):
    from src.brain.agent import _was_a_question

    assert not _was_a_question(goal)


# ====================================================================
# A question that asks WHAT wants the things, not the count
# ====================================================================


@pytest.mark.parametrize("goal", [
    "What is on my Desktop?",
    "Are there any .txt files on my Desktop?",
    "Is there a folder on my Desktop, and what is in it?",
    "What files are in my Downloads folder?",
    "Which games are on my Desktop?",
    "List the shortcuts on my Desktop",
    "Show me what is in that folder",
    "What apps start automatically with Windows?",
])
def test_a_request_for_the_things_is_recognised(goal):
    """Answered as one short sentence, "what is on my Desktop?" came
    back as "your desktop has 31 items" - a tally of the answer instead
    of the answer, with every name sitting in the tool output."""
    from src.brain.agent import _WANTS_THE_THINGS

    assert _WANTS_THE_THINGS.search(goal)


@pytest.mark.parametrize("goal", [
    "How many files are on my Desktop?",
    "What is the biggest file on my Desktop?",
    "How much space is my Desktop folder using?",
    "What version of Windows is this?",
    "What is my local IP address?",
    "What did I most recently add to my Desktop?",
    "Open Notepad.",
])
def test_a_request_for_one_fact_is_left_alone(goal):
    from src.brain.agent import _WANTS_THE_THINGS

    assert not _WANTS_THE_THINGS.search(goal)
