"""Alfred died and stayed up. Both halves of that are bugs."""

from __future__ import annotations

import pathlib

from src.ai.gemini import _is_connection_error


def test_a_closed_session_is_transport_not_a_fault():
    """The exact exception that killed Alfred.

    Gemini answered one reconnect with "1011 service unavailable". The
    loop carried on, spawned voice tasks against a session that was no
    longer there, and _receive raised this. It was not classified as a
    connection error, so it propagated out of run_forever, out of
    main(), and took WhatsApp, the brain, the task queue and the
    interface with it - for a voice hiccup.
    """
    assert _is_connection_error(
        RuntimeError("Alfred Live session is not connected.")
    )


def test_transient_transport_failures_are_all_reconnectable():
    for exc in (
        ConnectionResetError("reset"),
        TimeoutError(),
        OSError("network unreachable"),
        RuntimeError("session is not connected"),
    ):
        assert _is_connection_error(exc), exc


def test_a_real_bug_is_still_allowed_to_be_fatal():
    """Reconnecting through a TypeError would hide it for ever."""
    for exc in (
        TypeError("unsupported operand"),
        KeyError("missing"),
        ValueError("bad argument"),
    ):
        assert not _is_connection_error(exc), exc


def test_a_failed_reconnect_retries_instead_of_falling_through():
    """It used to print the failure and continue the outer loop.

    The next pass then used a session that was not there. The retry has
    to happen where the failure did.
    """
    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "ai" / "gemini.py"
    ).read_text(encoding="utf-8")

    after = source.split("reconnect failed:", 1)[1][:900]
    assert "while consecutive_failures" in after, (
        "a failed reconnect must keep trying, not fall through to using "
        "the dead session"
    )


def test_the_process_exits_when_alfred_stops_being_alfred():
    """A corpse that holds the port is worse than a crash.

    main() ended, but the WhatsApp library's non-daemon thread kept the
    interpreter from finishing. The interface still served pages and
    the port still answered while the brain had not ticked for
    twenty-four minutes - so the watchdog, which only watches whether
    the process is alive, saw nothing wrong.
    """
    source = (
        pathlib.Path(__file__).resolve().parent.parent / "src" / "main.py"
    ).read_text(encoding="utf-8")

    entry = source.split('if __name__ == "__main__":', 1)[1]
    assert "os._exit" in entry, (
        "main must force the process down so the watchdog can restart it"
    )
    # And it must do so however main() ended.
    assert "finally:" in entry


# ------------------------------------------------- claiming false success


def test_a_refusal_is_not_erased_by_an_unrelated_success():
    """Alfred said it had learned to make a cup of tea.

    The skill tool refused, correctly: "Making a cup of tea is a
    physical task that requires physical actions in the real world,
    which these digital Windows tools cannot do." The executor then
    called `skill list`, that succeeded, and the verifier accepted it.
    The task reported "Confirmed: Learn a routine for making a cup of
    tea."

    A tool saying the goal cannot be reached is a statement about the
    goal. Other calls succeeding afterwards do not change it.
    """
    from src.brain.agent import Step, _refused_in

    refused = Step(
        1, "", "skill", {"action": "learn"}, "auto",
        {"status": "error",
         "error": "Making a cup of tea is a physical task that requires "
                  "physical actions in the real world, which these digital "
                  "Windows tools cannot do."},
        False,
    )
    listed = Step(2, "", "skill", {"action": "list"}, "auto",
                  {"status": "success", "count": 39}, True)

    assert _refused_in([refused, listed]), (
        "a different action of the same tool succeeding is not an answer "
        "to the refusal"
    )


def test_a_refusal_that_was_actually_answered_is_let_go():
    """Refused once, then made to work, is not a refusal."""
    from src.brain.agent import Step, _refused_in

    refused = Step(1, "", "skill", {"action": "learn"}, "auto",
                   {"error": "cannot be done that way"}, False)
    worked = Step(2, "", "skill", {"action": "learn"}, "auto",
                  {"status": "success"}, True)

    assert not _refused_in([refused, worked])


def test_an_ordinary_failure_is_not_a_refusal():
    """A control that was not found is a bad attempt, not a verdict."""
    from src.brain.agent import Step, _refused_in

    missed = Step(1, "", "ui_control", {"action": "click"}, "auto",
                  {"error": "no control matches ref=None name='Save'"}, False)
    assert not _refused_in([missed])
