from __future__ import annotations

import json
from typing import Any

from src.tools.base import AlfredTool
from src.windows.powershell import PowerShellRunner
from src.windows.system_probe import SYSTEM_QUERIES


# PowerShell one-liners kept structured (ConvertTo-Json) instead of
# left to free-form text so results are parseable and don't rely on
# the model correctly reading column-aligned console output. Shared
# with the background brain via src/windows/system_probe.py so the two
# never drift.
_QUERIES: dict[str, str] = SYSTEM_QUERIES


class SystemInfoTool(AlfredTool):
    name = "system_info"

    description = (
        "Get structured information about the Windows machine itself: "
        "CPU/memory/uptime overview, disk space, or the top resource-"
        "consuming processes. Use this instead of raw PowerShell for "
        "questions like 'how much RAM is free', 'what's using my CPU', "
        "or 'how much disk space do I have left'."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "enum": list(_QUERIES.keys()),
                    "description": (
                        "'overview' for CPU/RAM/uptime, "
                        "'disks' for storage space, "
                        "'top_processes' for the heaviest running processes."
                    ),
                },
            },
            "required": ["query"],
        }

    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self.runner = runner or PowerShellRunner()

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")

        if query not in _QUERIES:
            raise ValueError(
                f"'query' must be one of {sorted(_QUERIES)}."
            )

        result = self.runner.run(_QUERIES[query], timeout=15.0)

        if not result.success:
            return {
                "status": "error",
                "error": result.stderr.strip() or "Command failed.",
            }

        parsed = self._safe_json(result.stdout)

        return {
            "status": "success",
            "query": query,
            "data": parsed if parsed is not None else result.stdout.strip(),
        }

    @staticmethod
    def _safe_json(raw: str) -> Any:
        raw = raw.strip()

        if not raw:
            return None

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
