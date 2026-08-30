from __future__ import annotations

import os
from typing import Any

from src.tools.base import AlfredTool
from src.windows.apps import AppLauncher


# Browsers whose page content Alfred can actually read. Opening a web
# page in one Alfred cannot read makes every following step impossible:
# Opera GX, the default here, reports ZERO controls for a YouTube page
# that Chrome exposes 173 of. A web page is opened to be worked with, so
# it goes to a browser that can be worked with.
_READABLE_BROWSERS = ("chrome", "msedge", "firefox")

_WEB = ("http://", "https://", "www.")


def _is_web(target: str) -> bool:
    lowered = target.strip().lower()
    return any(lowered.startswith(p) for p in _WEB)


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
                # Resolve the name the same way as on the user's desktop
                # first. The agent in that session starts a path, not a
                # name - so "notepad" worked only because it happens to
                # be on PATH, while "steam" or "multimc" did not.
                spec = self.launcher.resolve(app)

                if spec is None:
                    return {
                        "status": "not_found",
                        "error": f"could not find an app called {app!r}",
                        "instruction": (
                            "Tell the user you could not find it and ask "
                            "what it is called."
                        ),
                    }

                path, args = spec.value, None

                if spec.kind == "appsfolder":
                    # Get-StartApps hands back a real path for some
                    # classic desktop apps rather than an AppID; putting
                    # that behind shell:AppsFolder\ opens nothing.
                    if not os.path.exists(spec.value):
                        path = "explorer.exe"
                        args = "shell:AppsFolder" + chr(92) + spec.value

                out = self._isolated.launch(path, args)
                return {
                    "status": "success",
                    "app": spec.display or app,
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

        # A web page goes to a browser whose content is readable.
        if _is_web(app):
            readable = self._readable_browser()
            if readable is not None:
                try:
                    # In its OWN window. A browser window only exposes
                    # its ACTIVE tab, so opening into an existing window
                    # can leave the page in a background tab - present,
                    # but invisible to every tool that follows.
                    flag = "-new-window" if readable == "firefox"                         else "--new-window"
                    out = self.launcher.open(
                        app_name=readable, target=target,
                        arguments=f"{flag} {app}",
                    )
                    payload = out.as_dict()
                    payload["opened_url"] = app
                    payload["browser"] = readable
                    payload["instruction"] = (
                        "Opened in a browser whose page content can be "
                        "read. The window is named after the PAGE, not "
                        "the URL, and it is still loading - the title "
                        "here is the one it had a moment ago. Next: "
                        "ui_control wait_ready with min_controls=40 on "
                        "the site's name, then tree with limit=300."
                    )
                    return payload
                except Exception:  # noqa: BLE001
                    pass  # fall back to the default browser

        try:
            result = self.launcher.open(app_name=app, target=target)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "error": f"Could not open '{app}': {type(exc).__name__}: {exc}",
            }

        return result.as_dict()

    def _readable_browser(self) -> str | None:
        """The first installed browser Alfred can read pages in."""
        for name in _READABLE_BROWSERS:
            try:
                if self.launcher.resolve(name) is not None:
                    return name
            except Exception:  # noqa: BLE001
                continue
        return None

    def close(self) -> None:
        self.launcher.close()