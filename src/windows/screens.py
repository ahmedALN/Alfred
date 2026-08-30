"""Recognising a screen that needs the user, not the agent.

Apps stop and ask things. Steam opens on "Who's playing?" with a list of
accounts; a launcher wants a password; an installer wants the licence
accepted. Alfred cannot answer any of those - the first is the user's
choice, the second it is forbidden to type, the third is a decision only
they can make.

Left undetected these look like ordinary windows: the tree is populated,
wait_ready reports the app is usable, and the agent starts clicking. The
point of this module is to notice, name what is being asked, and hand
the question back with the real options in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# "Who's playing?" (Steam), "Choose a profile" (Netflix, launchers),
# "Select a user" (multi-account apps).
_PROFILE = re.compile(
    r"who'?s (playing|watching|there)|"
    r"(choose|select|pick|switch)\s+(an?\s+)?(account|profile|user)|"
    r"select\s+player|choose\s+who",
    re.I,
)

_SIGN_IN = re.compile(
    r"\b(sign in|sign-in|signin|log ?in|log-in|welcome back|"
    r"enter your password|forgot password|create an account|"
    r"two[- ]factor|2fa|authenticator|verification code|"
    r"one[- ]time code|steam guard)\b",
    re.I,
)

_CONSENT = re.compile(
    r"\b(terms of service|licen[cs]e agreement|end user licen|"
    r"privacy policy|i agree|accept the terms|eula)\b",
    re.I,
)

# Controls that represent something choosable rather than description.
_CHOOSABLE = {"Hyperlink", "ListItem", "Button", "TreeItem", "DataItem",
              "MenuItem", "TabItem"}

# Options that are not really a profile.
_NOT_A_PROFILE = re.compile(
    r"^(add account|add user|add profile|manage|manage profiles|"
    r"sign in with|other|cancel|back|close|minimi[sz]e|maximi[sz]e|help|"
    r"settings|forgot)", re.I,
)


@dataclass
class ScreenNeed:
    """Something only the user can answer."""

    kind: str                       # sign_in | choose_profile | consent
    question: str
    choices: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"needs_user": self.kind, "question": self.question}
        if self.choices:
            out["choices"] = self.choices
        out["instruction"] = _INSTRUCTIONS[self.kind]
        return out


_INSTRUCTIONS = {
    "sign_in": (
        "Do NOT type anything into this. Tell the user the app is asking "
        "them to sign in, say which app, and ask them to do it - then "
        "carry on once they say they are done."
    ),
    "choose_profile": (
        "Ask the user which one they want, reading out the choices. Do "
        "not pick for them. Once they answer, use ui_control open_item "
        "with that name."
    ),
    "consent": (
        "Read out what is being agreed to and ask the user whether to "
        "accept. Never accept terms on their behalf."
    ),
}


def _texts(controls: list[Any]) -> str:
    return " \n".join((getattr(c, "name", "") or "") for c in controls)


def _options(controls: list[Any]) -> list[str]:
    """The choosable, distinct, plausible options on screen."""
    seen: set[str] = set()
    out: list[str] = []

    for control in controls:
        if getattr(control, "control_type", "") not in _CHOOSABLE:
            continue

        name = (getattr(control, "name", "") or "").strip()
        if not name or len(name) > 60:
            continue

        key = name.lower()
        if key in seen or _NOT_A_PROFILE.match(name):
            continue

        seen.add(key)
        out.append(name)

    return out


# Everything a window has before it has any interface of its own.
_CHROME_ONLY = {
    "system", "minimise", "minimize", "maximise", "maximize", "restore",
    "close", "context help", "application", "title bar",
}


def draws_its_own_ui(controls: list[Any]) -> bool:
    """True when a window exposes nothing but its own frame.

    Games and game-engine clients paint their interface with the GPU and
    publish no accessibility tree at all - Roblox reports four controls:
    System, Minimise, Restore, Close. Retrying, waiting longer or
    reading more deeply will never find a Play button, because there
    isn't one to find. Only a screenshot can see that window.
    """
    named = [
        (getattr(c, "name", "") or "").strip().lower()
        for c in controls
    ]
    named = [n for n in named if n]

    if not named or len(named) > 8:
        return False

    return all(n in _CHROME_ONLY for n in named)


def assess(title: str, controls: list[Any]) -> ScreenNeed | None:
    """What, if anything, this screen needs from the user."""
    haystack = f"{title}\n{_texts(controls)}"

    # A masked field settles it, whatever the wording says.
    if any(getattr(c, "is_password", False) for c in controls):
        return ScreenNeed(
            "sign_in",
            f"{title or 'This app'} is asking for a password.",
        )

    if _PROFILE.search(haystack):
        options = _options(controls)
        if options:
            return ScreenNeed(
                "choose_profile",
                f"{title or 'This app'} is asking which account to use.",
                options,
            )

    if _SIGN_IN.search(haystack):
        return ScreenNeed(
            "sign_in",
            f"{title or 'This app'} is asking you to sign in.",
        )

    if _CONSENT.search(haystack):
        return ScreenNeed(
            "consent",
            f"{title or 'This app'} is asking you to accept its terms.",
        )

    return None
