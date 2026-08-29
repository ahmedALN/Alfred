from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "AlfredAssistant"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _pythonw() -> str:
    """Prefer pythonw.exe so Alfred starts without a console window."""

    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _launch_command() -> str:
    return f'"{_pythonw()}" -m src.main'


def install() -> dict[str, object]:
    """
    Register a Scheduled Task that starts Alfred at logon. Runs in the
    user's own context (no admin), hidden, and does not stop on battery.
    """

    root = _project_root()

    args = [
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/SC", "ONLOGON",
        "/TR", f'cmd /c "cd /d {root} && {_launch_command()}"',
        "/RL", "LIMITED",
        "/F",
    ]

    proc = subprocess.run(args, capture_output=True, text=True)

    if proc.returncode != 0:
        return {
            "status": "error",
            "error": proc.stderr.strip() or proc.stdout.strip(),
        }

    return {
        "status": "installed",
        "task": TASK_NAME,
        "runs": _launch_command(),
        "working_dir": str(root),
    }


def uninstall() -> dict[str, object]:
    proc = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        return {
            "status": "error",
            "error": proc.stderr.strip() or proc.stdout.strip(),
        }

    return {"status": "removed", "task": TASK_NAME}


def status() -> dict[str, object]:
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
    )

    return {
        "status": "enabled" if proc.returncode == 0 else "not_installed",
        "task": TASK_NAME,
        "detail": (proc.stdout or proc.stderr).strip().splitlines()[-1:],
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
