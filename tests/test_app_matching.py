"""Opening the thing that was asked for, not one sharing a preposition."""

from __future__ import annotations

from src.windows.apps import _meaningful


def test_common_words_do_not_identify_an_app():
    """"how to fish" and "Click to Do" share only the word "to".

    That was enough to match: any single shared token won. Alfred tried
    to launch a Windows AI feature instead of the game on the desktop,
    and the user got "Click to Do isn't available on this device".
    """
    assert _meaningful("how to fish") == {"how", "fish"}
    assert _meaningful("Click to Do") == {"do", "click"}
    assert not _meaningful("how to fish") & _meaningful("Click to Do")


def test_the_words_that_do_identify_it_survive():
    assert "spotify" in _meaningful("Spotify")
    assert _meaningful("Visual Studio Code") == {"visual", "studio", "code"}
    # Decoration around a real name is dropped, the name is not.
    assert _meaningful("How to Fish.exe - Shortcut") == {"how", "fish"}


def test_punctuation_is_not_part_of_a_name():
    assert _meaningful("VLC media player") == {"vlc", "media", "player"}
    assert _meaningful("") == set()
