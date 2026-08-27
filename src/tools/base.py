from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AlfredTool(ABC):
    """Base interface for every Alfred capability."""

    name: str
    description: str

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """
        JSON Schema describing the arguments accepted by the tool.
        """
        raise NotImplementedError

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the tool.

        The returned dictionary must be JSON-serializable.
        """
        raise NotImplementedError

    def gemini_declaration(self) -> dict[str, Any]:
        """Convert this tool into a Gemini function declaration."""

        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }