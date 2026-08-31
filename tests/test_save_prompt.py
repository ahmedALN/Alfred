"""The one prompt where getting it wrong destroys something.

Alfred spent ninety-seven seconds failing to close a Notepad with
unsaved changes. It could see the whole dialog - "Do you want to save
changes to...", Save, Don't save, Cancel - and had no idea the screen
was asking it anything, so it kept trying to close a window that was
waiting for an answer.
"""

from src.windows.screens import assess


class _C:
    def __init__(self, name, control_type="Button"):
        self.name = name
        self.control_type = control_type
        self.is_password = False


def _notepad_closing():
    """What Alfred actually reads off Notepad mid-close."""
    return [
        _C("Notepad", "Text"),
        _C("Do you want to save changes to Untitled?", "Text"),
        _C("Save"), _C("Don't save"), _C("Cancel"),
        _C("Bold (Ctrl+B)"), _C("Settings"), _C("Close"),
        _C("Ln 1, Col 57", "Text"),
    ]


def test_the_prompt_that_stopped_it_is_recognised():
    need = assess("*Untitled - Notepad", _notepad_closing())

    assert need is not None
    assert need.kind == "save_changes"


def test_the_choices_are_offered():
    """Knowing what it is being asked is only half of it - the answer
    has to be one of the buttons that is actually there."""
    need = assess("*Untitled - Notepad", _notepad_closing())

    assert "Save" in need.choices
    assert "Don't save" in need.choices
    assert "Cancel" in need.choices


def test_it_says_what_is_being_asked():
    need = assess("*Untitled - Notepad", _notepad_closing())

    assert "save" in need.question.lower()
    assert "Notepad" in need.question


# ------------------------------------------------------- not everything


def test_a_settings_page_with_a_save_button_is_not_asking_anything():
    """Half of every settings page has a Save button. Matching the word
    would stop Alfred on all of them."""
    page = [_C("Save"), _C("Cancel"), _C("Theme"), _C("Font size")]

    assert assess("Settings", page) is None


def test_a_document_that_mentions_saving_is_not_a_prompt():
    text = [_C("remember to save changes often", "Text"), _C("File")]

    assert assess("notes.txt - Notepad", text) is None


def test_the_other_wordings_are_caught_too():
    for words in (
        "Would you like to save your changes?",
        "You have unsaved changes.",
        "Save changes to document.txt?",
    ):
        need = assess("App", [_C(words, "Text"), _C("Save"), _C("Discard")])
        assert need is not None and need.kind == "save_changes", words
