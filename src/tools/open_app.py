from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool
from src.windows.apps import AppLauncher


class OpenAppTool(AlfredTool):
    name = "open_app"

    description = (
        "Open a Windows application and place its window on "
        "a virtual desktop. By default, applications requested "
        "by Alfred are placed on Alfred's desktop. Use target='user' "
        "only when the user explicitly wants the application on "
        "their own desktop. Use target='current' to leave the "
        "application on the currently active desktop."
    )

    @property
    def parameters_schema(
        self,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": (
                        "Application to open, such as "
                        "Calculator, Chrome, Notepad, or VS Code."
                    ),
                },
                "target": {
                    "type": "string",
                    "enum": [
                        "alfred",
                        "user",
                        "current",
                    ],
                    "description": (
                        "Virtual desktop target. "
                        "Use 'alfred' by default. "
                        "Use 'user' only when the user explicitly "
                        "requests their desktop. "
                        "Use 'current' when the application should "
                        "remain on the currently active desktop."
                    ),
                },
            },
            "required": ["app"],
        }

    def __init__(
        self,
        launcher: AppLauncher | None = None,
    ) -> None:
        self.launcher = (
            launcher
            or AppLauncher()
        )

    def execute(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        app = arguments.get(
            "app"
        )

        target = arguments.get(
            "target",
            "alfred",
        )

        if not isinstance(
            app,
            str,
        ):
            raise ValueError(
                "'app' must be a string."
            )

        if target not in {
            "alfred",
            "user",
            "current",
        }:
            raise ValueError(
                "'target' must be "
                "'alfred', 'user', or 'current'."
            )

        result = self.launcher.open(
            app_name=app,
            target=target,
        )

        return result.as_dict()

    def close(self) -> None:
        self.launcher.close()