"""The things that decide whether this survives being cloned.

A commit here is titled "Ready for someone who is not me", and the
suite it points at is the most useful thing in the repository for
anybody deciding whether to run Alfred on their own machine. But the
suite ran only when somebody remembered to run it, ruff had no idea
which rules the project had agreed to, and every dependency was pinned
with a bare `>=` against APIs that have already moved twice underneath
it - `gemini-2.5-flash` and `text-embedding-004` both started answering
404 mid-project.

These are cheap checks on the scaffolding, and each of them was
something that was actually missing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ====================================================================
# Configuration exists and says something
# ====================================================================


def test_there_is_a_project_file():
    assert (ROOT / "pyproject.toml").exists()


def test_pytest_is_configured_in_one_place():
    config = tomllib.loads(read("pyproject.toml"))
    pytest_config = config["tool"]["pytest"]["ini_options"]

    assert pytest_config["testpaths"] == ["tests"]
    assert "--strict-markers" in pytest_config["addopts"]


def test_addopts_does_not_swallow_the_summary_line():
    """`pytest -q` is what people type; a -q in addopts makes that -qq,
    which hides the "N passed" line that is the point of running it."""
    config = tomllib.loads(read("pyproject.toml"))

    assert "-q" not in config["tool"]["pytest"]["ini_options"]["addopts"].split()


def test_ruff_is_configured_and_selects_the_rules_that_matter_here():
    config = tomllib.loads(read("pyproject.toml"))
    selected = config["tool"]["ruff"]["lint"]["select"]

    # This program runs shell commands and reads timestamps for a
    # living; both have already cost it a real bug.
    assert "S" in selected
    assert "DTZ" in selected
    # And the blind-except suppressions all over the codebase only
    # mean something if the rule they name is switched on.
    assert "BLE" in selected


def test_every_marker_used_is_registered():
    """--strict-markers turns a typo into a failure, so the list has to
    be complete."""
    config = tomllib.loads(read("pyproject.toml"))
    registered = {
        line.split(":")[0]
        for line in config["tool"]["pytest"]["ini_options"]["markers"]
    }

    used = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("@pytest.mark."):
                name = stripped.removeprefix("@pytest.mark.").split("(")[0]
                if name not in ("parametrize", "skip", "skipif", "xfail", "usefixtures"):
                    used.add(name)

    assert used <= registered, f"unregistered markers: {sorted(used - registered)}"


# ====================================================================
# Dependencies
# ====================================================================


def test_every_dependency_has_a_ceiling():
    """A bare `>=` installs whatever those projects released this
    morning. That is not theoretical here."""
    unbounded = []

    for line in read("requirements.txt").splitlines():
        line = line.split("#")[0].strip()

        if not line or line.startswith("#"):
            continue

        requirement = line.split(";")[0].strip()

        if ">=" in requirement and "<" not in requirement:
            unbounded.append(requirement)

    assert not unbounded, f"no upper bound on: {unbounded}"


def test_the_windows_only_dependencies_say_so():
    """So `pip install -r requirements.txt` works on a CI runner and on
    anything that is not Windows."""
    text = read("requirements.txt")

    for package in ("pyvda", "pywin32", "pywinauto"):
        line = next(
            line for line in text.splitlines()
            if line.strip().startswith(package)
        )
        assert 'sys_platform == "win32"' in line


# ====================================================================
# Continuous integration
# ====================================================================


def test_the_suite_runs_somewhere_other_than_this_laptop():
    assert (ROOT / ".github" / "workflows").is_dir()


def test_ci_runs_the_tests_on_windows():
    """A suite that passes on Linux is testing a different program:
    pywin32, pywinauto and the virtual-desktop bindings do not install
    anywhere else."""
    workflow = read(".github/workflows/tests.yml")

    assert "windows-latest" in workflow
    assert "pytest" in workflow


def test_ci_lints_with_a_pinned_ruff():
    """An unpinned linter turns somebody else's release into your red
    build."""
    workflow = read(".github/workflows/tests.yml")

    assert "ruff==" in workflow


# ====================================================================
# Nothing private leaves the machine
# ====================================================================


@pytest.mark.parametrize("pattern", [
    ".env",
    "gmail_token.json",
    "gmail_client.json",
    "alfred_*.sqlite3",
    "*.jsonl",
    "logs/",
    "models/",
    ".alfred_interface_url",
])
def test_the_private_things_are_ignored(pattern):
    """The stores are not caches - they hold what Alfred has learned
    about you, your contacts, and what has been on your screen."""
    assert pattern in read(".gitignore")


def test_no_private_file_is_actually_tracked():
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout.splitlines()

    leaked = [
        name for name in tracked
        if name.endswith((".sqlite3", ".jsonl"))
        or name in (".env", "gmail_token.json", "gmail_client.json")
    ]

    assert not leaked, f"tracked by git: {leaked}"


# ====================================================================
# Documentation keeps up
# ====================================================================


def test_the_readme_mentions_the_doctor():
    """A self-check nobody knows about is a self-check nobody runs."""
    assert "src.doctor" in read("README.md")


def test_every_command_the_readme_lists_exists():
    import importlib
    import re

    modules = set(re.findall(r"python -m (src\.[\w.]+)", read("README.md")))

    missing = []
    for name in sorted(modules):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)

    assert not missing, f"README lists commands that do not import: {missing}"
