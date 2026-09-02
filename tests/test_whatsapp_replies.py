"""Three things that went wrong on a real evening of using Alfred."""

from __future__ import annotations

import asyncio

import pytest

from src.messaging.reply import _read, _trim, wants_picture


# --------------------------------------------------- the model thinking out loud


# Both of these were sent to a phone, word for word.
LEAKED_SCREENSHOT = (
    'Right - " For screenshot request, we should use SHOW: picture. But we '
    "also need to bring Claude to foreground before taking screenshot. "
    "The DO: is for"
)
LEAKED_CHECKING = (
    "continue checking? But the job is already checking. The instruction: "
    '"Answer with ONE line, starting with SAY.'
)


def test_the_models_reasoning_is_never_forwarded():
    """A marker found anywhere made the reasoning around it the reply.

    Worse, when that marker was DO: the same paragraph was submitted as
    the task goal, so Alfred set off to do a sentence about itself.
    """
    assert _read(LEAKED_SCREENSHOT) == ("unclear", "")
    assert _read(LEAKED_CHECKING) == ("unclear", "")


def test_a_marker_mentioned_in_passing_is_not_an_instruction():
    thinking = (
        "I think the user probably wants a picture so I should use "
        "SHOW: picture here"
    )
    assert _read(thinking) == ("unclear", "")


def test_the_format_it_was_asked_for_still_works():
    assert _read("SAY: Evening. What do you need?") == (
        "say", "Evening. What do you need?"
    )
    assert _read("DO: Open Steam. || Opening Steam now.") == (
        "do", "Open Steam. || Opening Steam now."
    )
    assert _read("SHOW: picture") == ("show", "picture")
    assert _read("STEER: use the other one") == ("steer", "use the other one")


def test_a_marker_on_its_own_line_after_a_preamble_is_read():
    """Models often say a sentence and then answer properly."""
    assert _read("Let me think.\nSAY: Here you go.") == ("say", "Here you go.")


def test_light_wrapping_is_tolerated():
    assert _read("Answer: SAY: hello") == ("say", "hello")
    assert _read('**SAY:** hello') == ("say", "hello")
    assert _read('"SHOW: picture"') == ("show", "picture")


def test_a_body_that_runs_into_another_marker_is_cut():
    assert _trim("picture. But we also need DO: something") == "picture. But we also need"


def test_an_empty_answer_is_not_a_crash():
    assert _read("") == ("say", "")
    assert _read(None) == ("say", "")


# ------------------------------------------------------- asking for the screen


def test_asking_for_a_screenshot_does_not_depend_on_a_model():
    """Alfred said "Here is your screenshot" five times and sent none.

    The model kept answering SAY instead of SHOW, and once one of those
    was in the history it copied itself - every later reply promised a
    picture that never came.
    """
    for asked in (
        "Send me a screenshot of my pc",
        "screenshot of my pc",
        "Screenshot",
        "send me a screengrab",
        "show me the screen",
        "can i see my desktop",
    ):
        assert wants_picture(asked) == "picture", asked


def test_asking_about_the_screen_is_still_a_question():
    """"What is on my screen" wants words back, not an image."""
    for asked in (
        "whats on my screen",
        "what is on my screen right now",
        "is steam on my screen",
    ):
        assert wants_picture(asked) == "", asked


def test_recording_is_told_apart_from_a_still():
    assert wants_picture("record my screen 10s") == "clip"
    assert wants_picture("send me a clip of my screen") == "clip"
    assert wants_picture("record the meeting notes") == ""


def test_ordinary_talk_is_not_a_screenshot_request():
    for asked in ("open steam", "how are you", "hello", "cancel the task"):
        assert wants_picture(asked) == "", asked


# --------------------------------------------- answering where you were asked


def test_a_job_from_whatsapp_is_not_read_aloud_in_the_room():
    """Asking from a phone made Alfred announce the answer out loud.

    The announcement went to all three places every time, so a message
    typed in bed was read to an empty house.
    """
    from src.brain.tasks import _safe_speak

    heard = []

    async def speak(text, source=""):
        heard.append((text, source))

    asyncio.run(_safe_speak(speak, "done", "whatsapp"))
    assert heard == [("done", "whatsapp")]


