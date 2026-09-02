"""Reading the error before deciding what it means."""

from __future__ import annotations

from src.brain.diagnose import diagnose, recognise, supported


class _Chat:
    def __init__(self, answer: str = "") -> None:
        self.answer = answer
        self.asked = 0

    def generate(self, *_a, **_k) -> str:
        self.asked += 1
        return self.answer


# ------------------------------------------------------- known shapes


def test_the_common_walls_are_named_without_asking_a_model():
    """Most failures are one of a dozen shapes.

    Recognising them outright is cheaper than a model call and, more
    to the point, cannot hallucinate a cause.
    """
    chat = _Chat("CAUSE: something\nINSTEAD: something else")

    found = diagnose(
        "ui_control", {"action": "click"},
        {"error": "no control matches ref=None name='Save'"},
        chat=chat,
    )
    assert found.certain
    assert "accessibility tree" in found.cause
    assert "tree" in found.suggestion
    assert chat.asked == 0          # never went near the model


def test_a_wall_that_will_not_move_is_marked_as_such():
    """Retrying an access denial twice more is two wasted minutes."""
    found = recognise("Access is denied. (0x80070005)")
    assert found is not None
    assert found.fatal is True
    assert "do not retry" in found.suggestion.lower()


def test_the_confirmation_dead_end_is_recognised():
    """The exact shape behind "he said he was doing it and didn't"."""
    found = recognise("power (sleep the PC) - needed your OK and there was "
                      "no way to ask from here")
    assert found is not None
    assert found.fatal is True


def test_a_missing_argument_is_told_apart_from_a_missing_control():
    a = recognise("'app' must be a non-empty string")
    b = recognise("no control matches ref=None name='Play'")
    assert a and b
    assert a.cause != b.cause


def test_an_unknown_error_asks_once_and_believes_the_answer():
    chat = _Chat("CAUSE: the disk is full\nINSTEAD: free some space first")
    found = diagnose("powershell", {}, {"error": "0x80070070 unexpected"},
                     chat=chat)
    assert chat.asked == 1
    assert found.cause == "the disk is full"
    assert found.suggestion == "free some space first"
    assert found.certain is False


def test_with_no_model_it_says_the_error_rather_than_inventing_one():
    found = diagnose("powershell", {}, {"error": "0x80070070 unexpected"},
                     chat=None)
    assert "0x80070070" in found.cause
    assert found.certain is False


def test_a_failure_with_no_message_is_admitted_to():
    found = diagnose("web", {}, {}, chat=None)
    assert "without saying why" in found.cause


def test_the_error_is_found_wherever_the_tool_put_it():
    """PowerShell uses stderr, others use error, message or reason."""
    for key in ("error", "stderr", "message", "reason", "detail"):
        found = diagnose("x", {}, {key: "Access is denied"}, chat=None)
        assert found.fatal, key


# --------------------------------------------------- unsupported lessons


# The real one. It was written into memory from a trace that read
# "skill(...) -> FAILED" with no error text at all, and it is false:
# the skill tool has supported both actions since the day it existed.
INVENTED = ("The skill tool does not support list or learn actions; skills "
            "must be invoked directly or discovered through available "
            "environment documentation.")


def test_a_lesson_the_evidence_does_not_support_is_not_kept():
    """Sixty-nine of these are in memory, each one permanent.

    A wrong lesson is worse than no lesson: it is never revisited, and
    it stops Alfred attempting that thing again.
    """
    assert supported(INVENTED, errors=[]) is False
    assert supported(INVENTED, errors=["needed your OK and there was no "
                                       "way to ask from here"]) is False
    # But if the error really does say so, it is a fair thing to learn.
    assert supported(INVENTED, errors=["action must be one of ['run']"]) is True


def test_a_lesson_that_matches_the_error_is_kept():
    assert supported(
        "PowerShell cannot stop the Snipping Tool when it is not running",
        errors=["Stop-Process : Cannot find a process with the name "
                "'SnippingTool'"],
    ) is True


def test_a_lesson_that_blames_needs_something_to_have_broken():
    """Only lessons that FORECLOSE something need evidence.

    The first version of this gate required it of every lesson, and
    threw away "Use PowerShell Get-ChildItem to locate file paths on
    the desktop" because nothing in that run had failed. Lessons do not
    only come from failures, and a technique note stops Alfred doing
    nothing at all.
    """
    assert supported("PowerShell fails to stop a process that is not "
                     "running", errors=[]) is False

    # No blame, no evidence needed.
    assert supported("Use PowerShell Get-ChildItem to locate file paths "
                     "and shortcuts on the desktop", errors=[]) is True
    assert supported("Spotify search is opened with Ctrl+L", errors=[]) is True


def test_a_claim_that_a_tool_cannot_do_something_must_be_quoted():
    """The dangerous shape, and the only one held to word-for-word proof.

    A generalisation from a real error is the point of asking a model.
    A claim that a tool LACKS a feature is different: it is permanent,
    usually wrong, and stops Alfred reaching for that tool again.
    """
    claim = "The web tool does not support fetching pages"
    assert supported(claim, errors=["Access is denied"]) is False
    assert supported(claim, errors=["action must be one of ['answer']"]) is True


def test_an_ordinary_lesson_is_not_held_to_that_standard():
    """Rejecting real lessons would trade one problem for another."""
    assert supported(
        "Spotify search is opened with Ctrl+L, not a toolbar icon",
        errors=["no control matches name='Search'"],
    ) is True


def test_a_lesson_too_short_to_mean_anything():
    assert supported("nope", errors=["a real error"]) is False
