"""Turning a message into work, and the work back into a reply.

The permission model is deliberately blunt: a message from an allowed
sender is treated exactly as if it had been spoken in the room, and a
message from anyone else is not treated at all. There is no middle
ground, because this channel can run anything on the machine and a
half-trusted stranger is a trusted stranger.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable

from src.messaging.base import Inbound

# Anything that could carry a credential. Alfred does not want these,
# cannot use them, and should not have them sitting in a chat log.
_SECRET = re.compile(
    r"\b(password|passwd|passcode|\bpin\b|otp|one[- ]time|2fa|"
    r"security code|cvv|card number|api[ _-]?key|token|secret)\b",
    re.I,
)

_REFUSED_SECRET = (
    "I don't want that - I never type passwords, codes or card details, "
    "so there is nothing useful I can do with it. Please delete that "
    "message. If something needs signing in to, I'll get it to the "
    "sign-in screen and hand it to you."
)


class MessageRouter:
    """Decides who may talk to Alfred, and what happens when they do."""

    def __init__(
        self,
        channel: Any,
        allowed: list[str] | set[str],
        submit: Callable[[str], str],
        *,
        status: Callable[[], str] | None = None,
        converse: Callable[[str], str] | None = None,
        ack: bool = True,
    ) -> None:
        self._channel = channel
        # Matched loosely, because a number arrives written a dozen ways -
        # but SENT to exactly as it was given, because the loose form is
        # missing the country code and would not reach anybody.
        self._allowed: dict[str, str] = {
            _normalise(a): str(a).strip()
            for a in allowed if str(a).strip()
        }
        self._submit = submit
        # Reads the message and decides whether it is something to say
        # back or something to do. Without one, everything is a job -
        # which is how "Hello alfred" ended up typed into Notepad.
        self._converse = converse
        self._status = status
        self._ack = ack
        self._lock = threading.Lock()
        self.seen: int = 0
        self.refused: int = 0

    # ---------------------------------------------------------------- inbound

    def allows(self, sender: str) -> bool:
        return _normalise(sender) in self._allowed

    def handle(self, message: Inbound) -> str | None:
        """Deal with one arrived message. Returns what was replied, if any."""
        text = (message.text or "").strip()

        if not self._allowed:
            # Refusing everything is the right failure: an empty
            # allowlist means nobody has said who may drive this machine.
            print("[Message] nobody is allowed - set the allowed senders")
            return None

        if not self.allows(message.sender):
            with self._lock:
                self.refused += 1
            print(f"[Message] refused a message from {message.sender!r}")
            return None

        if not text and not getattr(message, "media", None):
            return None

        with self._lock:
            self.seen += 1

        if _SECRET.search(text):
            self._reply(message.sender, _REFUSED_SECRET)
            return _REFUSED_SECRET

        lowered = text.lower().strip("?. ")
        if lowered in ("status", "what are you doing", "whats going on",
                       "what's going on", "busy?"):
            answer = self._status() if self._status else "Nothing running."
            self._reply(message.sender, answer)
            return answer

        if self._converse is not None:
            # An empty answer is not a failure: sending a screenshot
            # answers the message by itself, and following the picture
            # with a sentence saying a picture was sent is noise.
            answer = self._converse(
                text, getattr(message, "media", None),
                getattr(message, "media_kind", ""),
            )
            if answer:
                self._reply(message.sender, answer)
            return answer or None

        try:
            self._submit(text)
        except Exception as exc:  # noqa: BLE001
            failed = f"I couldn't start that: {exc}"
            self._reply(message.sender, failed)
            return failed

        if not self._ack:
            return None

        # Say it has started, not that it is done. What actually
        # happened arrives when it has happened.
        started = "On it."
        self._reply(message.sender, started)
        return started

    # --------------------------------------------------------------- outbound

    def notify(self, text: str) -> None:
        """Tell the user something without being asked."""
        text = (text or "").strip()
        if not text:
            return
        for original in sorted(self._allowed.values()):
            self._reply(original, text)

    def _reply(self, to: str, text: str) -> None:
        try:
            self._channel.send(text, to=to)
        except Exception as exc:  # noqa: BLE001
            print(f"[Message] could not reply: {exc}")


def _normalise(value: str) -> str:
    """A phone number written any of the usual ways, reduced to one form.

    The same phone is written +44 7700 900123, 447700900123 and
    07700900123 depending on who is writing it, and an allowlist that
    treats those as three different people locks the owner out. The last
    ten digits are the part that does not change.
    """
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)

    if len(digits) < 7:
        # Not a phone number: a username, an id, something else.
        return text.lower()

    return digits[-10:]
