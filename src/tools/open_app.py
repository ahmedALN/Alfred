from __future__ import annotations

from typing import Any

from src.tools.base import AlfredTool
from src.windows.apps import AppLauncher


class OpenAppTool(AlfredTool):
    name = "open_app"

    description = (
        "Open anything on Windows by natural name: an app (Spotify, "
        "Chrome, VS Code, Calculator, any Store app or Start-menu "
        "entry), a website (youtube, github.com), or a file/folder "
        "path. If it's already open, its window is reused. "
        "target='user' puts it on the user's desktop, 'current' leaves "
        "it where it opens, otherwise it goes to Alfred's desktop. "
        "A result with status 'not_found' includes name suggestions - "
        "relay them and ask which one."
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
                        "Calculator, Chrome, Notepad, or VS Code. "
                        "('name' is accepted as an alias.)"
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
        router: Any = None,
        isolated_desktop: Any = None,
    ) -> None:
        self.launcher = (
            launcher
            or AppLauncher()
        )
        # When Alfred is working on its own desktop, apps must open THERE.
        # The normal launcher starts them in Alfred's own session, which
        # is the user's screen.
        self._router = router
        self._isolated = isolated_desktop

    def execute(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        # Models reliably reach for 'name' here, and a rejected call costs
        # a whole step - accept the obvious synonyms.
        app = (
            arguments.get("app")
            or arguments.get("name")
            or arguments.get("application")
            or arguments.get("app_name")
        )

        target = arguments.get(
            "target",
            "alfred",
        )

        if not isinstance(app, str) or not app.strip():
            return {"status": "error", "error": "'app' must be a non-empty string."}

        if target not in {"alfred", "user", "current"}:
            target = "alfred"

        if (
            self._router is not None
            and self._isolated is not None
            and getattr(self._router, "isolated", False)
        ):
            try:
                out = self._isolated.launch(app)
                return {
                    "status": "success",
                    "app": app,
                    "opened_in": "alfred's private desktop",
                    "session": out.get("session"),
                    "pid": out.get("pid"),
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "status": "error",
                    "error": f"could not open {app!r} on my desktop: {exc}",
                    "instruction": (
                        "Tell the user it failed. Do NOT claim it opened "
                        "privately."
                    ),
                }

        try:
            result = self.launcher.open(app_name=app, target=target)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "error": f"Could not open '{app}': {type(exc).__name__}: {exc}",
            }

        return result.as_dict()

    def close(self) -> None:
        self.launcher.close()