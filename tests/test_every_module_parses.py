"""Every module Alfred ships is at least valid Python.

Written after `python -m src.whatsapp` shipped with a broken string
literal: nothing in the suite imported that file, so nothing noticed.
Entry points are exactly the files no test tends to touch, and exactly
the ones a person runs by hand. Parsing is cheap and imports nothing, so
it covers the modules that need a display, a GPU or a live connection
just as well as the rest.
"""

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SOURCES = sorted((_ROOT / "src").rglob("*.py"))


def _name(path: Path) -> str:
    return str(path.relative_to(_ROOT)).replace("\\", "/")


def test_there_is_something_to_check():
    assert len(_SOURCES) > 20


@pytest.mark.parametrize("path", _SOURCES, ids=_name)
def test_it_parses(path: Path):
    # utf-8-sig: a byte-order mark is what PowerShell leaves behind, and
    # Python itself copes with one, so it is not a failure.
    source = path.read_text(encoding="utf-8-sig")
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        pytest.fail(f"{_name(path)} line {exc.lineno}: {exc.msg}")
