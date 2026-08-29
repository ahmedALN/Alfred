from __future__ import annotations

from typing import Any

_WHAT_I_AM = (
    "I'm Alfred, an AI that lives on this Windows PC. I run at startup and "
    "stay resident. I listen for a wake word, hold a spoken conversation, "
    "and can act on the machine. I have a background awareness loop that "
    "watches the system and speaks up when something needs attention, a "
    "long-term memory that carries facts between sessions, a task agent for "
    "multi-step jobs, and my own virtual desktop so I can work without "
    "covering your screen."
)


def describe_capabilities(
    registry: Any,
    settings: Any,
    *,
    resource_mode: Any = None,
    brain_enabled: bool = True,
) -> str:
    """A compact, accurate rundown Alfred can read out or put in its prompt."""

    lines: list[str] = [_WHAT_I_AM, "", "What I can do right now:"]

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

    if resource_mode is not None:
        lines.append(f"Current mode: {resource_mode.state}.")

    return "\n".join(lines)
