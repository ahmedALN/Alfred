from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

# Control types worth showing to a planner / model.
_ACTIONABLE = {
    "Button", "Edit", "Document", "ListItem", "MenuItem", "Hyperlink",
    "TabItem", "CheckBox", "RadioButton", "ComboBox", "TreeItem",
    "SplitButton", "Slider", "Text", "List", "Menu", "Tab", "Group",
}

# Types that are only interesting when they have a name (structural
# containers are noise otherwise).
_NEEDS_NAME = {"Group", "List", "Menu", "Tab", "Text"}

_REGEX_META = re.compile(r"[.^$*+?()\[\]{}|\\]")


@dataclass
class Control:
    ref: int
    control_type: str
    name: str
    automation_id: str
    rect: tuple[int, int, int, int]
    enabled: bool
    is_password: bool = False

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
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
        if self.is_password:
            out["password_field"] = True
        return out


class UiaError(RuntimeError):
    pass


# Pseudo-selector shapes models invent for a window, e.g.
# "[contains='Untitled - Notepad']", "title='Calculator'", '"Spotify"'.
_PSEUDO_SELECTOR = re.compile(
    r"""^\s*\[?\s*(?:contains|title|name|window)?\s*[=:]?\s*"""
    r"""['"]?(?P<inner>.+?)['"]?\s*\]?\s*$""",
    re.I | re.X,
)


def clean_title(title: str) -> str:
    """Pull the real window title out of a model-invented selector."""
    text = (title or "").strip()
    if not text:
        return text
    if text.startswith("[") or "=" in text[:12] or text[0] in "'\"":
        m = _PSEUDO_SELECTOR.match(text)
        if m:
            inner = m.group("inner").strip().strip("'\"").strip()
            if inner:
                return inner
    return text


def title_pattern(title: str) -> str:
    """A plain string becomes a case-insensitive substring match."""
    title = clean_title(title)
    if _REGEX_META.search(title):
        return title
    return f"(?i).*{re.escape(title)}.*"


