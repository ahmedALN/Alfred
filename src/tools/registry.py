from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool


class ToolRegistry:
    """Registry of tools available to Alfred."""

    def __init__(self) -> None:
        self._tools: dict[str, AlfredTool] = {}

    def register(self, tool: AlfredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")

        self._tools[tool.name] = tool

    def get(self, name: str) -> AlfredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown Alfred tool: {name}") from exc

    def list(self) -> list[AlfredTool]:
        return list(self._tools.values())

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return self.get(name).execute(arguments)