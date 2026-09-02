from __future__ import annotations

import ctypes
import json
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from src.windows.powershell import PowerShellRunner

# ====================================================================
# Structured PowerShell queries
#
# Single source of truth. Both the user-facing tools
# (src/tools/system_info.py, src/tools/network_info.py) and the
# background brain (src/brain/signals.py) read these so the two never
# drift apart.
# ====================================================================

SYSTEM_QUERIES: dict[str, str] = {
    "overview": (
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1; "
        "$uptime = (Get-Date) - $os.LastBootUpTime; "
        "[PSCustomObject]@{ "
        "ComputerName = $env:COMPUTERNAME; "
        "OS = $os.Caption; "
        "CPU = $cpu.Name; "
        "CPULoadPercent = $cpu.LoadPercentage; "
        "TotalMemoryGB = [math]::Round($os.TotalVisibleMemorySize/1MB,1); "
        "FreeMemoryGB = [math]::Round($os.FreePhysicalMemory/1MB,1); "
        "UptimeHours = [math]::Round($uptime.TotalHours,1) "
        "} | ConvertTo-Json"
    ),
    "disks": (
        "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
        "Select-Object DeviceID, "
        "@{N='SizeGB';E={[math]::Round($_.Size/1GB,1)}}, "
        "@{N='FreeGB';E={[math]::Round($_.FreeSpace/1GB,1)}} | "
        "ConvertTo-Json"
    ),
    "top_processes": (
        "Get-Process | Sort-Object CPU -Descending | "
        "Select-Object -First 10 Name, Id, "
        "@{N='MemoryMB';E={[math]::Round($_.WorkingSet64/1MB,1)}}, CPU | "
        "ConvertTo-Json"
    ),
    # ----------------------------------------------------------------
    # Everything below was a question Alfred was asked, had no query
    # for, and answered by writing PowerShell from scratch each time.
    # The bench caught two of them coming back as non-answers - "The
    # provided information does not include the graphics card" - and
    # the skill library had banked five separate learned routines that
    # exist only because there was nothing to just ask.
    # ----------------------------------------------------------------
    "graphics": (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, DriverVersion, "
        "@{N='MemoryGB';E={[math]::Round($_.AdapterRAM/1GB,1)}}, "
        "CurrentHorizontalResolution, CurrentVerticalResolution | "
        "ConvertTo-Json"
    ),
    "windows_version": (
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "[PSCustomObject]@{ "
        "Caption = $os.Caption; "
        "Version = $os.Version; "
        "Build = $os.BuildNumber; "
        "DisplayVersion = (Get-ItemProperty "
        r"'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' "
        "-ErrorAction SilentlyContinue).DisplayVersion; "
        "Architecture = $os.OSArchitecture; "
        "InstalledOn = $os.InstallDate "
        "} | ConvertTo-Json"
    ),
    "computer": (
        "$cs = Get-CimInstance Win32_ComputerSystem; "
        "$bios = Get-CimInstance Win32_BIOS; "
        "[PSCustomObject]@{ "
        "Name = $env:COMPUTERNAME; "
        "Manufacturer = $cs.Manufacturer; "
        "Model = $cs.Model; "
        "TotalMemoryGB = [math]::Round($cs.TotalPhysicalMemory/1GB,1); "
        "Serial = $bios.SerialNumber "
        "} | ConvertTo-Json"
    ),
    "battery": (
        "$b = Get-CimInstance Win32_Battery; "
        "if ($null -eq $b) { '{\"OnBattery\": false, "
        "\"Note\": \"no battery - this is a desktop\"}' } "
        "else { [PSCustomObject]@{ "
        "Percent = $b.EstimatedChargeRemaining; "
        "MinutesRemaining = $b.EstimatedRunTime; "
        "Status = $b.BatteryStatus "
        "} | ConvertTo-Json }"
    ),
    "last_boot": (
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "[PSCustomObject]@{ "
        "LastBoot = $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm'); "
        "UptimeHours = [math]::Round(((Get-Date) - "
        "$os.LastBootUpTime).TotalHours,1) "
        "} | ConvertTo-Json"
    ),
    "startup_apps": (
        "Get-CimInstance Win32_StartupCommand | "
        "Select-Object Name, Command, Location, User | ConvertTo-Json"
    ),
}


