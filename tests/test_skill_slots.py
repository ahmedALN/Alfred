"""A parameter at the end of a sentence is where parameters usually are.

"Open Steam and {p0} for {p1}." was learned from a real request and then
failed on the spot, no steps run, whenever it was asked again - because
"{p1}." with the sentence's full stop attached was not recognised as a
slot. It was looked for as the literal text "{p1}.", never found, and
the value never filled. Every skill whose last word is its parameter was
broken the same way, which is most of the ones worth having.
"""

from src.brain.skills import align


def test_a_parameter_at_the_end_of_a_sentence_is_filled():
    filled = align(
        "Open Steam and {p0} for {p1}.",
        "Open Steam and search the store for Celeste.",
    )

    assert filled == {"p0": "search the store", "p1": "Celeste"}


def test_the_value_does_not_keep_the_sentence_s_full_stop():
    """The name of the game is "Celeste", not "Celeste."."""
    filled = align("Open {app}.", "Open Discord.")

    assert filled == {"app": "Discord"}


def test_a_request_without_the_full_stop_works_too():
    filled = align(
        "Open Steam and {p0} for {p1}.", "Open Steam and look for Hades"
    )

    assert filled == {"p0": "look", "p1": "Hades"}


def test_a_slot_in_the_middle_still_works():
    filled = align(
        "In MultiMC, select the {v} instance.",
        "In MultiMC, select the 1.20.1 instance.",
    )

    assert filled == {"v": "1.20.1"}


def test_a_multi_word_value_is_kept_whole():
    filled = align("Open {app}.", "Open Visual Studio Code.")

    assert filled == {"app": "Visual Studio Code"}


def test_something_unrelated_still_does_not_match():
    assert align("Open Steam and {p0} for {p1}.", "What time is it") is None
