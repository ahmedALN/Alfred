from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AlfredTool(ABC):
    """Base interface for every Alfred tool."""

    name: str
    description: str

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool and return a JSON-serializable result."""
        raise NotImplementedError