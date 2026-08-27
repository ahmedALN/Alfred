from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool
from src.windows.powershell import PowerShellRunner


class PowerShellTool(AlfredTool):
    name = "powershell"
    description = (
        "Execute a PowerShell command on the Windows computer "
        "and return stdout, stderr, exit code, and duration."
    )

    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self.runner = runner or PowerShellRunner()

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = arguments.get("command")
        timeout = arguments.get("timeout", 30.0)

        if not isinstance(command, str) or not command.strip():
            raise ValueError("powershell requires a non-empty 'command'.")

        if not isinstance(timeout, (int, float)):
            raise ValueError("'timeout' must be a number.")

        result = self.runner.run(command, float(timeout))

        return {
            "success": result.success,
            "command": result.command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.return_code,
            "duration_ms": result.duration_ms,
        }