from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.brain.signals import SignalCollector, default_collectors
from src.brain.types import Notable, Observation


@dataclass
class PerceptionThresholds:
    low_disk_gb: float = 15.0
    disk_step_gb: float = 5.0
    low_memory_pct: float = 12.0
    high_cpu_pct: float = 90.0
    high_cpu_ticks: int = 3
    long_uptime_hours: float = 336.0  # 14 days
    low_battery_pct: int = 20
    critical_battery_pct: int = 10


# ====================================================================
# What a reading is allowed to be for
#
# Every collector's observation ends up in one of three places: a check
# below turns it into a Notable, it is listed here as context the
# reasoner reads without it being news in itself, or it is a reading
# nothing consumes.
#
# The third case is not hypothetical. Three collectors were written to
# give the brain something personal to be proactive about - what you
# have been doing, what is overdue, whether the mailbox link has
# lapsed - they were wired into main.py, they ran every ninety seconds,
# and `_diff` had no branch for any of their keys. Their readings were
# collected and dropped on the floor. Across 1,338 ticks the brain
# produced five proposals, all of them the same sentence about free
# RAM, because free RAM was the only thing anything downstream could
# see.
#
# So the set is declared, and `unhandled_keys` reports anything that
# falls outside it. A collector added without a check is now a test
# failure rather than a silence.
# ====================================================================

# Read for context - the situation the reasoner is given, and the
# do-not-disturb decision - rather than raised as events of their own.
CONTEXT_ONLY_KEYS: frozenset[str] = frozenset({
    "process.top_cpu",
    "session.foreground_app",
    "session.idle_seconds",
    "session.fullscreen",
    "activity.where",
})

# Keys whose exact name a check looks for.
_HANDLED_EXACT: frozenset[str] = frozenset({
    "memory.free_pct",
    "cpu.load_pct",
    "system.uptime_hours",
    "updates.pending_reboot",
    "power.on_battery",
    "power.percent",
    "network.listening_ports",
    "activity.long_stretch",
    "world.overdue",
    "world.due_soon",
    "mail.linked",
})

# Keys whose name carries a device or profile in the middle.
_HANDLED_SHAPES: tuple[tuple[str, str], ...] = (
    ("disk.", ".free_gb"),
    ("firewall.", ".enabled"),
)


def _is_handled(key: str) -> bool:
    if key in _HANDLED_EXACT or key in CONTEXT_ONLY_KEYS:
        return True

    return any(
        key.startswith(prefix) and key.endswith(suffix)
        for prefix, suffix in _HANDLED_SHAPES
    )


@dataclass
class _Snapshot:
    values: dict[str, Any] = field(default_factory=dict)
    summaries: dict[str, str] = field(default_factory=dict)


