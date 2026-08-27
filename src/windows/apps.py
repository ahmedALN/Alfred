from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Literal

import psutil
import win32gui
import win32process

from src.config import load_settings
from src.windows.desktop_bridge import DesktopBridgeClient
from src.windows.desktops import DesktopManager


DesktopTarget = Literal[
    "alfred",
    "user",
    "current",
]


APP_ALIASES: dict[str, str] = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "brave": "brave.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "powershell": "powershell.exe",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "vscode": "code.exe",
    "vs code": "code.exe",
    "visual studio code": "code.exe",
    "visual studio": "devenv.exe",
    "word": "winword.exe",
    "microsoft word": "winword.exe",
    "excel": "excel.exe",
    "microsoft excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "microsoft powerpoint": "powerpnt.exe",
    "spotify": "Spotify.exe",
    "discord": "Discord.exe",
}


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str


@dataclass(frozen=True)
class AppLaunchResult:
    app: str
    executable: str | None
    target: DesktopTarget
    launched: bool
    moved_to_target: bool
    pid: int | None
    hwnd: int | None
    desktop: int | None
    window_title: str | None
    method: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class AppLauncher:
    """
    Fast Windows application launcher.

    Desktop movement uses one persistent DesktopBridgeClient.
    No .NET process is started for individual actions.
    """

    def __init__(
        self,
        desktop_manager: DesktopManager | None = None,
        desktop_bridge: DesktopBridgeClient | None = None,
    ) -> None:
        self.desktop_manager = (
            desktop_manager
            or DesktopManager()
        )

        self.desktop_bridge = (
            desktop_bridge
            or DesktopBridgeClient()
        )

        settings = load_settings()

        self.alfred_desktop = (
            settings.default_desktop
        )

        self.user_desktop = (
            settings.user_desktop
        )

    # ================================================================
    # Application resolution
    # ================================================================

    def normalize(
        self,
        app_name: str,
    ) -> str:
        name = app_name.strip()

        if not name:
            raise ValueError(
                "Application name cannot be empty."
            )

        return APP_ALIASES.get(
            name.lower(),
            name,
        )

    # ================================================================
    # Process discovery
    # ================================================================

    def _matches_process(
        self,
        process_name: str,
        executable: str,
    ) -> bool:
        left = (
            process_name
            .lower()
            .removesuffix(".exe")
        )

        right = (
            executable
            .lower()
            .removesuffix(".exe")
        )

        return left == right

    def _find_process(
        self,
        executable: str,
    ) -> int | None:
        for process in psutil.process_iter(
            ["pid", "name"]
        ):
            try:
                name = process.info["name"]

                if not name:
                    continue

                if self._matches_process(
                    name,
                    executable,
                ):
                    return int(
                        process.info["pid"]
                    )

            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
            ):
                continue

        return None

    # ================================================================
    # Window discovery
    # ================================================================

    def _list_visible_windows(
        self,
    ) -> list[WindowInfo]:
        windows: list[WindowInfo] = []

        def callback(
            hwnd: int,
            _: int,
        ) -> bool:
            if not win32gui.IsWindowVisible(
                hwnd
            ):
                return True

            if not win32gui.IsWindowEnabled(
                hwnd
            ):
                return True

            if win32gui.GetParent(hwnd) != 0:
                return True

            title = (
                win32gui.GetWindowText(hwnd)
                .strip()
            )

            if not title:
                return True

            try:
                _, pid = (
                    win32process
                    .GetWindowThreadProcessId(
                        hwnd
                    )
                )
            except Exception:
                return True

            windows.append(
                WindowInfo(
                    hwnd=hwnd,
                    pid=pid,
                    title=title,
                )
            )

            return True

        win32gui.EnumWindows(
            callback,
            0,
        )

        return windows

    def _find_window_by_pid(
        self,
        pid: int,
        timeout: float = 8.0,
    ) -> WindowInfo | None:
        deadline = (
            time.monotonic()
            + timeout
        )

        while time.monotonic() < deadline:
            for window in (
                self._list_visible_windows()
            ):
                if window.pid == pid:
                    return window

            time.sleep(0.05)

        return None

    def _find_new_window_by_title(
        self,
        app_name: str,
        known_hwnds: set[int],
        timeout: float = 8.0,
    ) -> WindowInfo | None:
        requested = (
            app_name
            .strip()
            .lower()
        )

        deadline = (
            time.monotonic()
            + timeout
        )

        while time.monotonic() < deadline:
            for window in (
                self._list_visible_windows()
            ):
                if window.hwnd in known_hwnds:
                    continue

                title = (
                    window.title.lower()
                )

                if (
                    title == requested
                    or requested in title
                    or title in requested
                ):
                    return window

            time.sleep(0.05)

        return None

    # ================================================================
    # Desktop targeting
    # ================================================================

    def _resolve_target(
        self,
        target: DesktopTarget,
    ) -> int:
        if target == "alfred":
            return self.alfred_desktop

        if target == "user":
            return self.user_desktop

        return (
            self.desktop_manager
            .current_number()
        )

    # ================================================================
    # Move + verify
    # ================================================================

    def _move_and_verify(
        self,
        hwnd: int,
        target_desktop: int,
    ) -> tuple[bool, int]:
        result = (
            self.desktop_bridge.move_window(
                hwnd,
                target_desktop,
            )
        )

        actual_desktop = int(
            result["actual_desktop"]
        )

        return (
            actual_desktop == target_desktop,
            actual_desktop,
        )

    # ================================================================
    # Public launcher
    # ================================================================

    def open(
        self,
        app_name: str,
        target: DesktopTarget = "alfred",
    ) -> AppLaunchResult:
        if target not in {
            "alfred",
            "user",
            "current",
        }:
            raise ValueError(
                "target must be "
                "'alfred', 'user', or 'current'."
            )

        requested_name = (
            app_name.strip()
        )

        if not requested_name:
            raise ValueError(
                "Application name cannot be empty."
            )

        executable = self.normalize(
            requested_name
        )

        target_desktop = (
            self._resolve_target(
                target
            )
        )

        known_hwnds = {
            window.hwnd
            for window in (
                self._list_visible_windows()
            )
        }

        pid = self._find_process(
            executable
        )

        launched = False
        method = "existing-process"
        window: WindowInfo | None = None

        if pid is not None:
            window = (
                self._find_window_by_pid(
                    pid
                )
            )

            if window is None:
                window = (
                    self._find_new_window_by_title(
                        requested_name,
                        known_hwnds,
                    )
                )

                if window is not None:
                    method = (
                        "application-frame-host"
                    )

        else:
            try:
                process = subprocess.Popen(
                    [executable],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                pid = process.pid
                launched = True
                method = "executable"

                window = (
                    self._find_window_by_pid(
                        pid
                    )
                )

                if window is None:
                    window = (
                        self._find_new_window_by_title(
                            requested_name,
                            known_hwnds,
                        )
                    )

                    if window is not None:
                        method = (
                            "application-frame-host"
                        )

            except (
                FileNotFoundError,
                OSError,
            ) as exc:
                raise FileNotFoundError(
                    f"Could not launch "
                    f"'{requested_name}'. "
                    f"Executable '{executable}' "
                    f"was not found."
                ) from exc

        if window is None:
            return AppLaunchResult(
                app=requested_name,
                executable=executable,
                target=target,
                launched=launched,
                moved_to_target=False,
                pid=pid,
                hwnd=None,
                desktop=None,
                window_title=None,
                method=method,
            )

        hwnd = window.hwnd

        try:
            current_desktop = (
                self.desktop_bridge
                .window_desktop(hwnd)
            )
        except Exception as exc:
            raise RuntimeError(
                "The application window was found, "
                "but Alfred could not determine its "
                "current virtual desktop."
            ) from exc

        moved = False

        if (
            target != "current"
            and current_desktop != target_desktop
        ):
            try:
                verified, actual_desktop = (
                    self._move_and_verify(
                        hwnd,
                        target_desktop,
                    )
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Application '{requested_name}' "
                    f"was found, but Alfred could not "
                    f"move its window to Desktop "
                    f"{target_desktop}."
                ) from exc

            if not verified:
                raise RuntimeError(
                    f"Application '{requested_name}' "
                    f"was not verified on Desktop "
                    f"{target_desktop}. "
                    f"Actual desktop: "
                    f"{actual_desktop}."
                )

            moved = True
        else:
            actual_desktop = current_desktop

        return AppLaunchResult(
            app=requested_name,
            executable=executable,
            target=target,
            launched=launched,
            moved_to_target=moved,
            pid=pid,
            hwnd=hwnd,
            desktop=actual_desktop,
            window_title=(
                window.title or None
            ),
            method=method,
        )

    # ================================================================
    # Lifecycle
    # ================================================================

    def close(self) -> None:
        self.desktop_bridge.close()