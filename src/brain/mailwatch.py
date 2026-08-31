"""Noticing that the mailbox link has lapsed, before you do.

Google expires the credentials of an app still in Testing after seven
days. Alfred handles that correctly when asked - it says the link has
expired and what to run - but only when asked, which means the failure
mode is an inbox that quietly stops being mentioned. You would not
notice a week of "nothing important came in" was actually a week of not
looking.

So the brain checks, and says so through the same route as everything
else, which reaches the phone. A lapsed link is worth one sentence a
week and nothing more.
"""

from __future__ import annotations

import time
from typing import Any

from src.brain.signals import SignalCollector
from src.brain.types import Observation

# The link lasts a week. Checking it every ninety seconds would be a
# network call an hour for something that changes on the scale of days.
_HOW_OFTEN = 30 * 60


class MailCollector(SignalCollector):
    name = "mail"

    def __init__(self, mail: Any, clock=time.monotonic) -> None:
        self._mail = mail
        self._clock = clock
        self._checked_at = 0.0
        self._last: Observation | None = None

    def collect(self) -> list[Observation]:
        if self._mail is None:
            return []

        # Never linked at all is not a lapse, it is a thing not set up.
        # Nagging about it is what a bad assistant does.
        if not getattr(self._mail, "linked", False):
            return []

        now = self._clock()
        if self._last is not None and (now - self._checked_at) < _HOW_OFTEN:
            return [self._last]

        self._checked_at = now
        working, why = self._try()

        self._last = Observation(
            source=self.name,
            key="mail.linked",
            value=working,
            summary=(
                f"Your mailbox is connected ({why})." if working
                else "Alfred has lost access to your inbox - the weekly "
                     "Google expiry. Run: python -m src.mail link"
            ),
        )
        return [self._last]

    def _try(self) -> tuple[bool, str]:
        try:
            return True, self._mail.address(refresh=False) or "linked"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:80]
