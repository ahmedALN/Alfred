from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import psutil
import win32con
import win32gui
import win32process

from src.config import load_settings
from src.windows.desktop_bridge import DesktopBridgeClient
from src.windows.desktops import DesktopManager

DesktopTarget = Literal["alfred", "user", "current"]


# Fast path for built-in Windows tools (no Start-menu lookup needed).
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
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "powershell": "powershell.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
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
    "steam": "steam.exe",
    "settings": "ms-settings:",
    "control panel": "control.exe",
    "snipping tool": "SnippingTool.exe",
    "registry editor": "regedit.exe",
    "regedit": "regedit.exe",
}

# URI-scheme launches (Settings pages, etc.) handled via os.startfile.
_URI_PREFIXES = ("ms-settings:", "ms-", "http://", "https://", "mailto:")


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str


@dataclass(frozen=True)
class LaunchSpec:
    kind: str          # "exe" | "appsfolder" | "shortcut" | "uri"
    value: str
    display: str


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
    status: str = "success"
    note: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class AppLauncher:
    """
    Resolve an app by natural name and launch it, fast.

    Resolution order: built-in alias -> already-open window -> Windows
    Start apps (Get-StartApps, covers Store apps + everything in the
    Start menu) -> Start-menu shortcuts -> PATH -> give up gracefully.
    Never raises for "not found"; returns status="not_found" so the
    model can tell the user or try another name.
    """

    WINDOW_WAIT = 4.0

    def __init__(
        self,
        desktop_manager: DesktopManager | None = None,
        desktop_bridge: DesktopBridgeClient | None = None,
    ) -> None:
        self.desktop_manager = desktop_manager or DesktopManager()

        # The bridge is only needed to move windows between desktops -
        # a nice-to-have, not required to launch things.
        self._bridge_arg = desktop_bridge
        self._bridge: DesktopBridgeClient | None = desktop_bridge
        self._bridge_tried = desktop_bridge is not None

        settings = load_settings()
        self.alfred_desktop = settings.default_desktop
        self.user_desktop = settings.user_desktop

        self._start_apps_cache: list[dict[str, str]] | None = None

        # Warm the Start-apps list in the background so the first
        # "open X" doesn't pay the ~2s Get-StartApps cost.
        import threading

        threading.Thread(
            target=self._get_start_apps, name="alfred-startapps-warm", daemon=True
        ).start()

    # ================================================================
    # Bridge (lazy, optional)
    # ================================================================

    @property
    def desktop_bridge(self) -> DesktopBridgeClient | None:
        if not self._bridge_tried:
            self._bridge_tried = True
            try:
                self._bridge = DesktopBridgeClient()
            except Exception as exc:  # noqa: BLE001
                print(f"[Apps] desktop bridge unavailable: {exc}")
                self._bridge = None
        return self._bridge

    # ================================================================
    # Name resolution
    # ================================================================

    def normalize(self, app_name: str) -> str:
        name = app_name.strip()
        if not name:
            raise ValueError("Application name cannot be empty.")
        return APP_ALIASES.get(name.lower(), name)

    def _get_start_apps(self) -> list[dict[str, str]]:
        if self._start_apps_cache is not None:
            return self._start_apps_cache

        apps: list[dict[str, str]] = []
        try:
            proc = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress",
                ],
                capture_output=True, text=True, timeout=12,
            )
            data = json.loads(proc.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            apps = [
                {"name": str(d.get("Name", "")), "appid": str(d.get("AppID", ""))}
                for d in data
                if d.get("Name") and d.get("AppID")
            ]
        except Exception as exc:  # noqa: BLE001
            print(f"[Apps] Get-StartApps failed: {exc}")

        # Assign once, atomically - safe against the warm-up thread.
        self._start_apps_cache = apps
        return apps

    def _match_start_app(self, query: str) -> dict[str, str] | None:
        q = query.strip().lower()
        apps = self._get_start_apps()
        if not apps:
            return None

        exact = [a for a in apps if a["name"].lower() == q]
        if exact:
            return exact[0]

        starts = [a for a in apps if a["name"].lower().startswith(q)]
        if starts:
            return min(starts, key=lambda a: len(a["name"]))

        contains = [a for a in apps if q in a["name"].lower()]
        if contains:
            return min(contains, key=lambda a: len(a["name"]))

        # token overlap (e.g. "vs code" -> "Visual Studio Code")
        q_tokens = set(q.split())
        scored = [
            (len(q_tokens & set(a["name"].lower().split())), a)
            for a in apps
        ]
        best_score, best = max(scored, key=lambda p: p[0])
        return best if best_score else None

    def _find_shortcut(self, query: str) -> Path | None:
        q = query.strip().lower()
        roots = [
            Path(os.environ.get("ProgramData", r"C:\ProgramData"))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("APPDATA", "")) / "Microsoft"
            / "Windows" / "Start Menu" / "Programs",
        ]
        best: tuple[int, Path] | None = None
        for root in roots:
            if not root.exists():
                continue
            for lnk in root.rglob("*.lnk"):
                stem = lnk.stem.lower()
                if stem == q:
                    return lnk
                if len(q) >= 3 and q in stem:
                    score = -abs(len(stem) - len(q))
                    if best is None or score > best[0]:
                        best = (score, lnk)
        return best[1] if best else None

    def resolve(self, app_name: str) -> LaunchSpec | None:
        raw = app_name.strip()
        lowered = raw.lower()

        # URI schemes / URLs.
        if any(lowered.startswith(p) for p in _URI_PREFIXES):
            return LaunchSpec("uri", raw, raw)

        aliased = APP_ALIASES.get(lowered, raw)

        if aliased.endswith(":"):  # e.g. ms-settings:
            return LaunchSpec("uri", aliased, raw)

        # A bare exe name / path we can find right now.
        if aliased.lower().endswith(".exe"):
            found = shutil.which(aliased) or _app_paths_lookup(aliased)
            if found:
                return LaunchSpec("exe", found, raw)
            # Known system exes resolve at launch time even without a path.
            if aliased.lower() in _SYSTEM_EXES:
                return LaunchSpec("exe", aliased, raw)

        # Everything else: ask Windows what it can launch.
        match = self._match_start_app(raw)
        if match:
            return LaunchSpec("appsfolder", match["appid"], match["name"])

        shortcut = self._find_shortcut(raw)
        if shortcut:
            return LaunchSpec("shortcut", str(shortcut), shortcut.stem)

        which = shutil.which(raw) or shutil.which(raw + ".exe")
        if which:
            return LaunchSpec("exe", which, raw)

        # An existing file or folder path -> open it in Explorer / its app.
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if os.path.exists(expanded):
            return LaunchSpec("uri", expanded, raw)

        # Looks like a website ("youtube", "github.com", "open reddit").
        site = _as_website(raw)
        if site:
            return LaunchSpec("uri", site, raw)

        # Fall back to the aliased exe name and let the OS try.
        if aliased.lower().endswith(".exe"):
            return LaunchSpec("exe", aliased, raw)

        return None

    # ================================================================
    # Launch
    # ================================================================

    def _launch(self, spec: LaunchSpec) -> int | None:
        if spec.kind in ("uri", "shortcut"):
            os.startfile(spec.value)  # noqa: S606
            return None

        if spec.kind == "appsfolder":
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{spec.value}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return None

        # exe
        try:
            proc = subprocess.Popen(
                [spec.value],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return proc.pid
        except (FileNotFoundError, OSError):
            os.startfile(spec.value)  # noqa: S606 - last resort
            return None

    # ================================================================
    # Window discovery
    # ================================================================

    def _list_visible_windows(self) -> list[WindowInfo]:
        windows: list[WindowInfo] = []

        def callback(hwnd: int, _: int) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetParent(hwnd) != 0:
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:  # noqa: BLE001
                return True
            windows.append(WindowInfo(hwnd=hwnd, pid=pid, title=title))
            return True

        win32gui.EnumWindows(callback, 0)
        return windows

    def _window_for_pid_tree(self, pid: int) -> WindowInfo | None:
        try:
            pids = {pid} | {
                c.pid for c in psutil.Process(pid).children(recursive=True)
            }
        except Exception:  # noqa: BLE001
            pids = {pid}
        for w in self._list_visible_windows():
            if w.pid in pids:
                return w
        return None

    def _wait_for_window(
        self,
        pid: int | None,
        display: str,
        known: set[int],
        timeout: float,
    ) -> WindowInfo | None:
        want = display.strip().lower()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pid is not None:
                w = self._window_for_pid_tree(pid)
                if w:
                    return w
            for w in self._list_visible_windows():
                if w.hwnd in known:
                    continue
                t = w.title.lower()
                if want and (want in t or t in want or _tokens_overlap(want, t)):
                    return w
            time.sleep(0.1)
        return None

    def _existing_window(self, display: str) -> WindowInfo | None:
        want = display.strip().lower()
        if not want:
            return None
        for w in self._list_visible_windows():
            t = w.title.lower()
            if want in t or _tokens_overlap(want, t):
                return w
        return None

    # ================================================================
    # Desktop targeting
    # ================================================================

    def _resolve_target(self, target: DesktopTarget) -> int:
        if target == "alfred":
            return self.alfred_desktop
        if target == "user":
            return self.user_desktop
        return self.desktop_manager.current_number()

    def _try_move(self, hwnd: int, target_desktop: int) -> tuple[bool, int | None]:
        bridge = self.desktop_bridge
        if bridge is None:
            return False, None
        try:
            current = bridge.window_desktop(hwnd)
            if current == target_desktop:
                return False, current
            result = bridge.move_window(hwnd, target_desktop)
            actual = int(result.get("actual_desktop", current))
            return actual == target_desktop, actual
        except Exception as exc:  # noqa: BLE001
            # Windows won't let us move a window owned by another process
            # onto another virtual desktop (E_ACCESSDENIED). Common and
            # harmless - the app is still open, just on this desktop.
            if not getattr(self, "_move_warned", False):
                print(f"[Apps] windows stay on this desktop ({exc})")
                self._move_warned = True
            return False, None

    # ================================================================
    # Public
    # ================================================================

    def open(
        self,
        app_name: str,
        target: DesktopTarget = "alfred",
    ) -> AppLaunchResult:
        if target not in ("alfred", "user", "current"):
            raise ValueError("target must be 'alfred', 'user', or 'current'.")

        requested = app_name.strip()
        if not requested:
            raise ValueError("Application name cannot be empty.")

        spec = self.resolve(requested)

        if spec is None:
            q = requested.lower()
            hints = [
                a["name"] for a in self._get_start_apps()
                if any(tok in a["name"].lower() for tok in q.split() if len(tok) > 2)
            ][:5]
            return AppLaunchResult(
                app=requested, executable=None, target=target,
                launched=False, moved_to_target=False, pid=None, hwnd=None,
                desktop=None, window_title=None, method="not_found",
                status="not_found",
                note=(
                    f"Couldn't find an app matching '{requested}'."
                    + (f" Did you mean: {', '.join(hints)}?" if hints else
                       " Say the exact name from the Start menu.")
                ),
            )

        target_desktop = self._resolve_target(target)
        known = {w.hwnd for w in self._list_visible_windows()}

        # Already open? Just retarget it.
        existing = self._existing_window(spec.display)
        if existing is not None:
            moved, actual = (
                (False, None) if target == "current"
                else self._try_move(existing.hwnd, target_desktop)
            )
            return AppLaunchResult(
                app=requested, executable=spec.value, target=target,
                launched=False, moved_to_target=moved, pid=existing.pid,
                hwnd=existing.hwnd, desktop=actual, window_title=existing.title,
                method="existing-window",
            )

        try:
            pid = self._launch(spec)
        except Exception as exc:  # noqa: BLE001
            return AppLaunchResult(
                app=requested, executable=spec.value, target=target,
                launched=False, moved_to_target=False, pid=None, hwnd=None,
                desktop=None, window_title=None, method="launch_failed",
                status="error", note=f"Launch failed: {exc}",
            )

        window = self._wait_for_window(
            pid, spec.display, known, self.WINDOW_WAIT
        )

        if window is None:
            # It very likely started; we just couldn't attach a window
            # (splash screen, slow start, background app). Not an error.
            return AppLaunchResult(
                app=requested, executable=spec.value, target=target,
                launched=True, moved_to_target=False, pid=pid, hwnd=None,
                desktop=None, window_title=None, method=spec.kind,
                note="Launched; window not confirmed yet.",
            )

        moved, actual = (
            (False, None) if target == "current"
            else self._try_move(window.hwnd, target_desktop)
        )

        return AppLaunchResult(
            app=requested, executable=spec.value, target=target,
            launched=True, moved_to_target=moved, pid=window.pid or pid,
            hwnd=window.hwnd, desktop=actual, window_title=window.title,
            method=spec.kind,
        )

    def close(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.close()
            except Exception:  # noqa: BLE001
                pass


# ====================================================================
# helpers
# ====================================================================

_SYSTEM_EXES = {
    "notepad.exe", "calc.exe", "mspaint.exe", "explorer.exe", "taskmgr.exe",
    "powershell.exe", "cmd.exe", "regedit.exe", "control.exe",
    "snippingtool.exe", "write.exe", "charmap.exe", "wt.exe",
}


def _app_paths_lookup(exe: str) -> str | None:
    """Resolve an exe via the App Paths registry key (how Start/Run finds apps)."""

    try:
        import winreg
    except ImportError:
        return None

    sub = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe
    )
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _ = winreg.QueryValueEx(key, None)
                if value and Path(value).exists():
                    return value
        except OSError:
            continue
    return None


def _tokens_overlap(a: str, b: str) -> bool:
    ta = {t for t in a.replace("-", " ").split() if len(t) > 2}
    tb = {t for t in b.replace("-", " ").split() if len(t) > 2}
    return bool(ta) and ta.issubset(tb)


# Common single-word site names Alfred should just open in the browser.
_KNOWN_SITES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "netflix": "https://www.netflix.com",
    "twitch": "https://www.twitch.tv",
    "maps": "https://maps.google.com",
    "google": "https://www.google.com",
    "amazon": "https://www.amazon.com",
    "outlook": "https://outlook.office.com",
}


def _as_website(name: str) -> str | None:
    n = name.strip().lower()
    if n in _KNOWN_SITES:
        return _KNOWN_SITES[n]
    # bare domain like "example.com" or "docs.python.org"
    if " " not in n and "." in n and not n.endswith("."):
        return "https://" + name.strip()
    return None
