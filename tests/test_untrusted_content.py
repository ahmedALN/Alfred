"""Text somebody else wrote must arrive as data, not as orders.

Alfred can open programs, click, type and run shell commands. Anything
that reaches its reasoning from outside - a web page, an email, a
teacher's announcement, a calendar invitation, or words visible on the
screen - is written by someone who is not the user, and a sentence in
one of those should never be able to drive the desktop.
"""

from __future__ import annotations

import pathlib

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "src" / "tools"

# Every tool that returns text Alfred did not write itself.
CARRIES_OTHER_PEOPLES_WORDS = [
    "web.py",
    "mail_tool.py",
    "classroom_tool.py",
    "calendar_tool.py",
    "computer_screenshot.py",
]


@pytest.mark.parametrize("filename", CARRIES_OTHER_PEOPLES_WORDS)
def test_it_says_the_content_is_data(filename: str):
    source = (TOOLS / filename).read_text(encoding="utf-8")
    assert "_UNTRUSTED" in source, (
        f"{filename} hands Alfred text written by someone else without "
        "saying it is data"
    )
    assert '"instruction": _UNTRUSTED' in source, (
        f"{filename} defines the warning but never attaches it to a result"
    )


@pytest.mark.parametrize("filename", CARRIES_OTHER_PEOPLES_WORDS)
def test_the_warning_actually_says_the_useful_thing(filename: str):
    """A warning that does not name the behaviour is decoration."""
    source = (TOOLS / filename).read_text(encoding="utf-8")
    block = source.split("_UNTRUSTED = (", 1)[1].split(")", 1)[0].lower()

    assert "data" in block and "instruction" in block

    # It has to say what to DO when the content tries to give an order.
    # Either refusing outright or - better, and what the mail tool
    # does - refusing and telling the user what was attempted.
    acts = ("ignore", "do not do it", "never act on it", "tell the user")
    assert any(word in block for word in acts), (
        "it must say what to do about an instruction, not just that the "
        "text is data"
    )


def test_the_screen_is_included_and_was_the_one_that_was_missed():
    """Marked last, and the most dangerous of them.

    Mail and the web were guarded from the start. A page open in a
    browser was not - so "what is on my screen" fed whatever a website
    had written straight into the executor's history, on a machine
    where Alfred has the whole desktop.
    """
    source = (TOOLS / "computer_screenshot.py").read_text(encoding="utf-8")
    assert "whole desktop" in source
