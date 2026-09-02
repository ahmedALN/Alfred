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

    def normalise_arguments(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """
        The call the model meant, when the label is the only thing wrong.

        Sixty-two of the seventy-four limitations Alfred has learned are
        a tool refusing a call over the name on an argument - not over
        anything it could not do. Overriding this lets a tool accept the
        synonyms models actually reach for.

        It must never guess: move a value to the argument it plainly
        belongs in, fill an enum only when the rest of the call admits
        one answer, and otherwise leave the arguments alone so the tool
        can refuse them properly.
        """

        return arguments

    def gemini_declaration(self) -> dict[str, Any]:
        """Convert this tool into a Gemini function declaration."""

        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }
