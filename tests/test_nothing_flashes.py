"""No console should ever paint itself over what you were reading.

Alfred spawns processes constantly - PowerShell for disk space, an app
launcher, a scheduled-task query. On Windows every one of those makes a
console, and a console that lives for two hundred milliseconds still
steals focus and still paints a black rectangle. Twenty a minute is not
a background assistant.

`src/windows/quiet.py` has the flag. This checks that every spawn
actually uses it, because "remember to pass creationflags" is not a
mechanism - thirteen calls had quietly forgotten.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Where a visible console is the point rather than a bug.
ALLOWED_TO_SHOW = {
    # The interactive setup: pip's progress and a .NET build are what
    # somebody is sitting there watching, and silence reads as a hang.
    "src/setup.py",
    # The example in the docstring that defines the helper.
    "src/windows/quiet.py",
}


def _spawns(text: str):
    """Every subprocess call in a file, with its full argument list."""
    for match in re.finditer(
        r"subprocess\.(?:run|Popen|check_output|call)\s*\(", text
    ):
        chunk = text[match.start():match.start() + 1200]
        depth = 0
        for index, char in enumerate(chunk):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield text[:match.start()].count("\n") + 1, chunk[:index]
                    break


def test_every_spawn_hides_its_window():
    guilty = []

    for path in sorted(ROOT.joinpath("src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()

        if relative in ALLOWED_TO_SHOW:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")

        for line, call in _spawns(text):
            if "creationflags" not in call and "NO_WINDOW" not in call:
                guilty.append(f"{relative}:{line}")

    assert not guilty, (
        "these spawn a visible console - pass creationflags=NO_WINDOW "
        "(from src.windows.quiet), or add the file to ALLOWED_TO_SHOW "
        "with a reason: " + ", ".join(guilty)
    )


def test_the_flag_is_real_on_windows():
    import sys

    from src.windows.quiet import NO_WINDOW

    if sys.platform == "win32":
        assert NO_WINDOW != 0
    else:
        assert NO_WINDOW == 0


def test_quietly_keeps_flags_it_was_given():
    from src.windows.quiet import NO_WINDOW, quietly

    out = quietly(capture_output=True, creationflags=0x10)

    assert out["capture_output"] is True
    assert out["creationflags"] == (0x10 | NO_WINDOW)


def test_alfred_starts_without_a_console_at_boot():
    """pythonw, not python: the difference between booting invisibly
    and booting with a black window on your desktop."""
    from src import autostart

    source = pathlib.Path(autostart.__file__).read_text(encoding="utf-8")

    assert "pythonw" in source, "the boot entry would open a console"
