"""
python -m src.startup  -  what launches at logon, and what it costs.

    list      every startup app, where it comes from, and its RAM now
    sessions  what is running in each Windows session, with totals
    trim      close non-essential apps in ALFRED'S session only
              (--dry-run to see what it would close first)

Why this exists: Alfred's isolated session is created by logging on
again, so Windows starts your whole startup list a second time. On this
machine that was ~24 apps and 50+ processes duplicated - a second
Ollama, a second Discord - quietly costing RAM.

'trim' only ever touches Alfred's own session. Nothing of yours lives
there, and anything closed comes back the next time that session starts.
"""

from __future__ import annotations

import json
import subprocess
import sys

from src.windows.quiet import NO_WINDOW

# Things Alfred's session genuinely needs, or that are unsafe to close.
_ESSENTIAL = {
    "childinputagent",   # Alfred's own agent - the whole point
    "explorer",          # the shell; closing it breaks the session
    "dwm",               # compositing; capture needs it
    "csrss", "winlogon", "wininit", "services", "lsass", "smss",
    "sihost", "ctfmon", "fontdrvhost", "rdpclip", "shellhost",
    "taskhostw", "runtimebroker", "dllhost", "conhost", "svchost",
    "textinputhost", "startmenuexperiencehost", "searchhost",
    "useroobebroker", "smartscreen", "unsecapp",
}


def _ps(command: str, timeout: float = 60.0) -> str:
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False, creationflags=NO_WINDOW)
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _json(command: str) -> list[dict]:
    raw = _ps(command)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def cmd_list(_args: list[str]) -> int:
    rows = _json(
        "Get-CimInstance Win32_StartupCommand | "
        "Select-Object Name,Location,Command | ConvertTo-Json -Compress"
    )
    print(f"{len(rows)} startup app(s):\n")
    for r in rows:
        name = str(r.get("Name") or "?")[:34]
        loc = str(r.get("Location") or "")[:40]
        print(f"  {name:<34}  {loc}")
    print(
        "\nEach of these also starts inside Alfred's session when it opens."
        "\nDisable ones you don't need in Task Manager > Startup apps."
    )
    return 0


def cmd_sessions(_args: list[str]) -> int:
    rows = _json(
        "Get-Process | Group-Object SessionId | ForEach-Object { "
        "[PSCustomObject]@{ Session=$_.Name; Count=$_.Count; "
        "MB=[math]::Round((($_.Group | Measure-Object WorkingSet64 -Sum)"
        ".Sum/1MB),0) } } | ConvertTo-Json -Compress"
    )
    if not rows:
        print("could not read session data")
        return 1
    print(f"{'session':<10}{'processes':>11}{'RAM (MB)':>11}")
    for r in sorted(rows, key=lambda x: str(x.get("Session"))):
        print(f"  {r.get('Session')!s:<8}{r.get('Count', 0):>11}"
              f"{r.get('MB', 0):>11}")

    from src.windows.child_session import child_session_id
    sid = child_session_id()
    print(f"\nAlfred's isolated session: "
          f"{sid if sid is not None else '(not running)'}")
    return 0


def _child_apps() -> tuple[int | None, list[dict]]:
    from src.windows.child_session import (
        ChildSessionClient,
        ChildSessionError,
        child_session_id,
    )

    sid = child_session_id()
    if sid is None:
        return None, []
    client = ChildSessionClient("child")
    try:
        client.connect()
        return sid, client.list_apps()
    except ChildSessionError:
        return sid, []
    finally:
        client.close()


def cmd_trim(args: list[str]) -> int:
    dry = "--dry-run" in args or "-n" in args

    sid, apps = _child_apps()
    if sid is None:
        print("Alfred's isolated session isn't running - nothing to trim.")
        return 0
    if not apps:
        print(f"session {sid} is up but its agent isn't reachable, "
              "or it has no windowed apps.")
        return 1

    keep, close = [], []
    for app in apps:
        name = str(app.get("name") or "")
        (keep if name.lower() in _ESSENTIAL else close).append(app)

    print(f"Alfred's session: {sid}\n")
    if keep:
        print("  keeping:")
        for a in keep:
            print(f"    {a.get('name')}")
    if not close:
        print("\n  nothing to close - the session is already lean.")
        return 0

    print("\n  would close:" if dry else "\n  closing:")
    for a in close:
        title = str(a.get("title") or "")[:40]
        print(f"    {a.get('name')!s:<28} {title}")

    if dry:
        print("\n(dry run - nothing was closed)")
        return 0

    from src.windows.child_session import ChildSessionClient, ChildSessionError

    client = ChildSessionClient("child")
    try:
        client.connect()
        result = client.close_apps([a["pid"] for a in close if "pid" in a])
        print(f"\nclosed {len(result.get('closed', []))}, "
              f"failed {len(result.get('failed', []))}")
        return 0
    except ChildSessionError as exc:
        print(f"\ncould not close: {exc}")
        return 1
    finally:
        client.close()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    handler = {
        "list": cmd_list, "sessions": cmd_sessions, "trim": cmd_trim,
    }.get(argv[0])
    if handler is None:
        print(__doc__)
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
