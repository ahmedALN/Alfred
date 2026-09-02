"""Looking at the world after a step, rather than at the log about it.

Every check Alfred had was a reading of its own transcript. The
verifier is handed the tool log and asked whether the step worked,
which makes it a judge of what the tools SAID. When a tool says the
wrong thing, nothing downstream can tell.

That is not a hypothetical. The launcher reported "Launched; window
not confirmed yet" as a success for anything it started, so a game
that opened, found no launcher running and closed again inside a
second was reported as opened. The transcript was accurate; the world
disagreed with it.

So: after a step claims to have worked, go and look. Is the app still
running? Does the file exist? These are facts, they cost milliseconds,
and no amount of confident wording in a tool result can talk over one.

The rule throughout is that a check may only ever REFUTE a claim it
has positive evidence against. "I could not tell" is not a failure -
a guard that fires on the absence of evidence is worse than no guard,
because it turns working steps into retries.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Aftercheck:
    """What was looked at, and what was actually there."""

    ok: bool
    what: str      # what was checked, in plain words
    detail: str    # the fact that was found

    def __str__(self) -> str:
        return f"{self.what}: {self.detail}"


# How long to let something settle before believing it opened. A game
# that needs a launcher is gone well inside this; an app that is merely
# slow is alive throughout it, which is the distinction being drawn.
SETTLE = 1.5


def _text(result: Any, *keys: str) -> str:
    if not isinstance(result, dict):
        return ""
    for k in keys:
        v = result.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _check_open_app(
    args: dict[str, Any], result: dict[str, Any], settle: float
) -> Aftercheck | None:
    """Did the app that was opened stay open?"""
    from src.windows.apps import _pid_alive, running_image, shortcut_target

    # The tool already knows it failed; nothing to add.
    if str(result.get("status", "")).lower() not in ("", "success"):
        return None

    # Opened on the private desktop, or reused a window that was already
    # there - neither is a fresh process this can follow.
    if result.get("opened_in") or result.get("method") == "existing-window":
        return None

    executable = _text(result, "executable")
    pid = result.get("pid")
    name = _text(result, "app") or str(args.get("app") or "the app")

    exe = executable
    if exe.lower().endswith((".lnk", ".url")):
        exe = shortcut_target(exe)[0]
    if not exe.lower().endswith(".exe"):
        # A Store app or a URI: the shell starts something this cannot
        # name, so there is nothing honest to check.
        return None

    time.sleep(settle)

    if _pid_alive(pid if isinstance(pid, int) else None):
        return Aftercheck(True, f"{name} still running", f"pid {pid} is alive")

    found = running_image(exe)
    if found:
        return Aftercheck(
            True, f"{name} still running", f"pid {found} is alive"
        )

    return Aftercheck(
        False,
        f"{name} is not running",
        f"{os.path.basename(exe)} started and exited within "
        f"{settle:.0f}s without a window",
    )


def _check_written_path(
    args: dict[str, Any], result: dict[str, Any]
) -> Aftercheck | None:
    """A step that says it made a file: is the file there?"""
    target = _text(result, "path", "file", "folder", "destination")
    if not target:
        target = _text(args, "path", "file", "folder", "destination")
    if not target or len(target) < 3:
        return None
    # Only judge a real, absolute local path. Anything else is a name
    # this cannot resolve, and guessing is how false failures start.
    if not (len(target) > 2 and target[1] == ":"):
        return None

    if os.path.exists(target):
        return Aftercheck(True, "the path exists", target)
    return Aftercheck(False, "the path is not there", f"{target} does not exist")


def check(
    tool: str,
    args: dict[str, Any] | None,
    result: Any,
    *,
    settle: float = SETTLE,
) -> Aftercheck | None:
    """Go and look. None means there was nothing factual to look at."""
    if not isinstance(result, dict):
        return None
    args = args or {}

    try:
        if tool == "open_app":
            return _check_open_app(args, result, settle)
        if tool in ("powershell", "desktop_control"):
            return _check_written_path(args, result)
    except Exception:  # noqa: BLE001
        # A check that breaks must never fail the step it was checking.
        return None
    return None
