"""The "without disturbing me" routing, and cleaning up afterwards."""

from src.brain.isolation import strip_isolation_phrase, wants_isolation
from src.windows.isolated_desktop import IsolatedDesktop


# ---------------------------------------------------------------- trigger


def test_recognises_the_ways_people_ask_for_isolation():
    for phrase in [
        "without disturbing me, open Spotify",
        "open Spotify without disturbing me",
        "don't disturb me, tidy my downloads",
        "check my disk space in the background",
        "do this on your own desktop: open Notepad",
        "sort my files while I keep working",
        "open the browser without taking over my screen",
    ]:
        assert wants_isolation(phrase), phrase


def test_ordinary_requests_are_not_isolated():
    for phrase in [
        "open Spotify and play something",
        "what is my ip address",
        "tell me about the disturbance in the force",
    ]:
        assert not wants_isolation(phrase), phrase


def test_the_phrase_is_stripped_wherever_it_sits():
    cases = {
        "without disturbing me, open Spotify and play something":
            "open Spotify and play something",
        "open Spotify and play something without disturbing me":
            "open Spotify and play something",
        "in the background, check my disk space":
            "check my disk space",
        "do this on your own desktop: open Notepad":
            "open Notepad",
        "please, without disturbing me, tidy my downloads":
            "tidy my downloads",
        "could you, in the background, back up my notes":
            "back up my notes",
    }
    for said, expected in cases.items():
        assert strip_isolation_phrase(said) == expected, said


def test_a_bare_phrase_never_strips_to_nothing():
    """The planner must always get a goal, even from a useless request."""
    assert strip_isolation_phrase("without disturbing me").strip()
    assert strip_isolation_phrase("").strip() == ""


def test_untouched_when_there_is_no_phrase():
    assert strip_isolation_phrase("open Spotify") == "open Spotify"


# ---------------------------------------------------------------- cleanup


class FakeClient:
    """Stands in for the agent living in the isolated session."""

    def __init__(self, apps):
        self._apps = list(apps)
        self.closed = []

    def list_apps(self):
        return list(self._apps)

    def close_apps(self, pids, force=False):
        self.closed.extend(pids)
        self._apps = [a for a in self._apps if a["pid"] not in pids]
        return {"closed": list(pids), "failed": []}

    def close(self):
        pass


def _desktop(apps, baseline, launched=()):
    d = IsolatedDesktop()
    client = FakeClient(apps)
    d.client = lambda: client            # type: ignore[method-assign]
    d.apps = client.list_apps            # type: ignore[method-assign]
    type(d).running = property(lambda self: True)
    d._baseline = set(baseline)
    d._launched = list(launched)
    return d, client


def test_cleanup_closes_only_what_appeared_after_alfred_started():
    apps = [
        {"pid": 1, "name": "explorer"},      # was already there
        {"pid": 2, "name": "WindowsTerminal"},  # was already there
        {"pid": 9, "name": "Notepad"},       # Alfred opened this
    ]
    d, client = _desktop(apps, baseline={1, 2})
    result = d.cleanup()

    assert result["closed"] == [9]
    assert client.closed == [9]


def test_cleanup_catches_apps_that_handed_off_to_another_process():
    """Notepad and Store apps are single-instance: the pid we launched
    exits and a different one owns the window. Tracking pids alone misses
    it - the baseline diff must catch it."""
    apps = [
        {"pid": 1, "name": "explorer"},
        {"pid": 77, "name": "Notepad"},   # NOT the pid launch reported
    ]
    d, client = _desktop(apps, baseline={1}, launched=[42])
    result = d.cleanup()

    assert 77 in result["closed"]


def test_cleanup_leaves_everything_alone_when_nothing_is_new():
    apps = [{"pid": 1, "name": "explorer"}, {"pid": 2, "name": "dwm"}]
    d, client = _desktop(apps, baseline={1, 2})
    result = d.cleanup()

    assert result["closed"] == []
    assert client.closed == []


def test_close_everything_also_clears_the_baseline_clutter():
    apps = [{"pid": 1, "name": "explorer"}, {"pid": 5, "name": "Discord"}]
    d, client = _desktop(apps, baseline={1, 5})
    result = d.cleanup(close_everything=True)

    assert set(result["closed"]) == {1, 5}


def test_cleanup_is_safe_with_no_session():
    d = IsolatedDesktop()
    type(d).running = property(lambda self: False)
    result = d.cleanup()
    assert result["closed"] == [] and "no session" in result["note"]
