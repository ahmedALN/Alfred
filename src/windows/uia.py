from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# Control types worth showing to a planner / model.
_ACTIONABLE = {
    "Button", "Edit", "Document", "ListItem", "MenuItem", "Hyperlink",
    "TabItem", "CheckBox", "RadioButton", "ComboBox", "TreeItem",
    "SplitButton", "Slider", "Text",
}


@dataclass
class Control:
    ref: int
    control_type: str
    name: str
    automation_id: str
    rect: tuple[int, int, int, int]
    enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "type": self.control_type,
            "name": self.name,
            "id": self.automation_id or None,
            "center": [
                (self.rect[0] + self.rect[2]) // 2,
                (self.rect[1] + self.rect[3]) // 2,
            ],
            "enabled": self.enabled,
        }


class UiaError(RuntimeError):
    pass


class UiaSession:
    """
    Thin, forgiving wrapper over pywinauto's UIA backend. Reads a
    window's control tree and drives controls by name / id / ref -
    exact, no screenshots. Chromium/Electron apps (Spotify, Discord,
    browsers) only expose their tree when focused, so every read
    focuses the target window first.
    """

    def __init__(self) -> None:
        self._desktop = None
        self._by_ref: dict[int, Any] = {}
        self._last_window = None

    # ----------------------------------------------------------------

    def _dt(self):
        if self._desktop is None:
            from pywinauto import Desktop

            self._desktop = Desktop(backend="uia")
        return self._desktop

    def window(self, title_re: str | None = None, pid: int | None = None):
        dt = self._dt()
        try:
            if pid:
                win = dt.window(process=int(pid))
            elif title_re:
                # Treat a plain string as a case-insensitive substring.
                import re as _re

                if not _re.search(r"[.^$*+?()\[\]{}|\\]", title_re):
                    pat = f"(?i).*{_re.escape(title_re)}.*"
                else:
                    pat = title_re
                win = dt.window(title_re=pat)
            else:
                from pywinauto import win32functions

                hwnd = win32functions.GetForegroundWindow()
                win = dt.window(handle=hwnd)
            win.wait("exists", timeout=4)
            return win
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"window not found: {exc}") from exc

    def focus(self, win) -> None:
        for _ in range(2):
            try:
                win.set_focus()
                time.sleep(0.35)
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.3)

    @staticmethod
    def _descendants(win, max_depth: int) -> list:
        """Depth-bounded so a browser's enormous a11y tree can't hang the
        call. pywinauto walks the whole subtree otherwise."""
        for kwargs in ({"depth": max_depth}, {}):
            try:
                return list(win.descendants(**kwargs))
            except TypeError:
                continue
            except Exception as exc:  # noqa: BLE001
                raise UiaError(f"could not read the control tree: {exc}") from exc
        return []

    # ----------------------------------------------------------------

    def tree(
        self,
        title_re: str | None = None,
        pid: int | None = None,
        limit: int = 80,
        max_depth: int = 14,
    ) -> tuple[str, list[Control]]:
        win = self.window(title_re, pid)
        self._last_window = win
        self._by_ref.clear()

        title = ""
        try:
            title = win.window_text()
        except Exception:  # noqa: BLE001
            pass

        # Read once without stealing focus (fast, non-disruptive). Many
        # Chromium/Electron apps only expose their tree when focused, so
        # if that comes back thin, focus and re-read.
        descendants = self._descendants(win, max_depth)
        if len(descendants) < 4:
            self.focus(win)
            descendants = self._descendants(win, max_depth)

        controls: list[Control] = []
        ref = 0
        seen: set[str] = set()
        for el in descendants:
            try:
                ct = el.element_info.control_type
                if ct not in _ACTIONABLE:
                    continue
                name = (el.window_text() or "").strip()
                if not name and ct not in ("Edit", "Document"):
                    continue
                aid = getattr(el.element_info, "automation_id", "") or ""
                key = f"{ct}|{name}|{aid}"
                if key in seen:
                    continue
                seen.add(key)
                r = el.rectangle()
                rect = (r.left, r.top, r.right, r.bottom)
                enabled = True
                try:
                    enabled = bool(el.is_enabled())
                except Exception:  # noqa: BLE001
                    pass
                c = Control(ref, ct, name[:80], aid, rect, enabled)
                self._by_ref[ref] = el
                controls.append(c)
                ref += 1
                if ref >= limit:
                    break
            except Exception:  # noqa: BLE001
                continue

        return title, controls

    # ----------------------------------------------------------------

    def _resolve(self, ref: int | None, name: str | None):
        if ref is not None and ref in self._by_ref:
            return self._by_ref[ref]

        if name and self._last_window is not None:
            want = name.strip().lower()
            best = None
            best_len = 1e9
            for el in self._by_ref.values():
                try:
                    t = (el.window_text() or "").strip().lower()
                except Exception:  # noqa: BLE001
                    continue
                if t == want:
                    return el
                if want in t and len(t) < best_len:
                    best, best_len = el, len(t)
            if best is not None:
                return best

        raise UiaError(
            f"no control matches ref={ref} name={name!r} - run 'tree' first"
        )

    def click(self, ref: int | None = None, name: str | None = None) -> str:
        el = self._resolve(ref, name)
        try:
            el.click_input()
        except Exception:
            el.invoke()  # type: ignore[attr-defined]
        return el.window_text() or ""

    def invoke(self, ref: int | None = None, name: str | None = None) -> str:
        el = self._resolve(ref, name)
        for method in ("invoke", "click_input", "select"):
            try:
                getattr(el, method)()
                return el.window_text() or ""
            except Exception:  # noqa: BLE001
                continue
        raise UiaError("could not invoke the control")

    def type_text(
        self, text: str, ref: int | None = None, name: str | None = None
    ) -> None:
        if ref is not None or name is not None:
            el = self._resolve(ref, name)
            try:
                el.set_focus()
            except Exception:  # noqa: BLE001
                pass
            try:
                el.set_edit_text(text)
                return
            except Exception:  # noqa: BLE001
                pass
            el.click_input()
        from pywinauto.keyboard import send_keys

        send_keys(
            _escape(text), pause=0.01,
            with_spaces=True, with_tabs=True, with_newlines=True,
        )

    def send_key(self, keys: str) -> None:
        from pywinauto.keyboard import send_keys

        send_keys(keys, with_spaces=True)

    def get_text(self, ref: int | None = None, name: str | None = None) -> str:
        el = self._resolve(ref, name)
        try:
            return el.window_text() or el.get_value()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return el.window_text() or ""

    def exists(self, name: str, title_re: str | None = None) -> bool:
        try:
            _, controls = self.tree(title_re)
        except UiaError:
            return False
        want = name.strip().lower()
        return any(want in c.name.lower() for c in controls)

    def wait_for(
        self, name: str, title_re: str | None = None, timeout: float = 10.0
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exists(name, title_re):
                return True
            time.sleep(0.8)
        return False


def _escape(text: str) -> str:
    text = text.replace("{", "{{}").replace("}", "{}}")
    for ch in "+^%~()[]":
        text = text.replace(ch, "{" + ch + "}")
    return text
