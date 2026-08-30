"""Which desktop does Alfred act on? The pipe name decides."""

import src.windows.child_session as cs
from src.windows.child_session import ChildSessionClient


def _fake_sessions(monkeypatch, current=2, child=6):
    monkeypatch.setattr(cs, "current_session_id", lambda: current)
    monkeypatch.setattr(cs, "child_session_id", lambda: child)


def test_current_targets_the_users_own_desktop(monkeypatch):
    _fake_sessions(monkeypatch)
    assert ChildSessionClient("current").PIPE_NAME.endswith(".s2")


def test_child_targets_the_isolated_session(monkeypatch):
    _fake_sessions(monkeypatch)
    assert ChildSessionClient("child").PIPE_NAME.endswith(".s6")


def test_an_explicit_session_id_wins(monkeypatch):
    _fake_sessions(monkeypatch)
    assert ChildSessionClient(9).PIPE_NAME.endswith(".s9")


def test_child_falls_back_to_current_when_none_is_running(monkeypatch):
    """Asking for isolation when there is no child session must still
    work - Alfred degrades to the user's desktop rather than failing."""
    _fake_sessions(monkeypatch, current=2, child=None)
    assert ChildSessionClient("child").PIPE_NAME.endswith(".s2")


def test_default_target_is_the_current_session(monkeypatch):
    _fake_sessions(monkeypatch)
    assert ChildSessionClient().PIPE_NAME.endswith(".s2")


def test_unknown_session_falls_back_to_the_legacy_name(monkeypatch):
    monkeypatch.setattr(cs, "current_session_id", lambda: None)
    monkeypatch.setattr(cs, "child_session_id", lambda: None)
    assert ChildSessionClient("child").PIPE_NAME == ChildSessionClient.PIPE_BASE


def test_targets_produce_distinct_pipes(monkeypatch):
    """The whole point: two agents can coexist and be addressed apart."""
    _fake_sessions(monkeypatch)
    assert (ChildSessionClient("current").PIPE_NAME
            != ChildSessionClient("child").PIPE_NAME)
