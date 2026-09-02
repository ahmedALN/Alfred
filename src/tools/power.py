"""Sleeping, locking and shutting down the machine.

Asked to put the PC to sleep, Alfred said it was doing it and did
nothing - twice, the second time with the exact command handed to it.
There was no power tool: it had to compose a shell line and hope, and
"hope" is not a capability.

Two things make this less trivial than it looks.

The first is that suspending the machine kills the process that asked
for it. Run SetSuspendState in the foreground and the call blocks until
the machine wakes up again, so Alfred never gets to say "goodnight" -
it just stops mid-sentence. Every state change here is therefore
scheduled a moment ahead and detached, leaving time for the answer to
go out first.

The second is that these are not all the same kind of act. Sleeping and
locking cost nothing; you press a key and everything is where you left
it. Shutting down and restarting close whatever was open, and unsaved
work goes with them. Those two ask first.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from src.tools.base import AlfredTool
from src.windows.quiet import NO_WINDOW

# Long enough for a reply to reach a phone before the screen goes dark,
# short enough that nobody wonders whether it worked.
_GRACE_SECONDS = 3

# Reversible: you lose nothing, and undoing it is a keypress.
_HARMLESS = {"sleep", "lock"}

# These close what is open. Unsaved work does not survive them.
_COSTLY = {"shutdown", "restart", "signout"}


class PowerTool(AlfredTool):
    name = "power"

    description = (
        "Sleep, lock, restart, shut down or sign out of this PC. "
        "actions: sleep (suspend to RAM), lock (lock the screen), "
        "restart, shutdown, signout. Sleep and lock happen straight "
        "away. Restart, shutdown and signout close open programs and "
        "lose unsaved work, so they need confirm=true - ask the user "
        "first and tell them what is open."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["sleep", "lock", "restart", "shutdown", "signout"],
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "Required for restart, shutdown and signout. The "
                        "user must have asked for it knowing what is open."
                    ),
                },
            },
            "required": ["action"],
        }

    def __init__(self, run=None) -> None:
        # Injectable so the tests never actually put the machine to bed.
        self._run = run or _detach

    # ----------------------------------------------------------------

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip().lower()
        action = {"suspend": "sleep", "log off": "signout",
                  "logout": "signout", "log out": "signout",
                  "reboot": "restart", "power off": "shutdown",
                  "turn off": "shutdown"}.get(action, action)

        if action not in _HARMLESS | _COSTLY:
            return {
                "status": "error",
                "error": (
                    "action must be one of "
                    "['sleep', 'lock', 'restart', 'shutdown', 'signout']"
                ),
            }

        if action in _COSTLY and not bool(arguments.get("confirm")):
            return {
                "status": "needs_confirmation",
                "error": (
                    f"{action} closes everything that is open and unsaved "
                    "work is lost. Ask the user to confirm, then call this "
                    "again with confirm=true."
                ),
            }

        if sys.platform != "win32":
            return {"status": "error",
                    "error": "This only knows how to do it on Windows."}

        try:
            self._run(_COMMANDS[action])
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"could not {action}: {exc}"}

        return {
            "status": "success",
            "action": action,
            # Said rather than described, because this answer is the last
            # thing that gets out before the screen goes.
            "said": _SAID[action],
            "in_seconds": _GRACE_SECONDS,
        }


# Sleep is SetSuspendState with hibernate forced off - the first
# argument is "hibernate", and passing 1 there on a machine with
# hibernation enabled writes the RAM to disk instead of suspending,
# which is not what anybody means by "sleep".
_COMMANDS = {
    "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
    "lock": "rundll32.exe user32.dll,LockWorkStation",
    "restart": "shutdown /r /t 0",
    "shutdown": "shutdown /s /t 0",
    "signout": "shutdown /l",
}

_SAID = {
    "sleep": "Putting the PC to sleep now. Goodnight.",
    "lock": "Locking the screen now.",
    "restart": "Restarting now.",
    "shutdown": "Shutting down now.",
    "signout": "Signing you out now.",
}


def _detach(command: str) -> None:
    """Run it in a few seconds, without waiting for it.

    Detached and delayed for the same reason: the machine is about to
    stop being available to the process that asked, so the asking must
    finish first and must not be waiting on the answer.
    """
    subprocess.Popen(
        ["cmd", "/c", f"timeout /t {_GRACE_SECONDS} /nobreak >nul & {command}"],
        creationflags=NO_WINDOW
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
