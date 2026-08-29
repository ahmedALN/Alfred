"""
python -m src.childsession probe  -  can this machine give Alfred its own
                                     isolated Windows session?

Read-only. Changes nothing, needs no admin. Checks every prerequisite for
Windows Child Sessions - the mechanism behind Power Automate's and
UiPath's "picture-in-picture" - and says go or no-go with reasons.

    probe        run all checks and print a verdict
    explain      what a child session is and what enabling it would cost
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, field

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"

_MARK = {OK: "PASS", WARN: "WARN", FAIL: "FAIL", INFO: "----"}


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, state: str, detail: str = "", fix: str = "") -> None:
        self.checks.append(Check(name, state, detail, fix))

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.state == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.state == WARN]


# ====================================================================
# helpers
# ====================================================================


def _ps(command: str, timeout: float = 20.0) -> str:
    """Run a read-only PowerShell snippet, return stdout (or '')."""
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ====================================================================
# checks
# ====================================================================


def check_edition(r: Report) -> None:
    edition = _ps("(Get-CimInstance Win32_OperatingSystem).Caption")
    build = _ps("(Get-CimInstance Win32_OperatingSystem).BuildNumber")
    label = f"{edition or 'unknown'} (build {build or '?'})"

    low = edition.lower()
    if not edition:
        r.add("Windows edition", WARN, "could not read the edition")
    elif "home" in low:
        r.add(
            "Windows edition", FAIL, label,
            "Child sessions need Pro, Enterprise or Education. Home has no "
            "Remote Desktop host, so this approach cannot work here.",
        )
    else:
        r.add("Windows edition", OK, label)


def check_child_sessions_api(r: Report) -> None:
    """WTSIsChildSessionsEnabled - the read-only half of the API."""
    try:
        wtsapi32 = ctypes.WinDLL("wtsapi32.dll")
        fn = wtsapi32.WTSIsChildSessionsEnabled
        fn.argtypes = [ctypes.POINTER(ctypes.c_bool)]
        fn.restype = ctypes.c_bool
        enabled = ctypes.c_bool(False)
        if not fn(ctypes.byref(enabled)):
            r.add("Child-session API", WARN,
                  f"WTSIsChildSessionsEnabled failed "
                  f"(Win32 error {ctypes.get_last_error()})")
            return
        if enabled.value:
            r.add("Child sessions", OK, "already ENABLED on this machine")
        else:
            r.add(
                "Child sessions", INFO, "supported, currently disabled",
                "Enabling needs one admin call (WTSEnableChildSessions) and "
                "a REBOOT. Nothing else on the machine changes.",
            )
    except AttributeError:
        r.add("Child-session API", FAIL,
              "WTSIsChildSessionsEnabled missing from wtsapi32.dll",
              "This Windows build does not support child sessions.")
    except Exception as exc:  # noqa: BLE001
        r.add("Child-session API", WARN, f"{type(exc).__name__}: {exc}")


def check_child_session_id(r: Report) -> None:
    try:
        wtsapi32 = ctypes.WinDLL("wtsapi32.dll")
        fn = wtsapi32.WTSGetChildSessionId
        fn.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        fn.restype = ctypes.c_bool
        sid = ctypes.c_ulong(0)
        if fn(ctypes.byref(sid)) and sid.value:
            r.add("Existing child session", OK, f"session {sid.value} is live")
        else:
            r.add("Existing child session", INFO, "none right now (expected)")
    except Exception:  # noqa: BLE001
        r.add("Existing child session", INFO, "none right now (expected)")


_TS_KEY = r"HKLM:\System\CurrentControlSet\Control\Terminal Server"
_RDP_TCP_KEY = _TS_KEY + r"\WinStations\RDP-Tcp"


def check_rdp_enabled(r: Report) -> None:
    val = _ps(
        f"(Get-ItemProperty '{_TS_KEY}' -Name fDenyTSConnections"
        " -ErrorAction SilentlyContinue).fDenyTSConnections"
    )
    if val == "0":
        r.add("Remote Desktop host", OK, "enabled (fDenyTSConnections=0)")
    elif val == "1":
        r.add(
            "Remote Desktop host", FAIL, "disabled (fDenyTSConnections=1)",
            "Settings > System > Remote Desktop > On. Child sessions are a "
            "loopback RDP session, so the host must be enabled. This does "
            "NOT expose the machine externally by itself.",
        )
    else:
        r.add("Remote Desktop host", WARN, "could not read the setting")


def check_term_service(r: Report) -> None:
    out = _ps("(Get-Service TermService).Status")
    start = _ps("(Get-Service TermService).StartType")
    if out.lower() == "running":
        r.add("TermService", OK, f"running (start type {start or '?'})")
    elif out:
        r.add("TermService", FAIL, f"{out} (start type {start or '?'})",
              "Remote Desktop Services must be running.")
    else:
        r.add("TermService", WARN, "service not found")


def check_loopback_rdp(r: Report) -> None:
    """The single most important check - can anything reach RDP locally."""
    listening = _port_open("127.0.0.1", 3389)
    if listening:
        r.add("Loopback RDP (127.0.0.1:3389)", OK, "listening and reachable")
        return
    port = _ps(
        f"(Get-ItemProperty '{_RDP_TCP_KEY}' -Name PortNumber"
        " -ErrorAction SilentlyContinue).PortNumber"
    )
    if port and port.isdigit() and port != "3389":
        if _port_open("127.0.0.1", int(port)):
            r.add("Loopback RDP", OK, f"listening on custom port {port}")
            return
    r.add(
        "Loopback RDP (127.0.0.1:3389)", FAIL, "nothing listening",
        "Without a local RDP listener a child session cannot be created. "
        "Usually means Remote Desktop is off, or a firewall rule is "
        "blocking loopback.",
    )


def check_rdp_activex(r: Report) -> None:
    """The host needs the RDP ActiveX control (mstscax.dll)."""
    path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "System32", "mstscax.dll")
    if os.path.exists(path):
        r.add("RDP ActiveX control", OK, "mstscax.dll present")
    else:
        r.add("RDP ActiveX control", FAIL, "mstscax.dll not found",
              "The child-session host is built on this control.")


def check_rdp_certificate(r: Report) -> None:
    thumb = _ps(
        f"(Get-ItemProperty '{_RDP_TCP_KEY}' -Name SSLCertificateSHA1Hash"
        " -ErrorAction SilentlyContinue).SSLCertificateSHA1Hash"
    )
    if thumb:
        r.add("RDP certificate", OK, "a listener certificate is configured")
    else:
        r.add("RDP certificate", WARN, "no explicit certificate thumbprint",
              "Windows normally self-signs one on first use. Only a problem "
              "if the connection later fails on trust.")


def check_join_state(r: Report) -> None:
    out = _ps("dsregcmd /status", timeout=25.0)
    if not out:
        r.add("Domain / Entra join", INFO, "could not determine")
        return
    azure = "AzureAdJoined : YES" in out
    domain = "DomainJoined : YES" in out
    if azure:
        r.add(
            "Domain / Entra join", WARN, "Entra (Azure AD) joined",
            "Microsoft documents child sessions as unsupported on "
            "Entra-joined cloud machines. Worth testing anyway, but this is "
            "the most likely thing to bite.",
        )
    elif domain:
        r.add("Domain / Entra join", INFO, "domain joined")
    else:
        r.add("Domain / Entra join", OK, "local account / workgroup")


def check_startup_apps(r: Report) -> None:
    """Startup apps launch in BOTH sessions - worth knowing up front."""
    out = _ps(
        "@(Get-CimInstance Win32_StartupCommand | "
        "Select-Object -ExpandProperty Name) -join ', '"
    )
    items = [i.strip() for i in out.split(",") if i.strip()] if out else []
    if not items:
        r.add("Startup apps", OK, "none found - nothing will double up")
    else:
        preview = ", ".join(items[:6]) + ("..." if len(items) > 6 else "")
        r.add(
            "Startup apps", WARN, f"{len(items)} would launch twice: {preview}",
            "Anything set to run at logon also starts inside the child "
            "session. Usually harmless, but VPN clients and chat apps can "
            "conflict with themselves.",
        )


def check_session_and_rights(r: Report) -> None:
    try:
        sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
        r.add("Console session", INFO, f"session {sid}")
    except Exception:  # noqa: BLE001
        pass
    if _is_admin():
        r.add("This process", INFO, "running as administrator")
    else:
        r.add(
            "This process", INFO, "running as a normal user",
            "Fine for probing. Enabling child sessions later needs one "
            "elevated command.",
        )


def check_memory(r: Report) -> None:
    free = _ps(
        "[math]::Round((Get-CimInstance Win32_OperatingSystem)."
        "FreePhysicalMemory/1MB,1)"
    )
    try:
        gb = float(free)
    except ValueError:
        return
    if gb < 3:
        r.add("Free memory", WARN, f"{gb} GB free",
              "A second session needs headroom - a desktop plus its apps.")
    else:
        r.add("Free memory", OK, f"{gb} GB free")


ALL_CHECKS = (
    check_edition,
    check_child_sessions_api,
    check_child_session_id,
    check_rdp_enabled,
    check_term_service,
    check_loopback_rdp,
    check_rdp_activex,
    check_rdp_certificate,
    check_join_state,
    check_startup_apps,
    check_session_and_rights,
    check_memory,
)


# ====================================================================
# CLI
# ====================================================================


def run_probe() -> Report:
    r = Report()
    for fn in ALL_CHECKS:
        try:
            fn(r)
        except Exception as exc:  # noqa: BLE001
            r.add(fn.__name__, WARN, f"check crashed: {exc}")
    return r


def cmd_probe(_args: list[str]) -> int:
    print("Probing this machine for Windows Child Session support...")
    print("(read-only - nothing is changed)\n")

    r = run_probe()
    width = max(len(c.name) for c in r.checks) + 2

    for c in r.checks:
        print(f"  [{_MARK[c.state]}] {c.name:<{width}} {c.detail}")
        if c.fix:
            for line in _wrap(c.fix, 72):
                print(f"         {line}")

    print()
    if r.blockers:
        print("VERDICT: blocked. Fix these before child sessions can work:")
        for c in r.blockers:
            print(f"  - {c.name}: {c.detail}")
        return 1

    print("VERDICT: this machine looks capable of running a child session.")
    if r.warnings:
        print("\nWorth knowing first:")
        for c in r.warnings:
            print(f"  - {c.name}: {c.detail}")
    print(
        "\nNext step needs your go-ahead: enabling child sessions is one\n"
        "elevated call plus a reboot. Run `python -m src.childsession explain`\n"
        "for exactly what that does and how to undo it."
    )
    return 0


def cmd_explain(_args: list[str]) -> int:
    print(_EXPLAIN.strip())
    return 0


_EXPLAIN = """
WHAT A CHILD SESSION IS

  A second Windows session on this same PC, tied to your logon. It has its
  own desktop, its own mouse/keyboard input queue and its own window
  manager. A program running inside it can click and type freely without
  touching your screen, your focus or your clipboard.

  It is the same mechanism behind Microsoft Power Automate's and UiPath's
  "picture-in-picture" automation. It is a documented Windows API, free,
  and needs no Remote Desktop licensing because it is a loopback session.

