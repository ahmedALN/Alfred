"""What to do about a step that did not actually work.

Diagnosing a failure and then replanning from scratch throws away the
one thing that was already known: what the person asked for. Most
failures do not need a different plan, they need one thing doing
first. A game that closes on startup usually wants its launcher
running. A network path that will not open usually wants the VPN. A
client wants its service.

That shape - "before X, do Y" - is the same everywhere, so it is
worth naming once rather than hoping a planner rediscovers it. Three
places to find the answer, cheapest first:

  the target itself   a steam:// link, or an exe under steamapps, says
                      what it needs without anybody being asked
  what the error said  "Steam must be running" is not a riddle
  the model            for everything not covered above

and then it is written down, so the next time it is the first kind of
answer rather than the third.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Repair:
    """One thing to do first, and then the original step again."""

    step: str        # plain English, for the plan and for the person
    tool: str
    args: dict[str, Any]
    why: str
    lesson: str = ""     # worth remembering, or "" if it is obvious
    certain: bool = True  # recognised outright, or guessed at

    def __str__(self) -> str:
        return f"{self.step} ({self.why})"


# Markers that say which launcher owns a program. A path or URI is
# matched against the fragments; the first hit wins.
#
# This is a list of what is common, not a list of what is possible -
# anything not here still gets an answer, it just costs a model call.
_LAUNCHERS: list[tuple[tuple[str, ...], str]] = [
    (("steam://", "steamapps", "\\steam\\"), "Steam"),
    (("com.epicgames.launcher:", "epic games\\", "epicgames"),
     "Epic Games Launcher"),
    (("goggalaxy", "gog galaxy", "gog.com\\"), "GOG Galaxy"),
    (("battle.net", "battlenet", "blizzard"), "Battle.net"),
    (("origin://", "\\origin\\", "ea desktop", "eaapp", "electronic arts"),
     "EA app"),
    (("uplay://", "ubisoft", "uplay"), "Ubisoft Connect"),
    (("riotclient", "riot games"), "Riot Client"),
    (("roblox-player:", "\\roblox\\"), "Roblox"),
    (("minecraftlauncher", "\\minecraft"), "Minecraft Launcher"),
]

# "X must be running", "please start X", "requires X to be running".
# The generalisation: an app that needs something else almost always
# says so, and says so in one of a few shapes.
_NEEDS_TEXT = [
    re.compile(r"([A-Z][\w .'-]{2,30}?)\s+(?:must|needs to|has to)\s+be\s+"
               r"(?:running|open|started)", re.I),
    re.compile(r"(?:please\s+)?(?:start|launch|open|run)\s+"
               r"([A-Z][\w .'-]{2,30}?)\s+(?:first|before)", re.I),
    re.compile(r"requires?\s+([A-Z][\w .'-]{2,30}?)\s+to\s+be\s+"
               r"(?:running|open|installed)", re.I),
    re.compile(r"could not (?:connect to|find)\s+"
               r"([A-Z][\w .'-]{2,30}?)(?:\.|,|$)", re.I),
]

_NOT_A_NAME = {
    "the", "this", "that", "it", "windows", "the app", "the game",
    "the program", "the application", "the server", "an error",
}


def _launcher_for(*texts: str) -> str | None:
    """Which launcher a path, URI or note belongs to."""
    blob = " ".join(t.lower() for t in texts if t)
    if not blob:
        return None
    for fragments, name in _LAUNCHERS:
        if any(f in blob for f in fragments):
            return name
    return None


def _named_in(text: str) -> str | None:
    """The thing an error message says has to be running."""
    for pattern in _NEEDS_TEXT:
        found = pattern.search(text or "")
        if not found:
            continue
        name = found.group(1).strip(" .'\"")
        if name.lower() in _NOT_A_NAME or len(name) < 3:
            continue
        return name
    return None


def _open_first(name: str, app: str, why: str, *,
                certain: bool = True, lesson: str | None = None) -> Repair:
    # `lesson=""` has to mean "keep nothing", not "use the default" -
    # a guess written down as a fact is how memory filled up with
    # things Alfred believed and had never checked.
    if lesson is None:
        lesson = f"{app} needs {name} running first."
    return Repair(
        step=f"Open {name}",
        tool="open_app",
        args={"app": name, "target": "current"},
        why=why,
        lesson=lesson,
        certain=certain,
    )


def prerequisite(
    app: str,
    executable: str = "",
    note: str = "",
    *,
    chat: Any = None,
) -> Repair | None:
    """What has to be running before this app will start.

    ``executable`` is the launch target - a path, a .lnk, or a URI.
    ``note`` is whatever the failure said.
    """
    target = executable or ""
    if target.lower().endswith((".lnk", ".url")):
        try:
            from src.windows.apps import shortcut_target

            resolved, _args = shortcut_target(target)
            target = resolved or target
        except Exception:  # noqa: BLE001
            pass

    # 1. The target says what owns it.
    owner = _launcher_for(target, executable)
    if owner and owner.lower() not in app.lower():
        return _open_first(
            owner, app,
            f"{Path(target).name or app} is run by {owner}, which has to "
            "be running first",
        )

    # 2. The error named something.
    named = _named_in(note)
    if named and named.lower() not in app.lower():
        return _open_first(named, app, f"the error said {named} has to be running")

    # 3. The launcher may be named in the note even when the shape of
    #    the sentence was not one of the ones above.
    owner = _launcher_for(note)
    if owner and owner.lower() not in app.lower():
        return _open_first(owner, app, f"the error mentions {owner}")

    # 4. Ask, for everything the table has never heard of.
    if chat is not None:
        guess = _ask(app, target, note, chat)
        if guess:
            return _open_first(
                guess, app,
                f"{guess} is normally needed before {app} will start",
                certain=False,
                lesson="",   # a guess is not yet worth writing down
            )
    return None


_SYSTEM = """A program on Windows started and closed again immediately \
without opening a window. That normally means one thing: something it \
depends on is not running yet - a game launcher, a client, a service.

Name the ONE program that most likely has to be running first, as it \
appears in the Start menu. If nothing obvious is missing, or the \
program simply crashed, say NONE.

Reply with only the name, or NONE. No explanation."""


def _ask(app: str, target: str, note: str, chat: Any) -> str | None:
    prompt = (
        f"{_SYSTEM}\n\nPROGRAM: {app}\n"
        f"STARTED FROM: {target or 'unknown'}\n"
        f"WHAT HAPPENED: {note or 'it exited immediately'}\n\nName:"
    )
    try:
        raw = (chat.generate(prompt, temperature=0.0, max_tokens=40) or "").strip()
    except Exception:  # noqa: BLE001
        return None

    name = raw.splitlines()[0].strip(" .'\"`") if raw else ""
    if not name or name.upper().startswith("NONE") or len(name) > 40:
        return None
    if name.lower() in _NOT_A_NAME or name.lower() in app.lower():
        return None
    return name