class UiaSession:
    """
    Thin, forgiving wrapper over pywinauto's UIA backend. Reads a
    window's control tree and drives controls by name / id / ref -
    exact, no screenshots.

    Built for multi-step work *inside* an app: the last tree is cached so
    a sequence of clicks and types doesn't re-walk the tree each time,
    stale refs re-resolve against a fresh read, and there are explicit
    waits for apps that take seconds to become usable.
    """

    def __init__(self) -> None:
        self._desktop = None
        self._by_ref: dict[int, Any] = {}
        self._controls: list[Control] = []
        self._last_window = None
        self._last_title: str = ""
        self._last_spec: tuple[str | None, int | None] = (None, None)

    # ---------------------------------------------------------------- core

    def _dt(self):
        if self._desktop is None:
            from pywinauto import Desktop

            self._desktop = Desktop(backend="uia")
        return self._desktop

    def windows(self, limit: int = 40) -> list[dict[str, Any]]:
        """Every visible top-level window - what's actually on screen."""
        out: list[dict[str, Any]] = []
        try:
            tops = self._dt().windows()
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"could not list windows: {exc}") from exc

        for w in tops:
            try:
                title = (w.window_text() or "").strip()
                if not title:
                    continue
                info = w.element_info
                out.append({
                    "title": title[:90],
                    "pid": getattr(info, "process_id", None),
                    "class": getattr(info, "class_name", "") or "",
                })
                if len(out) >= limit:
                    break
            except Exception:  # noqa: BLE001
                continue
        return out

    def window(self, title_re: str | None = None, pid: int | None = None):
        dt = self._dt()
        try:
            if pid:
                win = dt.window(process=int(pid))
            elif title_re:
                win = dt.window(title_re=title_pattern(title_re))
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

    def focus_window(self, title_re: str | None = None,
                     pid: int | None = None) -> str:
        """Bring a window to the foreground and make it the active target."""
        win = self.window(title_re, pid)
        self.focus(win)
        self._last_window = win
        self._last_spec = (title_re, pid)
        try:
            self._last_title = win.window_text() or ""
        except Exception:  # noqa: BLE001
            self._last_title = ""
        return self._last_title

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

    # ---------------------------------------------------------------- tree

    def tree(
        self,
        title_re: str | None = None,
        pid: int | None = None,
        limit: int = 80,
        max_depth: int = 14,
        contains: str | None = None,
    ) -> tuple[str, list[Control]]:
        """Read a window's actionable controls.

        ``contains`` filters to controls whose name/type/id mentions it -
        essential in a big app where the interesting control would other-
        wise fall outside ``limit``.
        """
        win = self.window(title_re, pid)
        self._last_window = win
        self._last_spec = (title_re, pid)
        self._by_ref.clear()
        self._controls = []

        title = ""
        try:
            title = win.window_text() or ""
        except Exception:  # noqa: BLE001
            pass
        self._last_title = title

        # Read once without stealing focus (fast, non-disruptive). Many
        # Chromium/Electron apps only expose their tree when focused, so
        # if that comes back thin, focus and re-read.
        descendants = self._descendants(win, max_depth)
        if len(descendants) < 4:
            self.focus(win)
            descendants = self._descendants(win, max_depth)

        want = (contains or "").strip().lower()
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
                if not name and ct in _NEEDS_NAME:
                    continue
                aid = getattr(el.element_info, "automation_id", "") or ""
                key = f"{ct}|{name}|{aid}"
                if key in seen:
                    continue
                if want and want not in f"{name} {ct} {aid}".lower():
                    continue
                seen.add(key)
                r = el.rectangle()
                rect = (r.left, r.top, r.right, r.bottom)
                enabled = True
                try:
                    enabled = bool(el.is_enabled())
                except Exception:  # noqa: BLE001
                    pass
                c = Control(
                    ref, ct, name[:80], aid, rect, enabled,
                    is_password=_is_password_element(el),
                )
                self._by_ref[ref] = el
                self._controls.append(c)
                ref += 1
                if ref >= limit:
                    break
            except Exception:  # noqa: BLE001
                continue

        return title, list(self._controls)

    def find(self, query: str, limit: int = 20) -> list[Control]:
        """Search the CURRENT window for controls mentioning ``query``.
        Re-reads if nothing is cached yet."""
        if not self._controls:
            self.tree(*self._last_spec)
        q = query.strip().lower()
        hits = [
            c for c in self._controls
            if q in f"{c.name} {c.control_type} {c.automation_id}".lower()
        ]
        return hits[:limit]

    # ---------------------------------------------------------- resolution

    def _resolve(self, ref: int | None = None, name: str | None = None):
        el = self._resolve_cached(ref, name)
        if el is not None:
            return el

        # Stale cache (the UI moved on) - re-read the last window once.
        if self._last_window is not None or any(self._last_spec):
            try:
                self.tree(*self._last_spec)
            except UiaError:
                pass
            el = self._resolve_cached(ref, name)
            if el is not None:
                return el

        raise UiaError(
            f"no control matches ref={ref} name={name!r} - run 'tree' first, "
            "or the control may not be on screen yet"
        )

    def _resolve_cached(self, ref: int | None, name: str | None):
        if ref is not None and ref in self._by_ref:
            return self._by_ref[ref]

        if not name:
            return None

        want = name.strip().lower()
        best = None
        best_len = 1 << 30
        for i, c in enumerate(self._controls):
            el = self._by_ref.get(i)
            if el is None:
                continue
            hay = c.name.strip().lower()
            if hay == want:
                return el
            aid = (c.automation_id or "").strip().lower()
            if aid and aid == want:
                return el
            if want in hay and len(hay) < best_len:
                best, best_len = el, len(hay)
        return best

    def control_info(self, ref: int | None = None,
                     name: str | None = None) -> Control | None:
        """The Control record for a target, for callers that need to know
        e.g. whether it is a password field."""
        if ref is not None and 0 <= ref < len(self._controls):
            return self._controls[ref]
        if name:
            want = name.strip().lower()
            for c in self._controls:
                if c.name.strip().lower() == want:
                    return c
            for c in self._controls:
                if want in c.name.strip().lower():
                    return c
        return None

    # ------------------------------------------------------------- actions

    def click(self, ref: int | None = None, name: str | None = None,
              double: bool = False, right: bool = False) -> str:
        el = self._resolve(ref, name)
        label = _label(el)
        try:
            if right:
                el.right_click_input()
            elif double:
                el.double_click_input()
            else:
                el.click_input()
            return label
        except Exception:  # noqa: BLE001
            pass
        try:
            el.invoke()  # type: ignore[attr-defined]
            return label
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"could not click {label or ref}: {exc}") from exc

    def invoke(self, ref: int | None = None, name: str | None = None) -> str:
        el = self._resolve(ref, name)
        label = _label(el)
        for method in ("invoke", "click_input", "select", "toggle"):
            try:
                getattr(el, method)()
                return label
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
            try:
                el.click_input()
            except Exception:  # noqa: BLE001
                pass
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
        for getter in ("get_value", "window_text", "legacy_properties"):
            try:
                val = getattr(el, getter)()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(val, dict):
                val = val.get("Value") or val.get("Name") or ""
            if val:
                return str(val)
        return ""

    def select(self, item: str, ref: int | None = None,
               name: str | None = None) -> str:
        """Pick an item in a combo box / list / tab."""
        el = self._resolve(ref, name)
        for method in ("select", "set_text"):
            try:
                getattr(el, method)(item)
                return item
            except Exception:  # noqa: BLE001
                continue
        # Fall back: expand, then click the matching child.
        try:
            el.expand()
            time.sleep(0.25)
        except Exception:  # noqa: BLE001
            pass
        want = item.strip().lower()
        try:
            for child in el.children():
                if want in (child.window_text() or "").strip().lower():
                    child.click_input()
                    return item
        except Exception:  # noqa: BLE001
            pass
        raise UiaError(f"could not select {item!r}")

    def expand(self, ref: int | None = None, name: str | None = None) -> str:
        el = self._resolve(ref, name)
        for method in ("expand", "click_input"):
            try:
                getattr(el, method)()
                return _label(el)
            except Exception:  # noqa: BLE001
                continue
        raise UiaError("could not expand the control")

    def scroll(self, direction: str = "down", amount: int = 3,
               ref: int | None = None, name: str | None = None) -> str:
        direction = (direction or "down").lower()
        if direction not in ("up", "down", "left", "right"):
            raise UiaError("scroll direction must be up/down/left/right")
        target = None
        if ref is not None or name is not None:
            try:
                target = self._resolve(ref, name)
            except UiaError:
                target = None
        if target is None:
            target = self._last_window
        if target is None:
            raise UiaError("nothing to scroll - focus a window first")
        try:
            target.scroll(direction, "line", max(1, int(amount)))
            return f"scrolled {direction}"
        except Exception:  # noqa: BLE001
            pass
        # Wheel fallback via the keyboard.
        key = {"down": "{PGDN}", "up": "{PGUP}",
               "left": "{LEFT}", "right": "{RIGHT}"}[direction]
        self.send_key(key * max(1, min(int(amount), 10)))
        return f"scrolled {direction} (keyboard)"

    def menu_select(self, path: str) -> str:
        """Drive a classic menu bar, e.g. 'File->Save As' or 'File>Exit'."""
        if self._last_window is None:
            raise UiaError("focus a window first")
        norm = path.replace(">", "->").replace("->->", "->")
        try:
            self._last_window.menu_select(norm)
            return norm
        except Exception:  # noqa: BLE001
            pass
        # UIA apps often have no real menu bar - click the parts in turn.
        for part in [p.strip() for p in norm.split("->") if p.strip()]:
            self.click(name=part)
            time.sleep(0.35)
            try:
                self.tree(*self._last_spec)
            except UiaError:
                pass
        return norm

    # --------------------------------------------------------------- waits

    def exists(self, name: str, title_re: str | None = None) -> bool:
        spec = (title_re, None) if title_re else self._last_spec
        try:
            _, controls = self.tree(*spec, contains=name)
        except UiaError:
            return False
        want = name.strip().lower()
        return any(want in c.name.lower() for c in controls)

    def wait_for(
        self, name: str, title_re: str | None = None, timeout: float = 10.0
    ) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if self.exists(name, title_re):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.7)

    def wait_ready(self, title_re: str | None = None, pid: int | None = None,
                   timeout: float = 25.0, min_controls: int = 3) -> bool:
        """Wait until a freshly-launched app is actually usable.

        Apps take seconds to paint; a tree read immediately after launch
        comes back empty and the executor concludes the app is broken.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                _, controls = self.tree(title_re, pid)
                if len(controls) >= min_controls:
                    return True
            except UiaError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(1.0)


# ==================================================================
# helpers
# ==================================================================


def _label(el) -> str:
    try:
        return (el.window_text() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _is_password_element(el) -> bool:
    """True for a masked/secure input. Alfred must never type into one."""
    for probe in ("is_password", "IsPassword"):
        try:
            val = getattr(el, probe)
            val = val() if callable(val) else val
            if isinstance(val, bool):
                return val
        except Exception:  # noqa: BLE001
            continue
    try:
        info = el.element_info
        val = getattr(info, "is_password", None)
        if isinstance(val, bool):
            return val
        elem = getattr(info, "element", None)
        if elem is not None:
            val = getattr(elem, "CurrentIsPassword", None)
            if val is not None:
                return bool(val)
    except Exception:  # noqa: BLE001
        pass
    return False


# Human/model key names -> pywinauto's send_keys syntax.
_MODIFIERS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+",
              "win": "{VK_LWIN}", "cmd": "^", "meta": "{VK_LWIN}"}
_NAMED_KEYS = {
    "enter": "{ENTER}", "return": "{ENTER}", "esc": "{ESC}",
    "escape": "{ESC}", "tab": "{TAB}", "space": "{SPACE}",
    "backspace": "{BACKSPACE}", "bs": "{BACKSPACE}", "del": "{DEL}",
    "delete": "{DEL}", "home": "{HOME}", "end": "{END}",
    "pgup": "{PGUP}", "pageup": "{PGUP}", "pgdn": "{PGDN}",
    "pagedown": "{PGDN}", "up": "{UP}", "down": "{DOWN}",
    "left": "{LEFT}", "right": "{RIGHT}", "insert": "{INSERT}",
    "printscreen": "{PRTSC}", "capslock": "{CAPSLOCK}",
    **{f"f{i}": f"{{F{i}}}" for i in range(1, 25)},
}


def normalise_keys(keys: Any) -> str:
    """Turn what a model emits into pywinauto send_keys syntax.

    Accepts ``["ctrl","a"]``, ``"ctrl+a"``, ``"Ctrl-A"``, ``"enter"`` and
    already-correct strings like ``"^a"`` or ``"{ENTER}"``.
    """
    if isinstance(keys, (list, tuple)):
        parts = [str(k).strip() for k in keys if str(k).strip()]
    elif isinstance(keys, str):
        text = keys.strip()
        if not text:
            return ""
        # Already pywinauto syntax - leave it alone.
        if any(ch in text for ch in "^%+{}") and "+" not in text.strip("+"):
            return text
        if re.fullmatch(r"[^\s+\-]+", text) and text.lower() not in _NAMED_KEYS:
            return text if len(text) > 1 else text
        parts = [p for p in re.split(r"[+\-]", text) if p.strip()]
    else:
        return ""

    if not parts:
        return ""

    prefix = ""
    final: list[str] = []
    for part in parts:
        low = part.strip().lower()
        if low in _MODIFIERS and part is not parts[-1]:
            prefix += _MODIFIERS[low]
        elif low in _NAMED_KEYS:
            final.append(_NAMED_KEYS[low])
        elif low in _MODIFIERS:
            prefix += _MODIFIERS[low]
        else:
            final.append(part)

    body = "".join(final)
    if not body:
        return prefix
    # A modifier applies to the whole group when the key is multi-char.
    if prefix and len(body) > 1 and not body.startswith("{"):
        return f"{prefix}({body})"
    return f"{prefix}{body}"


def _escape(text: str) -> str:
    text = text.replace("{", "{{}").replace("}", "{}}")
    for ch in "+^%~()[]":
        text = text.replace(ch, "{" + ch + "}")
    return text
