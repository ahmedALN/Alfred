from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ====================================================================
# Perception
# ====================================================================


@dataclass(frozen=True)
class Observation:
    """
    A single raw reading from one signal collector at one tick.

    ``key`` identifies the thing being observed (e.g. "disk.C:",
    "firewall.Domain.enabled", "process.top_cpu"). ``value`` is the
    current reading. ``summary`` is a short human sentence.
    """

    source: str
    key: str
    value: Any
    summary: str


@dataclass(frozen=True)
class Notable:
    """
    Something worth reasoning about: a signal that is new, changed, or
    has crossed a threshold since the previous tick.
    """

    source: str
    key: str
    summary: str
    severity: str = "info"  # "info" | "warn" | "critical"
    previous: Any = None
    current: Any = None


# ====================================================================
# Deliberation
# ====================================================================


@dataclass(frozen=True)
class DeliberationContext:
    """Everything the reasoner sees for one deliberation call."""

    notables: list[Notable]
    memory_context: str
    recent_turns: list[dict[str, str]]
    tool_catalogue: list[dict[str, Any]]
    suppressions: list[str]
    autonomy: str


class ProposalKind(str, Enum):
    SPEAK = "speak"
    ACT = "act"


@dataclass(frozen=True)
class Proposal:
    """A single thing the brain wants to do this tick."""

    kind: ProposalKind
    message: str
    rationale: str = ""
    urgency: str = "normal"  # "low" | "normal" | "high"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    reversible: bool | None = None

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Proposal | None":
        kind_raw = str(raw.get("kind", "")).strip().lower()

        if kind_raw not in (ProposalKind.SPEAK.value, ProposalKind.ACT.value):
            return None

        message = str(raw.get("message", "")).strip()

        if not message and kind_raw == ProposalKind.SPEAK.value:
            return None

        tool = raw.get("tool")
        tool = str(tool).strip() if isinstance(tool, str) and tool.strip() else None

        args = raw.get("args")
        args = args if isinstance(args, dict) else {}

        if kind_raw == ProposalKind.ACT.value and not tool:
            return None

        urgency = str(raw.get("urgency", "normal")).strip().lower()

        if urgency not in ("low", "normal", "high"):
            urgency = "normal"

        reversible = raw.get("reversible")
        reversible = reversible if isinstance(reversible, bool) else None

        return Proposal(
            kind=ProposalKind(kind_raw),
            message=message,
            rationale=str(raw.get("rationale", "")).strip(),
            urgency=urgency,
            tool=tool,
            args=args,
            reversible=reversible,
        )


# ====================================================================
# Policy
# ====================================================================


class Verdict(str, Enum):
    AUTO = "auto"        # execute now
    CONFIRM = "confirm"  # ask the user out loud, wait for approval
    FORBID = "forbid"    # never, drop and log


@dataclass(frozen=True)
class Decision:
    proposal: Proposal
    verdict: Verdict
    reason: str
