"""A session that outlives its context window, and comes back to itself.

The 1008 aborts were read as a flaky socket for a long time. They were
not: a Live session ends when its context window fills, and an always-on
assistant fills one quickly - the microphone streams whether or not
anybody is speaking, and all of it is transcribed into the same window.
Every one of those ends also silently cost Alfred the conversation.
"""

from src.ai.gemini import AlfredLiveSession


def _config_of(session):
    return AlfredLiveSession._config(session)


class _Bare:
    """Just enough of a session to build a config from."""

    _resume_handle = ""

    def _tool_declarations(self):
        return []

    def _system_instruction(self):
        return "be helpful"


def test_the_session_is_allowed_to_outlive_its_context_window():
    config = _config_of(_Bare())

    assert config.context_window_compression is not None
    assert config.context_window_compression.sliding_window is not None


def test_a_reconnect_asks_to_resume_rather_than_start_over():
    config = _config_of(_Bare())

    assert config.session_resumption is not None


def test_the_first_connection_has_no_handle_to_offer():
    assert _config_of(_Bare()).session_resumption.handle is None


def test_a_handle_the_server_gave_us_is_offered_back():
    """Otherwise every drop costs the whole conversation."""
    resuming = _Bare()
    resuming._resume_handle = "handle-from-the-server"

    assert _config_of(resuming).session_resumption.handle == \
        "handle-from-the-server"