NETWORK_QUERIES: dict[str, str] = {
    # "What is my IP address?" is one of the most ordinary questions
    # anybody asks a computer, and there was no query for it - the four
    # here were ports and firewall rules. Alfred answered it by writing
    # PowerShell from scratch each time, and the bench caught it getting
    # that wrong: "Your local IP address is not provided in the output."
    #
    # Loopback and the link-local 169.254 block are filtered out: both
    # are addresses the machine gave itself, and neither is the answer
    # to the question.
    "ip_addresses": (
        "Get-NetIPAddress -AddressFamily IPv4 | "
        "Where-Object { $_.IPAddress -notlike '127.*' "
        "-and $_.IPAddress -notlike '169.254.*' } | "
        "Select-Object IPAddress, InterfaceAlias, PrefixLength | "
        "ConvertTo-Json"
    ),
    "adapters": (
        "Get-NetAdapter | Where-Object Status -eq 'Up' | "
        "Select-Object Name, InterfaceDescription, Status, "
        "LinkSpeed, MacAddress | ConvertTo-Json"
    ),
    "wifi": (
        "netsh wlan show interfaces"
    ),
    "listening_ports": (
        "Get-NetTCPConnection -State Listen | "
        "Select-Object LocalAddress, LocalPort, "
        "@{N='Process';E={(Get-Process -Id $_.OwningProcess "
        "-ErrorAction SilentlyContinue).ProcessName}} | "
        "Sort-Object LocalPort | ConvertTo-Json"
    ),
    "blocked_inbound_rules": (
        "Get-NetFirewallRule -Direction Inbound -Action Block "
        "-Enabled True | "
        "Select-Object DisplayName, Profile | "
        "ForEach-Object { "
        "$rule = $_; "
        "$ports = $rule | Get-NetFirewallPortFilter; "
        "[PSCustomObject]@{ "
        "Name = $rule.DisplayName; "
        "Profile = $rule.Profile; "
        "Protocol = $ports.Protocol; "
        "LocalPort = $ports.LocalPort "
        "} } | ConvertTo-Json"
    ),
    "allowed_inbound_rules": (
        "Get-NetFirewallRule -Direction Inbound -Action Allow "
        "-Enabled True | "
        "Select-Object DisplayName, Profile | "
        "ForEach-Object { "
        "$rule = $_; "
        "$ports = $rule | Get-NetFirewallPortFilter; "
        "[PSCustomObject]@{ "
        "Name = $rule.DisplayName; "
        "Profile = $rule.Profile; "
        "Protocol = $ports.Protocol; "
        "LocalPort = $ports.LocalPort "
        "} } | ConvertTo-Json"
    ),
    "firewall_profile_status": (
        "Get-NetFirewallProfile | "
        "Select-Object Name, Enabled, "
        "DefaultInboundAction, DefaultOutboundAction | "
        "ConvertTo-Json"
    ),
}

# Detecting whether a reboot is pending. Pure registry reads, so this
# is safe to run unattended and never modifies anything.
PENDING_REBOOT_QUERY = (
    "$paths = @("
    "'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
    "Component Based Servicing\\RebootPending',"
    "'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
    "WindowsUpdate\\Auto Update\\RebootRequired'"
    "); "
    "$pending = $false; "
    "foreach ($p in $paths) { if (Test-Path $p) { $pending = $true } } "
    "$pfr = Get-ItemProperty "
    "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager' "
    "-Name PendingFileRenameOperations -ErrorAction SilentlyContinue; "
    "if ($pfr) { $pending = $true } "
    "[PSCustomObject]@{ PendingReboot = $pending } | ConvertTo-Json"
)


def run_json_query(
    query: str,
    runner: PowerShellRunner,
    timeout: float = 15.0,
) -> Any:
    """
    Run one of the structured queries above and return parsed JSON,
    or ``None`` on any failure. Never raises.
    """

    try:
        result = runner.run(query, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None

    if not result.success:
        return None

    raw = result.stdout.strip()

    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ====================================================================
# Cheap native probes (no subprocess)
# ====================================================================


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("SystemStatusFlag", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input. 0.0 on failure."""

    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)

        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0

        tick_now = ctypes.windll.kernel32.GetTickCount()

        return max(0.0, (tick_now - info.dwTime) / 1000.0)
    except Exception:  # noqa: BLE001
        return 0.0


@dataclass(frozen=True)
class PowerState:
    on_battery: bool
    percent: int | None


def power_state() -> PowerState:
    """AC/battery status. Falls back to 'on AC, unknown %' on failure."""

    try:
        status = _SYSTEM_POWER_STATUS()

        if not ctypes.windll.kernel32.GetSystemPowerStatus(
            ctypes.byref(status)
        ):
            return PowerState(on_battery=False, percent=None)

        # ACLineStatus: 0 = offline (battery), 1 = online, 255 = unknown.
        on_battery = status.ACLineStatus == 0

        percent: int | None = int(status.BatteryLifePercent)

        if percent == 255:
            percent = None

        return PowerState(on_battery=on_battery, percent=percent)
    except Exception:  # noqa: BLE001
        return PowerState(on_battery=False, percent=None)


def foreground_app() -> str | None:
    """Process name of the current foreground window, or None."""

    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()

        if not hwnd:
            return None

        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        if not pid:
            return None

        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        return None


def is_fullscreen_foreground() -> bool:
    """
    True only when the foreground window is a real borderless fullscreen
    surface (video, game, slideshow) - NOT merely maximised. Used to
    hold back proactive speech.

    A maximised window covers the monitor's work area but keeps its
    caption/frame styles and leaves the taskbar visible; a true
    fullscreen window drops those styles and covers the taskbar too.
    """

    try:
        import win32api
        import win32con
        import win32gui

        hwnd = win32gui.GetForegroundWindow()

        if not hwnd:
            return False

        class_name = win32gui.GetClassName(hwnd)

        if class_name in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
            return False

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)

        # Maximised, or has a title bar / sizing border -> it's a normal
        # window, not fullscreen, regardless of how big it is.
        if style & win32con.WS_MAXIMIZE:
            return False
        if style & (win32con.WS_CAPTION | win32con.WS_THICKFRAME):
            return False

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)

        monitor = win32api.MonitorFromWindow(
            hwnd, win32con.MONITOR_DEFAULTTONEAREST
        )
        info = win32api.GetMonitorInfo(monitor)
        # Compare against the FULL monitor bounds (not the work area):
        # a fullscreen surface covers the taskbar.
        m_left, m_top, m_right, m_bottom = info["Monitor"]

        return (
            left <= m_left
            and top <= m_top
            and right >= m_right
            and bottom >= m_bottom
        )
    except Exception:  # noqa: BLE001
        return False
