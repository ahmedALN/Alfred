"""Window titles are not regexes.

Alfred could not close a Notepad with unsaved changes. The title was
"*HelloHello - Notepad" and every attempt came back "window not found:
nothing to repeat at position 0" - the leading asterisk, Notepad's mark
for unsaved work, being read as a regex quantifier with nothing to
repeat. Which is to say: the moment a document was worth closing
carefully, it became unaddressable.
"""

from src.windows.uia import _is_deliberate_pattern, title_pattern


def _matches(pattern: str, title: str) -> bool:
    import re

    return bool(re.search(pattern, title))


# --------------------------------------------------- titles that broke it


def test_an_unsaved_document_can_be_named():
    """The one that failed. An invalid pattern is a title."""
    title = "*HelloHello - Notepad"

    assert _matches(title_pattern(title), title)


def test_a_second_explorer_window_can_be_named():
    """"Document (2)" compiles, and as a regex it matches "Document 2" -
    everything except the window it is the name of."""
    title = "Document (2) - Word"

    assert _matches(title_pattern(title), title)


def test_a_terminal_titled_with_a_path_can_be_named():
    title = r"C:\WINDOWS\system32\cmd.exe"

    assert _matches(title_pattern(title), title)


def test_a_question_in_a_dialog_title_can_be_named():
    title = "Save changes?"

    assert _matches(title_pattern(title), title)


def test_a_plain_title_still_matches_loosely():
    assert _matches(title_pattern("Notepad"), "Untitled - Notepad")


def test_matching_ignores_case_as_it_always_did():
    assert _matches(title_pattern("notepad"), "Untitled - Notepad")


# ------------------------------------------------- deliberate patterns


def test_something_that_is_plainly_a_pattern_is_still_treated_as_one():
    assert _is_deliberate_pattern(".*Notepad") is True


def test_a_title_that_cannot_compile_is_never_a_pattern():
    assert _is_deliberate_pattern("*Hello - Notepad") is False


def test_plain_words_are_never_a_pattern():
    assert _is_deliberate_pattern("Notepad") is False
    assert _is_deliberate_pattern("") is False
