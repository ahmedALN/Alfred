from __future__ import annotations

import asyncio
import time
from typing import Callable

StateCallback = Callable[[bool], None]


class ActivationController:
    """
    Owns the NORMAL <-> LISTENING state.

    - NORMAL: the mic is captured but nothing is sent to the voice model.
      A wake word or hotkey flips us to LISTENING.
    - LISTENING: mic audio flows to the model. Every user turn resets an
      idle timer; after ``idle_seconds`` of silence we drop back to NORMAL.

    When both the wake word and the hotkey are disabled, ``always_on`` is
    set and Alfred simply always listens (the original behaviour).
    """

    def __init__(
        self,
        idle_seconds: float = 30.0,
        *,
        always_on: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle = idle_seconds
        self._always = always_on
        self._monotonic = monotonic

        self._listening = always_on
        self._last_activity = monotonic()
        self.on_state_change: StateCallback | None = None

    # ----------------------------------------------------------------

    @property
    def is_listening(self) -> bool:
        return self._always or self._listening

    @property
    def always_on(self) -> bool:
        return self._always

    def wake(self, source: str = "wake") -> None:
        self._last_activity = self._monotonic()

        if self._listening or self._always:
            return

        self._listening = True
        self._emit(True, source)

    def note_activity(self) -> None:
        self._last_activity = self._monotonic()

    def extend(self, min_seconds: float) -> None:
        """Guarantee at least ``min_seconds`` remain before the idle
        timeout - used when Alfred asks the user a question and should
        keep listening for the answer."""
        target = self._monotonic() + min_seconds - self._idle
        if target > self._last_activity:
            self._last_activity = target

    def sleep(self, source: str = "idle") -> None:
        if self._always or not self._listening:
            return

        self._listening = False
        self._emit(False, source)

    def _emit(self, listening: bool, source: str) -> None:
        print(
            f"[Activation] {'LISTENING' if listening else 'asleep'} ({source})"
        )
        if self.on_state_change is not None:
            try:
                self.on_state_change(listening)
            except Exception as exc:  # noqa: BLE001
                print(f"[Activation] state callback failed: {exc}")

    # ----------------------------------------------------------------

    async def run(self, poll_seconds: float = 2.0) -> None:
        """Background loop: return to NORMAL after an idle stretch."""

        if self._always:
            # Nothing to police.
            while True:
                await asyncio.sleep(3600)

        while True:
            await asyncio.sleep(poll_seconds)

            if not self._listening:
                continue

            if self._monotonic() - self._last_activity > self._idle:
                self.sleep("idle timeout")