def test_an_announcer_that_does_not_want_the_source_still_works():
    """Nothing should break because it takes one argument."""
    from src.brain.tasks import _safe_speak

    heard = []

    async def old_style(text):
        heard.append(text)

    asyncio.run(_safe_speak(old_style, "done", "whatsapp"))
    assert heard == ["done"]


def test_a_failing_announcer_does_not_take_the_task_with_it():
    from src.brain.tasks import _safe_speak

    async def broken(text, source=""):
        raise RuntimeError("no channel")

    asyncio.run(_safe_speak(broken, "done", "voice"))   # must not raise


# ------------------------------------------------ telling it to stop


from src.messaging.reply import Conversation, forbids, is_stop


class _NeverAsked:
    def generate(self, *_a, **_k):
        raise AssertionError(
            "a prohibition must never reach the model - it inverted one"
        )


def _talker(running="Search the web for how to fish.", **kw):
    jobs, steers, cancels = [], [], []
    talk = Conversation(
        _NeverAsked(),
        lambda goal: jobs.append(goal),
        steer=lambda t: (steers.append(t), True)[1],
        running=lambda: running,
        cancel=lambda: cancels.append(True),
        **kw,
    )
    return talk, jobs, steers, cancels


def test_do_not_do_x_never_becomes_do_x():
    """The real one, from a real evening.

    Told "stop that, do not search for how to fish", the model dropped
    the negation and answered DO: Open a browser and search for how to
    fish. A task by that name was created twenty-seven seconds after
    the person said not to, and Alfred fetched the Steam page.
    """
    talk, jobs, _steers, cancels = _talker()

    talk.handle("stop that, do not search for how to fish")

    assert jobs == [], "a prohibition became a job"
    assert cancels, "the running job should have been stopped"


def test_a_bare_stop_stops_the_running_job():
    talk, jobs, _s, cancels = _talker()
    reply = talk.handle("stop")
    assert cancels and jobs == []
    assert "stop" in reply.lower()


def test_stopping_when_nothing_runs_says_so():
    talk, jobs, _s, cancels = _talker(running="")
    reply = talk.handle("cancel that")
    assert jobs == [] and cancels == []
    assert "nothing running" in reply.lower()


def test_a_prohibition_while_working_is_a_correction():
    talk, jobs, steers, cancels = _talker()
    talk.handle("don't use the browser for that")
    assert jobs == [] and cancels == []
    assert steers, "it should have steered the running job"


def test_a_prohibition_with_nothing_running_starts_nothing():
    talk, jobs, _s, cancels = _talker(running="")
    reply = talk.handle("don't open steam")
    assert jobs == [] and cancels == []
    assert "won't" in reply.lower() or "not" in reply.lower()


def test_stopping_a_thing_is_not_stopping_the_job():
    """"stop the music" is an instruction about Spotify."""
    assert is_stop("stop the music") is False
    assert is_stop("stop spotify") is False
    assert is_stop("stop that") is True
    assert is_stop("stop what you are doing") is True


def test_ordinary_requests_are_not_mistaken_for_prohibitions():
    for said in ("open steam", "open notepad instead of wordpad",
                 "no not that one", "what is the weather"):
        assert not forbids(said), said
        assert not is_stop(said), said


def test_a_fact_to_keep_is_not_a_routine_to_build():
    """"remember that I hate coriander" became a task reading

        Learn a routine for remembering that the user hates coriander.

    A routine is a sequence of actions. A dislike is not one. The rule
    teaching Alfred that "learn how to x" means build a skill was too
    greedy about the word "remember", and swallowed the plainest way
    there is to tell it something about yourself.
    """
    from src.brain.agent import _PLAN_SYSTEM
    from src.messaging.reply import _SYSTEM

    for prompt in (_SYSTEM, _PLAN_SYSTEM):
        assert "Remember THAT x" in prompt
        assert "coriander" in prompt          # the example, spelled out
        # And the skill rule is explicitly about HOW TO, not remember.
        assert "Learn HOW TO x" in prompt
