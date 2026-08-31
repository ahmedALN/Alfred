"""Checking with your eyes that the thing actually happened.

Verification read a tool log and asked a model whether the log looked
like success. That is a fair check of whether Alfred DID something and
no check at all of whether it WORKED - which is how a screenshot that
was never saved came to be reported as saved, and how a search for one
game could be confirmed by a page showing another.

Alfred has had eyes throughout. It used them to read buttons nobody had
named, and never once to look at the screen and ask whether the last
thing it did had landed.

This is deliberately narrow. It is asked only when the log is not
already conclusive, only for steps whose outcome is something you could
SEE, and it can only ever say "no" - a screen that does not show what
was expected is real evidence of failure, while a screen that does is
not proof the whole step is complete. It cannot rescue a task; it can
stop one lying.
"""

from __future__ import annotations

import re
from typing import Any

# Steps whose success is visible. Anything about a file, a process, a
# port or a registry key is settled better by the tool result that
# already reported it.
_VISIBLE = re.compile(
    r"\b(window|screen|open|opened|shows?|showing|display|visible|"
    r"appears?|page|tab|dialog|button|menu|typed?|text|search(ed)?|"
    r"select(ed)?|click(ed)?|playing|plays)\b",
    re.I,
)

_NOT_VISIBLE = re.compile(
    r"\b(file|folder|directory|path|process|service|port|registry|"
    r"disk|drive|memory|cpu|installed|downloaded|saved to)\b",
    re.I,
)

_ASK = (
    "Look at this screenshot of the user's desktop. The assistant has "
    "just tried to do this:\n\n    {step}\n\nIt should now be true "
    "that:\n\n    {done_when}\n\nIs that visibly true on this screen?\n\n"
    "Answer with one word first - YES if the screen shows it, NO if the "
    "screen clearly shows otherwise, UNSURE if you cannot tell from the "
    "picture. Then one short sentence saying what you can actually see.\n"
    "UNSURE is the right answer far more often than NO. Only say NO if "
    "the screen shows something that contradicts it."
)


def worth_looking(done_when: str) -> bool:
    """Is this the kind of claim a picture could disprove?"""
    text = (done_when or "").strip()
    if not text:
        return False
    if _NOT_VISIBLE.search(text):
        return False
    return bool(_VISIBLE.search(text))


def contradicted(
    step: str,
    done_when: str,
    screenshot: Any,
    vision: Any,
) -> tuple[bool, str]:
    """Does the screen say this did not happen?

    Returns (contradicted, what_was_seen). Anything other than a clear
    NO comes back False: the point is to catch a lie, not to invent one.
    """
    if vision is None or screenshot is None:
        return False, ""

    try:
        png = screenshot()
    except Exception:  # noqa: BLE001
        return False, ""
    if not png:
        return False, ""

    try:
        seen = vision.analyze(
            png, _ASK.format(step=step[:200], done_when=done_when[:200]),
        )
    except Exception:  # noqa: BLE001
        return False, ""

    said = (seen or "").strip()
    if not said:
        return False, ""

    first = said.split(None, 1)[0].strip().upper().strip(".,:")
    if first == "NO":
        return True, said[:220]
    return False, said[:220]
