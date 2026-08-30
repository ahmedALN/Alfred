from __future__ import annotations

from typing import Any

_WHAT_I_AM = (
    "I'm Alfred, an AI that lives on this Windows PC. I run at startup and "
    "stay resident. I listen for a wake word, hold a spoken conversation, "
    "and can act on the machine. I have a background awareness loop that "
    "watches the system, a long-term memory that carries facts between "
    "sessions, and a task agent for multi-step jobs."
)

_HOW_I_WORK = (
    "I work inside apps by reading their controls by name, rather than "
    "guessing at pixels - so I can search in one, pick a result, open a "
    "menu. Where an app draws buttons with no names, I can be shown them "
    "once and remember where they sit. I learn apps as I go: what I saw, "
    "what worked, and what each button does. If you say 'without "
    "disturbing me', I do the work in a second Windows session you can't "
    "see - though you won't hear anything played there either."
)

_MY_LIMITS = (
    "Things I won't do: type a password, PIN, card number or any other "
    "credential - I'll tell you the sign-in is ready and hand it over. "
    "If an app asks which account to use, I'll ask you rather than "
    "choose. I ask before anything destructive and refuse the "
    "catastrophic. Games and anything that paints its own interface I "
    "can only see as a picture, so I'd rather start those from a "
    "launcher than try to play them."
)


def describe_capabilities(
    registry: Any,
    settings: Any,
    *,
    resource_mode: Any = None,
    brain_enabled: bool = True,
) -> str:
    """A compact, accurate rundown Alfred can read out or put in its prompt."""

    lines: list[str] = [
        _WHAT_I_AM, "", _HOW_I_WORK, "", "What I can do right now:",
    ]

    for tool in registry.list():
        desc = (getattr(tool, "description", "") or "").strip()
        first = desc.split(". ")[0].rstrip(".")
        lines.append(f"- {tool.name}: {first}.")

    lines.append("")
    lines.append(
        f"Autonomy: {settings.brain_autonomy} - I run ordinary requests "
        "straight away, ask before anything dangerous, and refuse the "
        "handful of catastrophic things (wiping a disk, deleting Windows)."
    )
    lines.append(
        f"Background awareness is {'on' if brain_enabled else 'off'}. "
        "Say 'do not disturb' to silence it, 'game mode' to free up the GPU."
    )

    lines.append("")
    lines.append(_MY_LIMITS)

    if resource_mode is not None:
        lines.append(f"Current mode: {resource_mode.state}.")

    return "\n".join(lines)
