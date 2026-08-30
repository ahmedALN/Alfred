from __future__ import annotations

import re

# "without disturbing me", "in the background", "on your own desktop", ...
# Deliberately narrow: this reroutes work onto a desktop the user cannot
# see, so a false positive is worse than a miss.
_ISOLATION = re.compile(
    r"\b("
    r"without disturbing (?:me|my (?:work|screen|desktop))"
    r"|don'?t disturb (?:me|my (?:work|screen|desktop))"
    r"|without (?:interrupting|bothering) me"
    r"|without (?:taking over|touching|using) my (?:screen|desktop|mouse|keyboard)"
    r"|(?:on|in|using) your own (?:desktop|session|screen)"
    r"|in your (?:own )?(?:session|desktop)"
    r"|quietly in the background"
    r"|in the background"
    r"|so i can keep working"
    r"|while i (?:keep )?work(?:ing)?"
    r")\b",
    re.I,
)

# Filler that only exists to introduce the isolation phrase.
_FILLER = re.compile(
    r"^(?:and|then|please|also|could you|can you|would you"
    r"|go(?:\s+ahead)?\s+and|do (?:this|that|it))\b",
    re.I,
)


def wants_isolation(text: str) -> bool:
    """True when the user asked for this to happen out of their way."""
    return bool(_ISOLATION.search(text or ""))


def strip_isolation_phrase(text: str) -> str:
    """Remove the routing phrase so the planner sees only the actual goal.

    The phrase can sit anywhere:
      "without disturbing me, open Spotify"        -> "open Spotify"
      "open Spotify without disturbing me"         -> "open Spotify"
      "do this on your own desktop: open Notepad"  -> "open Notepad"
    """
    cleaned = _ISOLATION.sub(" ", text or "")

    # Removing a phrase from the middle strands punctuation ("please, ,
    # tidy my downloads") and connectives ("do this : open Notepad").
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s*([,;:.!?-])\s*(?=[,;:.!?-])", "", cleaned)
    cleaned = cleaned.strip(" ,;:-.")

    # Peel leading filler, and any punctuation it leaves, until stable.
    for _ in range(4):
        peeled = _FILLER.sub("", cleaned, count=1).strip(" ,;:-.")
        if peeled == cleaned:
            break
        cleaned = peeled

    # Never hand back an empty goal - if the phrase was the whole message,
    # the caller still needs something to work with.
    return cleaned or (text or "").strip()
