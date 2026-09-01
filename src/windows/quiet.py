"""Starting things without a window flashing up.

Alfred runs PowerShell constantly - to check disk space, to start an
app, to look at a process. On Windows every one of those spawns a
console, and a console that lives for two hundred milliseconds still
steals focus and still paints a black rectangle over whatever you were
reading. Twenty of them in a minute is not a background assistant.

CREATE_NO_WINDOW is the flag that stops it. This is a single place to
get it from, so that a new subprocess call has an obvious right way to
be written, and so the cost of forgetting is one import rather than a
flicker nobody can reproduce on request.
"""

from __future__ import annotations

import subprocess
import sys

# 0 on anything that is not Windows, where the flag does not exist and
# the problem does not either.
NO_WINDOW: int = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
)


def quietly(**kwargs) -> dict:
    """subprocess keyword arguments, with the window suppressed.

    Use as ``subprocess.run(cmd, **quietly(capture_output=True))``.
    Any creationflags already passed are kept and added to.
    """
    kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | NO_WINDOW
    return kwargs
