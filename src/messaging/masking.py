"""Phone numbers, written down without writing them down.

Alfred narrates itself to a log file, and that narration is also the
interface's log panel. Both said the owner's full number on every
start:

    [Message] WhatsApp linked to +447435589157 - messaging your own chat

The log is gitignored, so this was never going to be published. It is
still the wrong shape: a number sitting in a file that gets pasted into
bug reports, read over a shoulder, and displayed in a window that is
open on a desk.

Enough is kept to tell one number from another - the country code and
the last four - because the reason for printing it at all is so you can
see it linked to the right account.
"""

from __future__ import annotations

import re

# Long runs of digits, with the punctuation people put in numbers.
_NUMBER = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def mask(number: str) -> str:
    """"+447435589157" -> "+44...9157". Recognisable, not readable."""
    text = (number or "").strip()
    if not text:
        return ""

    digits = "".join(c for c in text if c.isdigit())
    if len(digits) < 7:
        # Too short to be a phone number; nothing worth hiding.
        return text

    plus = "+" if text.lstrip().startswith("+") else ""
    # Two for a country code is right far more often than not, and
    # being wrong about it only changes how much is hidden.
    head = digits[:2]
    tail = digits[-4:]
    return f"{plus}{head}...{tail}"


def scrub(text: str) -> str:
    """Mask every number that looks like a phone number in a line."""
    return _NUMBER.sub(lambda m: mask(m.group(0)), text or "")
