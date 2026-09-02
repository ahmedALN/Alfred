from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Inbound:
    """One message that arrived from outside."""

    sender: str          # whoever sent it, in the channel's own terms
    text: str
    channel: str = ""
    raw: Any = None
    # A picture or clip that came with it. Alfred could be talked to
    # and not shown anything, which rules out most of the times a
    # person reaches for their phone: this error, this letter, this
    # thing on the shelf.
    media: bytes | None = None
    media_kind: str = ""     # image | video | document


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
