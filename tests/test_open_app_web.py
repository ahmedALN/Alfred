"""Opening a web page so the next step can actually find it."""

from src.tools.open_app import _await_window, _is_web, _settled


def test_a_web_address_is_recognised():
    assert _is_web("https://youtube.com/@x")
    assert _is_web("http://example.com")
    assert _is_web("www.example.com")
    assert not _is_web("notepad")
    assert not _is_web(r"C:\Users\me\file.txt")


def test_a_window_still_loading_is_not_settled():
    """A browser window is born "New Tab", then shows the bare address,
    and only then the page's name. The first two are no use to anything
    that has to find it again."""
    url = "https://www.youtube.com/@OfficialDEJI/videos"

    assert not _settled("New Tab - Google Chrome", url)
    assert not _settled("Untitled - Google Chrome", url)
    assert not _settled("youtube.com/@OfficialDEJI/videos - Google Chrome", url)
    assert _settled("DEJI - YouTube - Google Chrome", url)


def test_a_settled_title_is_returned_as_soon_as_it_appears(monkeypatch):
    import src.tools.open_app as mod

    seen = iter([
        {"old"},
        {"old", "New Tab - Google Chrome"},
        {"old", "DEJI - YouTube - Google Chrome"},
    ])
    monkeypatch.setattr(mod, "_await_window", _await_window)
    monkeypatch.setattr("src.windows.toplevel.titles", lambda: next(seen))
    monkeypatch.setattr("time.sleep", lambda _s: None)

    out = _await_window({"old"}, "https://youtube.com/@x", timeout=5)
    assert out == "DEJI - YouTube - Google Chrome"


def test_nothing_appearing_is_reported_as_nothing(monkeypatch):
    monkeypatch.setattr("src.windows.toplevel.titles", lambda: {"old"})
    monkeypatch.setattr("time.sleep", lambda _s: None)

    assert _await_window({"old"}, "https://x.com", timeout=0.01) is None
