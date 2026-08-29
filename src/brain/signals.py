from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.brain.types import Observation
from src.windows.powershell import PowerShellRunner
from src.windows.system_probe import (
    NETWORK_QUERIES,
    PENDING_REBOOT_QUERY,
    SYSTEM_QUERIES,
    foreground_app,
    idle_seconds,
    is_fullscreen_foreground,
    power_state,
    run_json_query,
)


class SignalCollector(ABC):
    """
    One source of awareness. ``collect`` must never raise: a broken
    collector should degrade to an empty list, not crash the brain.
    """

    name: str

    @abstractmethod
    def collect(self) -> list[Observation]:
        raise NotImplementedError

    def safe_collect(self) -> list[Observation]:
        try:
            return self.collect()
        except Exception as exc:  # noqa: BLE001 - deliberate: brain must survive
            print(f"[Brain/Signals] {self.name} collector failed: {exc}")
            return []


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_enabled(value: Any) -> bool | None:
    """
    Get-NetFirewallProfile's Enabled comes back as 1/0, true/false, or
    the GpoBoolean names. Return True/False when certain, None when not
    - the brain must never guess 'off'.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "enabled", "yes", "on"):
            return True
        if v in ("0", "false", "disabled", "no", "off"):
            return False
    return None


class ResourceCollector(SignalCollector):
    """CPU load, free RAM, and per-disk free space."""

    name = "resources"

    def __init__(
        self,
        runner: PowerShellRunner | None = None,
        low_disk_gb: float = 15.0,
        low_memory_pct: float = 12.0,
        high_cpu_pct: float = 90.0,
    ) -> None:
        self._runner = runner or PowerShellRunner()
        self._low_disk_gb = low_disk_gb
        self._low_memory_pct = low_memory_pct
        self._high_cpu_pct = high_cpu_pct

    def collect(self) -> list[Observation]:
        observations: list[Observation] = []

        overview = run_json_query(
            SYSTEM_QUERIES["overview"], self._runner
        )

        if isinstance(overview, dict):
            total = overview.get("TotalMemoryGB")
            free = overview.get("FreeMemoryGB")
            cpu_load = overview.get("CPULoadPercent")

            if isinstance(total, (int, float)) and isinstance(
                free, (int, float)
            ) and total:
                free_pct = round(100.0 * float(free) / float(total), 1)

                observations.append(
                    Observation(
                        source=self.name,
                        key="memory.free_pct",
                        value=free_pct,
                        summary=(
                            f"RAM: {free} GB free of {total} GB "
                            f"({free_pct}% free)"
                        ),
                    )
                )

            if isinstance(cpu_load, (int, float)):
                observations.append(
                    Observation(
                        source=self.name,
                        key="cpu.load_pct",
                        value=float(cpu_load),
                        summary=f"CPU load: {cpu_load}%",
                    )
                )

            uptime = overview.get("UptimeHours")

            if isinstance(uptime, (int, float)):
                observations.append(
                    Observation(
                        source=self.name,
                        key="system.uptime_hours",
                        value=float(uptime),
                        summary=f"Uptime: {uptime} h",
                    )
                )

        disks = run_json_query(SYSTEM_QUERIES["disks"], self._runner)

        for disk in _as_list(disks):
            if not isinstance(disk, dict):
                continue

            device = disk.get("DeviceID", "?")
            free_gb = disk.get("FreeGB")
            size_gb = disk.get("SizeGB")

            if not isinstance(free_gb, (int, float)):
                continue

            observations.append(
                Observation(
                    source=self.name,
                    key=f"disk.{device}.free_gb",
                    value=float(free_gb),
                    summary=(
                        f"Disk {device}: {free_gb} GB free"
                        + (f" of {size_gb} GB" if size_gb else "")
                    ),
                )
            )

        return observations


class NetworkCollector(SignalCollector):
    """Listening TCP ports and per-profile firewall status."""

    name = "network"

    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self._runner = runner or PowerShellRunner()

    def collect(self) -> list[Observation]:
        observations: list[Observation] = []

        ports = run_json_query(
            NETWORK_QUERIES["listening_ports"], self._runner, timeout=20.0
        )

        listening: list[str] = []

        for row in _as_list(ports):
            if not isinstance(row, dict):
                continue

            port = row.get("LocalPort")
            proc = row.get("Process") or "unknown"

            if port is None:
                continue

            listening.append(f"{port}/{proc}")

        # De-duplicate and sort for a stable signature across ticks.
        unique_ports = sorted(set(listening))

        observations.append(
            Observation(
                source=self.name,
                key="network.listening_ports",
                value=unique_ports,
                summary=(
                    f"{len(unique_ports)} listening TCP port(s): "
                    + ", ".join(unique_ports[:12])
                    + ("…" if len(unique_ports) > 12 else "")
                ),
            )
        )

        profiles = run_json_query(
            NETWORK_QUERIES["firewall_profile_status"],
            self._runner,
            timeout=20.0,
        )

        for row in _as_list(profiles):
            if not isinstance(row, dict):
                continue

            name = row.get("Name", "?")
            is_on = _parse_enabled(row.get("Enabled"))

            # Unknown / unparseable -> emit nothing, so a bad reading
            # can never look like "firewall turned off".
            if is_on is None:
                continue

            observations.append(
                Observation(
                    source=self.name,
                    key=f"firewall.{name}.enabled",
                    value=is_on,
                    summary=(
                        f"Firewall profile {name}: "
                        f"{'on' if is_on else 'OFF'}"
                    ),
                )
            )

        return observations


class ProcessCollector(SignalCollector):
    """The current set of heaviest processes by CPU."""

    name = "processes"

    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self._runner = runner or PowerShellRunner()

    def collect(self) -> list[Observation]:
        procs = run_json_query(
            SYSTEM_QUERIES["top_processes"], self._runner
        )

        names: list[str] = []

        for row in _as_list(procs):
            if not isinstance(row, dict):
                continue

            name = row.get("Name")

            if isinstance(name, str) and name:
                names.append(name)

        top5 = names[:5]

        return [
            Observation(
                source=self.name,
                key="process.top_cpu",
                value=sorted(set(top5)),
                summary="Top CPU processes: " + ", ".join(top5),
            )
        ]


class PowerCollector(SignalCollector):
    """AC vs battery and charge level."""

    name = "power"

    def collect(self) -> list[Observation]:
        state = power_state()

        return [
            Observation(
                source=self.name,
                key="power.on_battery",
                value=state.on_battery,
                summary=(
                    "On battery"
                    + (
                        f" ({state.percent}%)"
                        if state.percent is not None
                        else ""
                    )
                    if state.on_battery
                    else "On AC power"
                ),
            ),
            Observation(
                source=self.name,
                key="power.percent",
                value=state.percent,
                summary=(
                    f"Battery at {state.percent}%"
                    if state.percent is not None
                    else "Battery level unknown"
                ),
            ),
        ]


class UpdatesCollector(SignalCollector):
    """Whether Windows is waiting on a reboot."""

    name = "updates"

    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self._runner = runner or PowerShellRunner()

    def collect(self) -> list[Observation]:
        data = run_json_query(PENDING_REBOOT_QUERY, self._runner)

        pending = bool(
            isinstance(data, dict) and data.get("PendingReboot")
        )

        return [
            Observation(
                source=self.name,
                key="updates.pending_reboot",
                value=pending,
                summary=(
                    "A reboot is pending"
                    if pending
                    else "No reboot pending"
                ),
            )
        ]


class SessionContextCollector(SignalCollector):
    """
    What the user is doing right now. Mostly feeds do-not-disturb and
    habit notes rather than triggering suggestions directly.
    """

    name = "session_context"

    def collect(self) -> list[Observation]:
        app = foreground_app()
        idle = round(idle_seconds(), 1)
        fullscreen = is_fullscreen_foreground()

        return [
            Observation(
                source=self.name,
                key="session.foreground_app",
                value=app,
                summary=f"Foreground app: {app or 'unknown'}",
            ),
            Observation(
                source=self.name,
                key="session.idle_seconds",
                value=idle,
                summary=f"User idle for {idle:.0f}s",
            ),
            Observation(
                source=self.name,
                key="session.fullscreen",
                value=fullscreen,
                summary=(
                    "Foreground window is fullscreen"
                    if fullscreen
                    else "No fullscreen window"
                ),
            ),
        ]


def default_collectors(
    runner: PowerShellRunner | None = None,
) -> list[SignalCollector]:
    """The collector set the brain runs unless told otherwise."""

    shared = runner or PowerShellRunner()

    return [
        ResourceCollector(shared),
        NetworkCollector(shared),
        ProcessCollector(shared),
        PowerCollector(),
        UpdatesCollector(shared),
        SessionContextCollector(),
    ]
