from __future__ import annotations

import os
import signal
import threading
from pathlib import Path
from typing import Callable

try:
    import win32api
    import win32con
    import win32gui

    _WIN32 = True
except Exception:  # noqa: BLE001
    _WIN32 = False


_WM_TRAY = win32con.WM_USER + 20 if _WIN32 else 0

_ID_STATUS = 1000
_ID_TOGGLE_BRAIN = 1001
_ID_OPEN_LOGS = 1002
_ID_QUIT = 1003
_ID_GAME_MODE = 1004


class TrayIcon:
    """
    Minimal Windows system-tray presence for Alfred. Pure pywin32, no
    extra dependencies. Runs its message pump on a daemon thread; every
    failure degrades to "no tray icon" rather than taking Alfred down.

    Menu: brain pause/resume, open the logs folder, quit (raises SIGINT
    on the main thread so main()'s normal shutdown runs).
    """

    def __init__(
        self,
        *,
        is_brain_paused: Callable[[], bool],
        set_brain_paused: Callable[[bool], None],
        logs_dir: Path | str,
        tooltip: str = "Alfred",
        is_game_mode: Callable[[], bool] | None = None,
        toggle_game_mode: Callable[[], None] | None = None,
    ) -> None:
        self._is_paused = is_brain_paused
        self._set_paused = set_brain_paused
        self._logs_dir = Path(logs_dir)
        self._tooltip = tooltip
        self._is_game_mode = is_game_mode
        self._toggle_game_mode = toggle_game_mode

        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._nid_added = False

    # ----------------------------------------------------------------

    def start(self) -> bool:
        if not _WIN32:
            print("[Tray] pywin32 not available; skipping tray icon.")
            return False

        self._thread = threading.Thread(
            target=self._run, name="alfred-tray", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._create_window()
            self._add_icon()
            win32gui.PumpMessages()
        except Exception as exc:  # noqa: BLE001
            print(f"[Tray] disabled: {exc}")

    def _create_window(self) -> None:
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "AlfredTrayWindow"
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = win32api.GetModuleHandle(None)
        atom = win32gui.RegisterClass(wc)

        self._hwnd = win32gui.CreateWindow(
            atom, "Alfred", 0, 0, 0, 0, 0, 0, 0,
            wc.hInstance, None,
        )
        win32gui.UpdateWindow(self._hwnd)

    def _icon_handle(self):
        try:
            return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        except Exception:  # noqa: BLE001
            return 0

    def _add_icon(self) -> None:
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (
            self._hwnd, 0, flags, _WM_TRAY,
            self._icon_handle(), self._tooltip,
        )
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
        self._nid_added = True

    def _remove_icon(self) -> None:
        if self._nid_added and self._hwnd:
            try:
                win32gui.Shell_NotifyIcon(
                    win32gui.NIM_DELETE, (self._hwnd, 0)
                )
            except Exception:  # noqa: BLE001
                pass
            self._nid_added = False

    # ----------------------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == _WM_TRAY:
            if lparam in (win32con.WM_RBUTTONUP, win32con.WM_LBUTTONUP):
                self._show_menu()
            return 0

        if msg == win32con.WM_COMMAND:
            self._on_command(win32api.LOWORD(wparam))
            return 0

        if msg == win32con.WM_CLOSE:
            self._remove_icon()
            win32gui.DestroyWindow(hwnd)
            return 0

        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        paused = False
        try:
            paused = bool(self._is_paused())
        except Exception:  # noqa: BLE001
            pass

        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(
            menu, win32con.MF_STRING | win32con.MF_GRAYED,
            _ID_STATUS, "Alfred is running",
        )
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(
            menu, win32con.MF_STRING, _ID_TOGGLE_BRAIN,
            "Resume awareness" if paused else "Pause awareness",
        )

        if self._toggle_game_mode is not None:
            in_game = False
            try:
                in_game = bool(self._is_game_mode()) if self._is_game_mode else False
            except Exception:  # noqa: BLE001
                pass
            flags = win32con.MF_STRING | (win32con.MF_CHECKED if in_game else 0)
            win32gui.AppendMenu(menu, flags, _ID_GAME_MODE, "Game mode")

        win32gui.AppendMenu(
            menu, win32con.MF_STRING, _ID_OPEN_LOGS, "Open logs folder"
        )
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, _ID_QUIT, "Quit Alfred")

        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self._hwnd)
        win32gui.TrackPopupMenu(
            menu, win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
            pos[0], pos[1], 0, self._hwnd, None,
        )
        win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)

    def _on_command(self, command_id: int) -> None:
        if command_id == _ID_TOGGLE_BRAIN:
            try:
                self._set_paused(not self._is_paused())
            except Exception as exc:  # noqa: BLE001
                print(f"[Tray] could not toggle awareness: {exc}")

        elif command_id == _ID_GAME_MODE:
            try:
                if self._toggle_game_mode is not None:
                    self._toggle_game_mode()
            except Exception as exc:  # noqa: BLE001
                print(f"[Tray] could not toggle game mode: {exc}")

        elif command_id == _ID_OPEN_LOGS:
            try:
                self._logs_dir.mkdir(parents=True, exist_ok=True)
                os.startfile(str(self._logs_dir))  # noqa: S606
            except Exception as exc:  # noqa: BLE001
                print(f"[Tray] could not open logs: {exc}")

        elif command_id == _ID_QUIT:
            self._remove_icon()
            try:
                os.kill(os.getpid(), signal.SIGINT)
            except Exception:  # noqa: BLE001
                os._exit(0)
