"""Noticing something about your life rather than your disk.

The proactive loop had six collectors and every one of them watched the
machine: processor, memory, network, power, updates, and lately which
window is in front. So when Alfred spoke unprompted it was always about
plumbing, because plumbing was the only thing it could see.

This one reads the world model - what is due, what is late, who is
waiting - and reports the same way the others do, so the rest of the
brain needs no changes to start being proactive about the right things.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from src.brain.signals import SignalCollector
from src.brain.types import Observation

# The sources are Google APIs and a local activity log. Every ninety
# seconds would be rude to all of them and would tell us nothing new -
# deadlines move on the scale of hours at best.
_REFRESH_EVERY = 15 * 60


class WorldCollector(SignalCollector):
    name = "world"

    def __init__(
        self,
        world: Any,
        refresh: Any = None,
        clock=time.monotonic,
        wallclock=datetime.now,
    ) -> None:
        self._world = world
        self._refresh = refresh
        self._clock = clock
        self._wallclock = wallclock
        self._refreshed_at = 0.0

    def collect(self) -> list[Observation]:
        if self._world is None:
            return []

        now = self._clock()
        if self._refresh is not None and (
            now - self._refreshed_at
        ) >= _REFRESH_EVERY:
            self._refreshed_at = now
            try:
                self._refresh()
            except Exception as exc:  # noqa: BLE001
                print(f"[World] could not refresh: {exc}")

        when = self._wallclock()
        late = self._world.overdue(when)
        soon = self._world.due_soon(2, when)

        seen: list[Observation] = []

        if late:
            seen.append(Observation(
                source=self.name,
                key="world.overdue",
                # The names, not the count: "2 things overdue" is the
                # same sentence every day and stops being news, while
                # a new name in the list is the actual event.
                value=sorted(m["name"] for m in late),
                summary=(
                    "Overdue: " + "; ".join(m["name"] for m in late[:4])
                ),
            ))

        imminent = [m for m in soon if m not in late]
        if imminent:
            seen.append(Observation(
                source=self.name,
                key="world.due_soon",
                value=sorted(m["name"] for m in imminent),
                summary=(
                    "Due in the next day or two: "
                    + "; ".join(m["name"] for m in imminent[:4])
                ),
            ))

        return seen
