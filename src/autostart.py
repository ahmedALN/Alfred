from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.windows.quiet import NO_WINDOW

TASK_NAME = "AlfredAssistant"
SHORTCUT_NAME = "Alfred.vbs"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _pythonw() -> str:
    """Prefer pythonw.exe so Alfred starts without a console window."""

    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _launch_command() -> str:
    # The watchdog keeps Alfred alive across crashes.
    return f'"{_pythonw()}" -m src.watchdog'


def _startup_folder() -> Path:
    return Path(os.environ["APPDATA"]) / (
        "Microsoft/Windows/Start Menu/Programs/Startup"
    )


def _shortcut() -> Path:
    return _startup_folder() / SHORTCUT_NAME


def _write_vbs_launcher() -> Path:
    """The one hidden-launch mechanism, shared by the scheduled task
    and the Startup-folder fallback below. A one-line script rather
    than a .lnk shortcut, because a .lnk needs COM to write and this
    needs nothing - and VBScript is used at all for one property
    neither a shortcut nor a batch file has: ``WScript.Shell.Run``'s
    window style argument. ``0`` there is what actually suppresses a
    console window; Task Scheduler's own action has no such setting of
    its own, and a bare ``cmd /c`` has no equivalent either, which is
    how a black box ends up flashing - or, worse, sitting there the
    whole time Alfred runs, since cmd waits on whatever it launched -
    across every boot instead.
    """
    root = _project_root()
    lines = [
        'Set sh = CreateObject("WScript.Shell")',
        'sh.CurrentDirectory = "' + str(root) + '"',
        'sh.Run """' + _pythonw() + '"" -m src.watchdog", 0, False',
    ]
    script = '\r\n'.join(lines) + '\r\n'

    target = _shortcut()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8")
    return target


def _install_shortcut() -> dict[str, object]:
    """Start Alfred from the user's own Startup folder - needs no
    administrator rights, unlike the scheduled task above it."""
    target = _write_vbs_launcher()

    return {
        "status": "installed",
        "how": "startup folder",
        "at": str(target),
        "runs": _launch_command(),
        "working_dir": str(_project_root()),
    }


def install() -> dict[str, object]:
    """Start Alfred at logon.

    The scheduled task is tried first because it is the tidier of the
    two - it survives, it can be inspected, it restarts on failure. It
    also needs administrator rights, which Alfred does not have and is
    not going to ask for. When it is refused, the user's own Startup
    folder does the same job and needs nothing.
    """
    task = _install_task()
    if task.get("status") == "installed":
        return task

    shortcut = _install_shortcut()
    shortcut["note"] = (
        "the scheduled task needed administrator rights, so this went in "
        "your Startup folder instead - same result, nothing elevated"
    )
    return shortcut


def _install_task() -> dict[str, object]:
    """
    Register a Scheduled Task that starts Alfred at logon. Runs in the
    user's own context, hidden, and does not stop on battery.

    /TR used to be a raw ``cmd /c "cd /d ... && pythonw ..."`` - which
    needed cmd to set the working directory, and cmd.exe always owns a
    console. Task Scheduler has no setting to hide that console short
    of a full XML task definition, so at every boot it either flashed
    briefly or - since cmd was waiting on pythonw the entire time
    Alfred ran, not backgrounding it - sat there visibly for the whole
    session. Pointing /TR at the same hidden VBScript launcher the
    Startup-folder fallback uses (wscript.exe has no console at all,
    and WScript.Shell.Run's own window-style argument handles both the
    working directory and hiding pythonw) fixes both at once.
    """

    vbs = _write_vbs_launcher()

    args = [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/SC", "ONLOGON",
        "/TR", f'wscript.exe //B "{vbs}"',
        "/RL", "LIMITED",
        "/F",
    ]

    proc = subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW)

    if proc.returncode != 0:
        return {
            "status": "error",
            "error": proc.stderr.strip() or proc.stdout.strip(),
        }

    return {
        "status": "installed",
        "task": TASK_NAME,
        "runs": _launch_command(),
        "working_dir": str(_project_root()),
    }


def uninstall() -> dict[str, object]:
    """Take out whichever of the two is there. Possibly both."""
    removed = []

    proc = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True, creationflags=NO_WINDOW)
    if proc.returncode == 0:
        removed.append("scheduled task")

    target = _shortcut()
    if target.exists():
        target.unlink()
        removed.append("startup folder")

    if not removed:
        return {"status": "not_installed"}
    return {"status": "removed", "removed": removed}


def status() -> dict[str, object]:
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True, creationflags=NO_WINDOW)
    by_task = proc.returncode == 0
    by_folder = _shortcut().exists()

    how = []
    if by_task:
        how.append("scheduled task")
    if by_folder:
        how.append("startup folder")

    return {
        "status": "enabled" if how else "not_installed",
        "how": how,
        "task": TASK_NAME,
    }


def _main(argv: list[str]) -> int:
    action = argv[0] if argv else "status"
    fn = {"install": install, "uninstall": uninstall, "status": status}.get(action)

    if fn is None:
        print("usage: python -m src.autostart [install|uninstall|status]")
        return 2

    result = fn()
    for key, value in result.items():
        print(f"{key}: {value}")

    return 0 if result.get("status") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
