from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool
from src.windows.powershell import PowerShellRunner


class PowerShellTool(AlfredTool):
    name = "powershell"

    description = (
        "Execute a PowerShell command on the Windows computer. "
        "Use this when a suitable specialized Alfred tool does not "
        "exist or when direct Windows system access is required."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The PowerShell command to execute."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Maximum execution time in seconds. "
                        "Defaults to 30."
                    ),
                },
            },
            "required": ["command"],
        }

    def __init__(
        self,
        runner: PowerShellRunner | None = None,
    ) -> None:
        self.runner = runner or PowerShellRunner()

    def execute(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        command = arguments.get("command")
        timeout = arguments.get("timeout", 30.0)

        if not isinstance(command, str):
            raise ValueError(
                "'command' must be a string."
            )

        if not command.strip():
            raise ValueError(
                "'command' cannot be empty."
            )

        if not isinstance(timeout, (int, float)):
            raise ValueError(
                "'timeout' must be a number."
            )

        result = self.runner.run(
            command,
            float(timeout),
        )

        return {
            "success": result.success,
            "command": result.command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.return_code,
            "duration_ms": result.duration_ms,
        }