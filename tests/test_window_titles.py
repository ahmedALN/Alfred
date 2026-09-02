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


# ------------------------------------- waiting for what is not there


class _Session:
    """Only the bits wait_ready touches."""

    def __init__(self, script):
        from src.windows.uia import UiaSession

        self._script = list(script)
        self.looks = 0
        self.wait_ready = UiaSession.wait_ready.__get__(self)

    def tree(self, title_re=None, pid=None):
        from src.windows.uia import UiaError

        self.looks += 1
        item = self._script.pop(0) if self._script else self._script_last
        self._script_last = item
        if item == "gone":
            raise UiaError("window not found")
        return None, [object()] * item


def test_it_stops_waiting_for_a_window_that_is_not_there():
    """The executor often waits on a title it remembers from earlier
    that has since closed. Twenty-five seconds of nothing."""
    session = _Session(["gone", "gone", "gone", "gone", "gone"])

    assert session.wait_ready("*Gone - Notepad", timeout=25) is False
    assert session.looks == 2


def test_an_app_still_painting_is_still_waited_for():
    """A window that exists but has not finished drawing is the case
    this was written for."""
    session = _Session([0, 1, 2, 9])

    assert session.wait_ready("Notepad", timeout=25, min_controls=3) is True
    assert session.looks == 4


def test_a_window_that_appears_late_is_still_caught():
    """One miss is not proof of absence - an app mid-launch has no
    window for a moment."""
    session = _Session(["gone", 9])

    assert session.wait_ready("Notepad", timeout=25, min_controls=3) is True


# --------------------------- when two parts of Windows disagree


from src.windows.uia import _title_score


def test_a_title_longer_than_the_window_reports_still_finds_it():
    """Windows does not agree with itself about what a window is
    called. One enumeration says "Console window for 1.21.11" and
    another "Console window for 1.21.11 - MultiMC 5" - and anything
    working from the second could not address the window at all."""
    short = "Console window for 1.21.11"
    long = "Console window for 1.21.11 - MultiMC 5"

    assert _title_score(short, long) > 0


def test_the_real_containment_still_wins():
    """Asking for less than the title is a stronger match than asking
    for more."""
    contains = _title_score("Untitled - Notepad", "Notepad")
    contained = _title_score("Notepad", "Untitled - Notepad")

    assert contains > contained > 0


def test_a_tiny_title_does_not_match_everything():
    """A window called "a" is inside almost any request, and matching
    it would be worse than matching nothing."""
    assert _title_score("a", "open the calculator") == 0


def test_a_whole_word_beats_a_fragment_in_a_path():
    """This rule was written with word boundaries that had been
    corrupted into literal backspace characters, so it had never once
    fired - a fragment buried in a path scored the same as a name."""
    word = _title_score("Steam", "steam")
    fragment = _title_score("C:/upsteamed/thing.txt", "steam")

    assert word > fragment > 0


# ====================================================================
# A window whose title changed out from under Alfred
# ====================================================================


def test_a_title_that_no_longer_matches_scores_nothing():
    """Spotify is "Spotify Premium" until it plays something, and then
    it is "Shababs - Drake". Alfred read the title, wrote it down, came
    back a minute later to map the window, and was told it did not
    exist - while it sat on screen the whole time."""
    from src.windows.uia import _title_score

    assert _title_score("Shababs - Drake", "Spotify Premium") == 0


def test_the_program_behind_a_window_is_read_without_its_extension():
    import os

    from src.windows.uia import _process_name

    assert _process_name(os.getpid()) == "python"


def test_a_pid_that_is_not_there_is_empty_rather_than_an_error():
    from src.windows.uia import _process_name

    assert _process_name(999_999_999) == ""
    assert _process_name(0) == ""


def test_the_program_lookup_is_cached():
    """A window lookup walks every top-level window; asking the OS about
    the same processes each time is what turns a fallback into a reason
    not to have one."""
    import os

    from src.windows.uia import _PROCESS_NAMES, _process_name

    _PROCESS_NAMES.clear()
    _process_name(os.getpid())

    assert os.getpid() in _PROCESS_NAMES


class _Win:
    def __init__(self, title, pid):
        self._title = title
        self._pid = pid

    def window_text(self):
        return self._title

    def process_id(self):
        return self._pid

    @property
    def handle(self):
        return self._pid


class _Desktop:
    def __init__(self, windows):
        self._windows = windows

    def windows(self):
        return self._windows


def test_a_renamed_window_is_found_by_its_program(monkeypatch):
    """The fix, end to end: no title matches, so ask what the window IS
    rather than what it is currently called."""
    import src.windows.uia as uia

    monkeypatch.setattr(
        uia, "_process_name", lambda pid: {101: "Spotify", 102: "explorer"}.get(pid, "")
    )

    session = uia.UiaSession()
    desktop = _Desktop([_Win("Shababs - Drake", 101), _Win("Downloads", 102)])

    found = session._best_window(desktop, "Spotify Premium")

    assert found is not None
    assert found.window_text() == "Shababs - Drake"


def test_a_matching_title_still_wins(monkeypatch):
    """The fallback is a fallback - it must not take over from a title
    that matches perfectly well."""
    import src.windows.uia as uia

    monkeypatch.setattr(uia, "_process_name", lambda pid: "Spotify")

    session = uia.UiaSession()
    desktop = _Desktop([_Win("Shababs - Drake", 101), _Win("Spotify Premium", 102)])

    found = session._best_window(desktop, "Spotify Premium")

    assert found.window_text() == "Spotify Premium"


def test_nothing_matching_is_still_nothing(monkeypatch):
    import src.windows.uia as uia

    monkeypatch.setattr(uia, "_process_name", lambda pid: "explorer")

    session = uia.UiaSession()
    desktop = _Desktop([_Win("Downloads", 102)])

    assert session._best_window(desktop, "Spotify Premium") is None
