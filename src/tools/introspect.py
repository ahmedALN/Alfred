from __future__ import annotations

from typing import Any

from src.capabilities import describe_capabilities
from src.tools.base import AlfredTool


class WhatCanYouDoTool(AlfredTool):
    name = "what_can_you_do"

    description = (
        "Return an accurate description of what Alfred is and everything it "
        "can currently do. Call this when the user asks what you are, what "
        "you can do, what your capabilities or limits are, or how you work."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def __init__(
        self, registry: Any, settings: Any, resource_mode: Any = None,
        brain_enabled: bool = True,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._resource_mode = resource_mode
        self._brain_enabled = brain_enabled

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        return {
            "status": "success",
            "description": describe_capabilities(
                self._registry,
                self._settings,
                resource_mode=self._resource_mode,
                brain_enabled=self._brain_enabled,
            ),
        }