class Perception:
    """
    Runs the signal collectors on demand and turns the raw readings
    into ``Notable``s: only things that are new, changed, or have
    crossed a threshold since the previous tick.

    Hysteresis: a condition-based notable (low disk, high CPU, …) is
    emitted once when it becomes true and not again until it has
    cleared, so the brain is not nagged every tick.
    """

    def __init__(
        self,
        collectors: list[SignalCollector] | None = None,
        thresholds: PerceptionThresholds | None = None,
    ) -> None:
        self._collectors = collectors or default_collectors()
        self._t = thresholds or PerceptionThresholds()

        self._previous: _Snapshot | None = None
        self._active_conditions: set[str] = set()
        self._high_cpu_streak = 0
        self._unhandled: set[str] = set()
        self._ports_pending: set[str] = set()
        self._seen_matters: dict[str, set[str]] = {}

    # ----------------------------------------------------------------

    def sense(self) -> tuple[list[Notable], list[Observation]]:
        observations: list[Observation] = []

        for collector in self._collectors:
            observations.extend(collector.safe_collect())

        snapshot = _Snapshot()

        for obs in observations:
            snapshot.values[obs.key] = obs.value
            snapshot.summaries[obs.key] = obs.summary

            if not _is_handled(obs.key):
                self._unhandled.add(obs.key)

        notables = self._diff(snapshot)

        self._previous = snapshot

        return notables, observations

    def unhandled_keys(self) -> set[str]:
        """Readings seen so far that no check and no context list claims.

        A collector wired in without a check is a collector whose work
        is thrown away every ninety seconds, silently. This is how that
        becomes visible - to a test, and to `python -m src.doctor`.
        """

        return set(self._unhandled)

    # ----------------------------------------------------------------

    def _diff(self, snap: _Snapshot) -> list[Notable]:
        prev = self._previous
        prev_values = prev.values if prev else {}

        notables: list[Notable] = []

        self._check_disks(snap, prev_values, notables)
        self._check_memory(snap, prev_values, notables)
        self._check_cpu(snap, notables)
        self._check_firewall(snap, prev_values, notables)
        self._check_reboot(snap, prev_values, notables)
        self._check_power(snap, prev_values, notables)
        self._check_uptime(snap, notables)
        self._check_new_ports(snap, prev_values, notables)

        # The reason any of this is worth doing.
        self._check_long_stretch(snap, prev_values, notables)
        self._check_world(snap, prev_values, notables)
        self._check_mail_link(snap, prev_values, notables)

        return notables

    # ---- condition helpers -----------------------------------------

    def _enter(self, key: str) -> bool:
        """True the first time a condition becomes active."""

        if key in self._active_conditions:
            return False

        self._active_conditions.add(key)
        return True

    def _clear(self, key: str) -> None:
        self._active_conditions.discard(key)

    # ---- individual checks ----------------------------------------

    def _check_disks(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        for key, value in snap.values.items():
            if not (key.startswith("disk.") and key.endswith(".free_gb")):
                continue

            if not isinstance(value, (int, float)):
                continue

            cond = f"low_disk:{key}"

            if value < self._t.low_disk_gb:
                previous = prev.get(key)
                dropped_a_step = (
                    isinstance(previous, (int, float))
                    and previous - value >= self._t.disk_step_gb
                )

                if self._enter(cond) or dropped_a_step:
                    self._active_conditions.add(cond)
                    out.append(
                        Notable(
                            source="resources",
                            key=key,
                            summary=snap.summaries.get(key, f"{key}={value}"),
                            severity=(
                                "critical" if value < self._t.low_disk_gb / 3
                                else "warn"
                            ),
                            previous=previous,
                            current=value,
                        )
                    )
            else:
                self._clear(cond)

    def _check_memory(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        value = snap.values.get("memory.free_pct")

        if not isinstance(value, (int, float)):
            return

        cond = "low_memory"

        if value < self._t.low_memory_pct:
            if self._enter(cond):
                out.append(
                    Notable(
                        source="resources",
                        key="memory.free_pct",
                        summary=snap.summaries.get("memory.free_pct", ""),
                        severity="warn",
                        previous=prev.get("memory.free_pct"),
                        current=value,
                    )
                )
        else:
            self._clear(cond)

    def _check_cpu(self, snap: _Snapshot, out: list[Notable]) -> None:
        value = snap.values.get("cpu.load_pct")

        if not isinstance(value, (int, float)):
            self._high_cpu_streak = 0
            return

        if value >= self._t.high_cpu_pct:
            self._high_cpu_streak += 1
        else:
            self._high_cpu_streak = 0
            self._clear("high_cpu")

        if self._high_cpu_streak >= self._t.high_cpu_ticks:  # noqa: SIM102
            if self._enter("high_cpu"):
                top = snap.summaries.get("process.top_cpu", "")
                out.append(
                    Notable(
                        source="resources",
                        key="cpu.load_pct",
                        summary=(
                            f"Sustained high CPU load ({value}%) across "
                            f"{self._high_cpu_streak} checks. {top}"
                        ),
                        severity="warn",
                        current=value,
                    )
                )

    def _check_firewall(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        for key, value in snap.values.items():
            if not (key.startswith("firewall.") and key.endswith(".enabled")):
                continue

            was = prev.get(key)
            cond = f"fw_off:{key}"

            if value is False and was is False:
                # Off confirmed on two consecutive ticks - real.
                if self._enter(cond):
                    out.append(
                        Notable(
                            source="network",
                            key=key,
                            summary=(
                                f"{snap.summaries.get(key, key)} "
                                "(off on the last two checks)"
                            ),
                            severity="critical",
                            previous=was,
                            current=value,
                        )
                    )
            elif value is True:
                self._clear(cond)

    def _check_reboot(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        value = snap.values.get("updates.pending_reboot")
        was = prev.get("updates.pending_reboot")

        if value is True and was is not True:
            out.append(
                Notable(
                    source="updates",
                    key="updates.pending_reboot",
                    summary="Windows now has a reboot pending.",
                    severity="info",
                    previous=was,
                    current=value,
                )
            )

        if value is not True:
            self._clear("pending_reboot")

    def _check_power(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        on_battery = snap.values.get("power.on_battery")
        was_on_battery = prev.get("power.on_battery")
        percent = snap.values.get("power.percent")

        if on_battery is True and was_on_battery is False:
            out.append(
                Notable(
                    source="power",
                    key="power.on_battery",
                    summary=snap.summaries.get("power.on_battery", "On battery"),
                    severity="info",
                    previous=was_on_battery,
                    current=on_battery,
                )
            )

        if on_battery is not True:
            self._clear("low_battery")
            self._clear("critical_battery")
            return

        if isinstance(percent, int):
            if percent <= self._t.critical_battery_pct:
                if self._enter("critical_battery"):
                    out.append(
                        Notable(
                            source="power",
                            key="power.percent",
                            summary=f"Battery critically low at {percent}%.",
                            severity="critical",
                            current=percent,
                        )
                    )
            elif percent <= self._t.low_battery_pct:
                if self._enter("low_battery"):
                    out.append(
                        Notable(
                            source="power",
                            key="power.percent",
                            summary=f"Battery low at {percent}%.",
                            severity="warn",
                            current=percent,
                        )
                    )
            else:
                self._clear("low_battery")
                self._clear("critical_battery")

    def _check_uptime(self, snap: _Snapshot, out: list[Notable]) -> None:
        value = snap.values.get("system.uptime_hours")

        if not isinstance(value, (int, float)):
            return

        if value >= self._t.long_uptime_hours:
            if self._enter("long_uptime"):
                days = round(value / 24.0, 1)
                out.append(
                    Notable(
                        source="resources",
                        key="system.uptime_hours",
                        summary=(
                            f"The machine has been up for {days} days "
                            "without a restart."
                        ),
                        severity="info",
                        current=value,
                    )
                )
        else:
            self._clear("long_uptime")

    def _check_new_ports(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        current = snap.values.get("network.listening_ports")
        previous = prev.get("network.listening_ports")

        if not isinstance(current, list) or not isinstance(previous, list):
            return

        appeared = set(current) - set(previous)

        # A port that opened and closed inside three minutes was an app
        # starting up, and saying so was 148 of the brain's 210 notables
        # - seven times more than everything else combined, all of it
        # "Spotify opened a port". Only a port still there on the next
        # look is worth a sentence.
        confirmed = sorted(self._ports_pending & set(current))
        self._ports_pending = appeared - set(confirmed)

        if confirmed:
            out.append(
                Notable(
                    source="network",
                    key="network.listening_ports",
                    summary=(
                        "New listening TCP port(s): "
                        + ", ".join(confirmed)
                    ),
                    severity="info",
                    previous=previous,
                    current=confirmed,
                )
            )

    # ---- and the reason any of this is worth doing -----------------

    def _check_long_stretch(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        """You have been in the same window for a very long time."""

        value = snap.values.get("activity.long_stretch")

        if value is not True:
            self._clear("long_stretch")
            return

        # Only on the crossing. Every tick after it is the same fact.
        if prev.get("activity.long_stretch") is True:
            return

        if self._enter("long_stretch"):
            out.append(
                Notable(
                    source="activity",
                    key="activity.long_stretch",
                    summary=snap.summaries.get(
                        "activity.long_stretch",
                        "You have been in the same window a long time.",
                    ),
                    severity="info",
                    previous=prev.get("activity.long_stretch"),
                    current=value,
                )
            )

    def _check_world(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        """Something of yours came due, or went past due."""

        for key, severity in (
            ("world.overdue", "warn"),
            ("world.due_soon", "info"),
        ):
            current = snap.values.get(key)

            if not isinstance(current, list) or not current:
                # Nothing due is not news, and it lets the same item be
                # news again if it comes back.
                self._seen_matters.pop(key, None)
                continue

            previously = self._seen_matters.get(key, set())
            fresh = sorted(set(current) - previously)

            # Remember the whole current list, not just what was
            # announced: an item that stays overdue must not be
            # announced again tomorrow.
            self._seen_matters[key] = set(current)

            if not fresh:
                continue

            out.append(
                Notable(
                    source="world",
                    key=key,
                    summary=(
                        ("Overdue: " if key == "world.overdue"
                         else "Due in the next day or two: ")
                        + "; ".join(fresh[:4])
                        + (f" (+{len(fresh) - 4} more)" if len(fresh) > 4 else "")
                    ),
                    severity=severity,
                    previous=sorted(previously),
                    current=fresh,
                )
            )

    def _check_mail_link(
        self,
        snap: _Snapshot,
        prev: dict[str, Any],
        out: list[Notable],
    ) -> None:
        """The Google link lapsed - which is Alfred's problem to raise."""

        value = snap.values.get("mail.linked")

        if value is not False:
            self._clear("mail_unlinked")
            return

        if self._enter("mail_unlinked"):
            out.append(
                Notable(
                    source="mail",
                    key="mail.linked",
                    summary=snap.summaries.get(
                        "mail.linked",
                        "Alfred has lost access to your Google account.",
                    ),
                    severity="warn",
                    previous=prev.get("mail.linked"),
                    current=value,
                )
            )