WHAT ENABLING IT ACTUALLY DOES

  One elevated call:      WTSEnableChildSessions(TRUE)
  Then:                   a reboot, for it to take effect.

  That flag lets Windows create a child session on request. It does not
  open the machine to the network, does not add a user account, and does
  not change how you log in.

TO UNDO IT

  The same call with FALSE, and a reboot. Nothing else to clean up.

WHAT IT WOULD COST YOU DAY TO DAY

  - Anything in your startup apps also launches inside Alfred's session.
  - The PC cannot be restarted while the child session is open, so Alfred
    has to close it on shutdown.
  - Office refuses to run in two sessions at once; browsers and Spotify
    need a separate profile folder per session.
  - Logging out of Windows closes Alfred's session too.

WHAT IT WOULD NOT SOLVE

  Full-screen games. A child session is RDP-backed: no exclusive
  fullscreen and software rendering by default. Games should stay on your
  real desktop.

THE ONE UNKNOWN

  Whether Alfred's session keeps running when you lock your screen with
  Win+L. The docs say a child session cannot itself be locked, but say
  nothing about the parent locking. This has to be tested, not assumed -
  and it is the single thing most likely to kill the idea.
"""


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    handler = {"probe": cmd_probe, "explain": cmd_explain}.get(argv[0])
    if handler is None:
        print(__doc__)
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
