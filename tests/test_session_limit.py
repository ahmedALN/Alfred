"""The 1008s were the model, not Alfred.

Three theories, two of them mine and wrong: the context window filling,
then something in the config. What settled it was a bare idle session
with none of Alfred's code in it, which ended at 150 seconds with the
same error. Alfred's own sessions ended at 150, 152 and 151.

So it is a cap on this preview model, and reconnecting through it is
correct behaviour. Calling it a dropped connection filled the log with
alarm about the one thing working as intended, and buried real faults
among it.
"""

from src.ai.gemini import _SESSION_LIMIT, _is_session_limit


class _Aborted(Exception):
    def __str__(self):
        return "APIError: 1008 None. The operation was aborted."


def test_a_session_reaching_its_age_is_not_a_fault():
    assert _is_session_limit(150.0, _Aborted()) is True
    assert _is_session_limit(152.0, _Aborted()) is True


def test_a_session_that_dies_early_still_is_one():
    """Eleven seconds in is not the cap - something is wrong."""
    assert _is_session_limit(11.0, _Aborted()) is False


def test_a_different_error_at_the_same_age_is_still_a_fault():
    assert _is_session_limit(150.0, RuntimeError("connection reset")) is False


def test_the_limit_is_what_was_measured():
    assert _SESSION_LIMIT == 150.0
