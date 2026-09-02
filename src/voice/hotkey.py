from __future__ import annotations

import threading
from collections.abc import Callable

_MODIFIERS = {
    "alt": 0x0001,      # MOD_ALT
    "ctrl": 0x0002,     # MOD_CONTROL
    "control": 0x0002,
    "shift": 0x0004,    # MOD_SHIFT
    "win": 0x0008,      # MOD_WIN
    "super": 0x0008,
}
_MOD_NOREPEAT = 0x4000

_NAMED_VK = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """'ctrl+alt+a' -> (modifier_flags, virtual_key_code)."""

    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        return None

    mods = 0
    key: int | None = None

    for part in parts:
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        elif part in _NAMED_VK:
            key = _NAMED_VK[part]
        elif len(part) == 1:
            key = ord(part.upper())
        else:
            return None

    if key is None:
        return None

    return mods | _MOD_NOREPEAT, key


class HotkeyListener:
    """
    Global push-to-talk hotkey (default Ctrl+Alt+A). Runs a tiny message
    loop on a daemon thread; fires ``on_press`` on each activation.
    Degrades to a no-op if the hotkey can't be registered.
    """

    HOTKEY_ID = 0xA1F

    def __init__(self, on_press: Callable[[], None], spec: str = "ctrl+alt+a") -> None:
        self._on_press = on_press
        self._spec = spec
        self._thread: threading.Thread | None = None
        self._tid: int | None = None

    def start(self) -> bool:
        parsed = parse_hotkey(self._spec)
        if parsed is None:
            print(f"[Hotkey] could not parse '{self._spec}'.")
            return False

        try:
            import win32con  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            print(f"[Hotkey] disabled: {exc}")
            return False

        self._thread = threading.Thread(
            target=self._run, args=parsed, name="alfred-hotkey", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._tid is not None:
            try:
                import win32api
                import win32con

                win32api.PostThreadMessage(
                    self._tid, win32con.WM_QUIT, 0, 0
                )
            except Exception:  # noqa: BLE001
                pass

    def _run(self, mods: int, vk: int) -> None:
        import win32api
        import win32con
        import win32gui

        self._tid = win32api.GetCurrentThreadId()

        # win32gui.RegisterHotKey returns None on success and raises on
        # failure (e.g. the combo is already claimed by another app).
        try:
            win32gui.RegisterHotKey(None, self.HOTKEY_ID, mods, vk)
        except Exception:  # noqa: BLE001
            print(
                f"[Hotkey] '{self._spec}' is unavailable (already taken). "
                "Set ALFRED_HOTKEY to a free combo (the wake word still works)."
            )
            return

        print(f"[Hotkey] '{self._spec}' active.")

        try:
            while True:
                ret, msg = win32gui.GetMessage(None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                if msg[1] == win32con.WM_HOTKEY:
                    try:
                        self._on_press()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[Hotkey] on_press failed: {exc}")
        finally:
            try:
                win32gui.UnregisterHotKey(None, self.HOTKEY_ID)
            except Exception:  # noqa: BLE001
                pass
