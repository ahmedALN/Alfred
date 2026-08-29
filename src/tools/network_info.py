from __future__ import annotations

import json
from typing import Any

from src.tools.base import AlfredTool
from src.windows.powershell import PowerShellRunner
from src.windows.system_probe import NETWORK_QUERIES


# Shared with the background brain via src/windows/system_probe.py.
_QUERIES: dict[str, str] = NETWORK_QUERIES


class NetworkInfoTool(AlfredTool):
    name = "network_info"

    description = (
        "Get structured information about this machine's network and "
        "firewall state. Use 'blocked_inbound_rules' for questions "
        "like 'what ports are blocked on my firewall', "
        "'allowed_inbound_rules' for what's explicitly allowed in, "
        "'listening_ports' for what's actually open and listening "
        "right now (with the owning process), and "
        "'firewall_profile_status' for whether the firewall itself "
        "is on and its default policy per network profile."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "enum": list(_QUERIES.keys()),
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

        # Firewall rule enumeration can be slow on machines with many
        # rules (enterprise AV/VPN software adds hundreds), so give
        # it more headroom than a typical system_info query.
        timeout = 25.0 if "rules" in query else 15.0

        result = self.runner.run(_QUERIES[query], timeout=timeout)

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
