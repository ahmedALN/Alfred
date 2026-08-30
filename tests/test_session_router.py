"""Which desktop do the input/capture tools act on?"""

import src.windows.session_router as sr
from src.windows.child_session import ChildSessionError
from src.windows.session_router import SessionRouter


class FakeClient:
    def __init__(self, target, alive=True):
        self.target = target
        self.alive = alive
        self.closed = False
        self.connects = 0

    def connect(self):
        self.connects += 1
        if not self.alive:
            raise ChildSessionError(f"no agent for {self.target}")

    def ping(self):
        if not self.alive:
            raise ChildSessionError("pipe dead")
        return {"ok": True}

    def close(self):
        self.closed = True


def _patch(monkeypatch, alive_targets=("current", "child")):
    made = {}

    def factory(target="current"):
        client = FakeClient(target, alive=target in alive_targets)
        made.setdefault(target, []).append(client)
        return client

    monkeypatch.setattr(sr, "ChildSessionClient", factory)
    return made


def test_defaults_to_the_users_desktop(monkeypatch):
    made = _patch(monkeypatch)
    r = SessionRouter()
    assert r.target == "current" and not r.isolated
    assert r.client().target == "current"


def test_switches_to_the_isolated_session(monkeypatch):
    _patch(monkeypatch)
    r = SessionRouter()
    r.use_isolated()
    assert r.isolated
    assert r.client().target == "child"


def test_switches_back(monkeypatch):
    _patch(monkeypatch)
    r = SessionRouter()
    r.use_isolated()
    r.use_users_desktop()
    assert not r.isolated
    assert r.client().target == "current"


def test_connections_are_reused(monkeypatch):
    made = _patch(monkeypatch)
    r = SessionRouter()
    first = r.client()
    second = r.client()
    assert first is second
    assert len(made["current"]) == 1  # only one was ever built


def test_a_dead_connection_is_replaced(monkeypatch):
    made = _patch(monkeypatch)
    r = SessionRouter()
    first = r.client()
    first.alive = False          # the agent went away
    second = r.client()
    assert second is not first
    assert first.closed


def test_falls_back_to_the_users_desktop_when_isolation_is_unavailable(
    monkeypatch,
):
    """A degraded result the user can see beats a task that fails."""
    _patch(monkeypatch, alive_targets=("current",))
    r = SessionRouter()
    r.use_isolated()
    assert r.client().target == "current"


def test_close_releases_every_connection(monkeypatch):
    made = _patch(monkeypatch)
    r = SessionRouter()
    r.client()
    r.use_isolated()
    r.client()
    r.close()
    assert all(c.closed for group in made.values() for c in group)
