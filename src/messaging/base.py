from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Inbound:
    """One message that arrived from outside."""

    sender: str          # whoever sent it, in the channel's own terms
    text: str
    channel: str = ""
    raw: Any = None


class Channel(ABC):
    """A way for messages to reach Alfred and answers to get back."""

    name: str = "channel"

    @abstractmethod
    def send(self, text: str, to: str | None = None) -> bool:
        """Send a message. Returns whether it went."""
        raise NotImplementedError

    def start(self, on_message: Callable[[Inbound], None]) -> None:
        """Begin delivering inbound messages. Optional for send-only."""
        return None

    def stop(self) -> None:
        return None
