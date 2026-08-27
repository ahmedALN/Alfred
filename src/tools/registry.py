from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool


class ToolRegistry:
    """Central registry for Alfred's capabilities."""

    def __init__(self) -> None:
        self._tools: dict[str, AlfredTool] = {}

    def register(self, tool: AlfredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(
                f"Tool already registered: {tool.name}"
            )

        self._tools[tool.name] = tool

    def get(self, name: str) -> AlfredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            available = ", ".join(sorted(self._tools))
            raise KeyError(
                f"Unknown Alfred tool '{name}'. "
                f"Available tools: {available or 'none'}"
            ) from exc

    def list(self) -> list[AlfredTool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return sorted(self._tools)

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self.get(name)
        return tool.execute(arguments)

    def gemini_declarations(self) -> list[dict[str, Any]]:
        """
        Convert every registered Alfred tool into a Gemini
        function declaration.
        """

        return [
            tool.gemini_declaration()
            for tool in self.list()
        ]