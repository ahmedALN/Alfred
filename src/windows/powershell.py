from __future__ import annotations

import subprocess

from src.windows.quiet import NO_WINDOW
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    command: str
    stdout: str
    stderr: str
    return_code: int
    duration_ms: int

    @property
    def success(self) -> bool:
        return self.return_code == 0


class PowerShellRunner:
    """Execute PowerShell commands and return structured results."""

    def __init__(self, executable: str = "powershell.exe") -> None:
        self.executable = executable

    def run(self, command: str, timeout: float = 30.0) -> CommandResult:
        if not command.strip():
            raise ValueError("PowerShell command cannot be empty.")

        started = time.perf_counter()

        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = int((time.perf_counter() - started) * 1000)

            stdout = exc.stdout or ""
            stderr = exc.stderr or ""

            return CommandResult(
                command=command,
                stdout=stdout,
                stderr=f"Command timed out after {timeout:.1f}s.\n{stderr}",
                return_code=-1,
                duration_ms=elapsed,
            )

        elapsed = int((time.perf_counter() - started) * 1000)

        return CommandResult(
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            return_code=completed.returncode,
            duration_ms=elapsed,
        )