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

_MENU_SEPARATOR = re.compile(r"\s*(?:->|>)\s*")

# How deep to walk a control tree unless told otherwise.
#
# This was 14, to stop a browser's enormous tree hanging the call. The
# node budget is what actually bounds that; the depth limit just
# silently truncated. On a YouTube channel page depth 14 found 18
# controls and NOT ONE video link - depth 25 found all 30 videos in the
# same 0.2s. Alfred could not click what it could not see.
_DEFAULT_MAX_DEPTH = 30

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


def _title_score(title: str, wanted: str) -> int:
    """How well a window title answers what was asked for."""
    haystack = title.strip().lower()
    want = wanted.strip().lower()

    if not want or not haystack:
        return 0

    if haystack == want:
        score = 1000
    elif haystack.startswith(want):
        score = 500
    elif want in haystack:
        # A whole word beats a fragment buried in a path.
        score = 300 if re.search(rf"{re.escape(want)}", haystack) else 100
    else:
        return 0

    # Among equals the tighter title is the better answer.
    return score - min(len(title) // 4, 40)


def _is_deliberate_pattern(title: str) -> bool:
    """Did somebody mean a regex, or is this just what the window is called?

    Only a string that both contains metacharacters AND compiles is
    given the benefit of the doubt. Everything else goes through the
    scoring path, which matches literally and picks the best window
    rather than the first - the path titles with an asterisk in them
    used to be shut out of.
    """
    return bool(title) and bool(_REGEX_META.search(title)) and _compiles(title)


def title_pattern(title: str) -> str:
    """A plain string becomes a case-insensitive substring match.

    Containing a regex metacharacter used to be taken as proof that the
    caller meant a regex. Real window titles are full of them: Notepad
    marks unsaved work with a leading asterisk, Explorer counts
    duplicates with (2), a terminal's title is a path full of
    backslashes. "*Hello - Notepad" is not a pattern, it is an invalid
    one - "nothing to repeat at position 0" - so a document with
    unsaved changes could not be addressed at all, which is precisely
    when you most want to close it.

    So a title is always taken literally here. A caller who genuinely
    means a pattern is served by window(), which tries that only after
    nothing turned out to be called this.
    """
    title = clean_title(title)
    return f"(?i).*{re.escape(title)}.*"


def _exists(win) -> bool:
    try:
        return bool(win.exists(timeout=0.4))
    except Exception:  # noqa: BLE001
        return False


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


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
        self._last_scanned: int = 0

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
                entry = {
                    "title": title[:90],
                    "pid": getattr(info, "process_id", None),
                    "class": getattr(info, "class_name", "") or "",
                }
                try:
                    r = w.rectangle()
                    entry["rect"] = [r.left, r.top, r.right, r.bottom]
                except Exception:  # noqa: BLE001
                    pass
                try:
                    entry["hwnd"] = int(w.handle)
                except Exception:  # noqa: BLE001
                    pass
                out.append(entry)
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
                # Literally first, always. Every window is looked at and
                # scored as plain text, because taking the first or
                # shortest match picked the wrong one as soon as titles
                # moved: searching in Explorer renamed it "notepad -
                # Search Results in Windows - File Explorer", and a
                # terminal called "C:\WINDOWS\system32\cmd.exe" became
                # the better match for "Windows".
                #
                # This used to be skipped for anything containing a
                # regex metacharacter, on the theory that such a string
                # was meant as a pattern. Window titles are full of
                # them - Notepad's unsaved asterisk, Explorer's (2), a
                # path's backslashes - and read as patterns they match
                # either nothing or the wrong thing. "Document (2)" as a
                # regex does not even match the window it names.
                wanted = clean_title(title_re)
                win = self._best_window(dt, wanted)
                if win is None:
                    win = dt.window(title_re=title_pattern(title_re))
                    # A genuine pattern gets its turn only once nothing
                    # was called that.
                    if _is_deliberate_pattern(wanted) and not _exists(win):
                        win = dt.window(title_re=wanted)
            else:
                from pywinauto import win32functions

                hwnd = win32functions.GetForegroundWindow()
                win = dt.window(handle=hwnd)
            # dt.window(...) hands back an unresolved specification that
            # has to be waited on; _best_window hands back an element
            # that is already there. Only the first kind has .wait, and
            # calling it on the second broke every lookup on the user's
            # own desktop.
            waiter = getattr(win, "wait", None)
            if callable(waiter):
                waiter("exists", timeout=4)
            return win
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"window not found: {exc}") from exc

    def _same_as_last(self, candidate) -> bool:
        """Is this the window the previous action worked in?"""
        if self._last_window is None:
            return False
        try:
            return candidate.handle == self._last_window.handle
        except Exception:  # noqa: BLE001
            return False

    def _best_window(self, dt, wanted: str):
        """The open window that best answers ``wanted``.

        When several match equally - an old tab of the same site in
        another browser, a second copy of an app - the one nearest the
        front is the one meant. A stale "Deji - YouTube - Opera" left
        over from an hour ago otherwise beat the Chrome window that had
        just been opened for the job.
        """
        try:
            from src.windows.toplevel import ordered_titles

            depth = {t: i for i, t in enumerate(ordered_titles())}
        except Exception:  # noqa: BLE001
            depth = {}

        best = None
        best_score = 0

        for candidate in dt.windows():
            try:
                title = (candidate.window_text() or "").strip()
            except Exception:  # noqa: BLE001
                continue

            if not title:
                continue

            score = _title_score(title, wanted)
            if score <= 0:
                continue

            # Staying in the window we were just working in is almost
            # always right: "search here, then open the result" is one
            # job, and the second half should not wander elsewhere.
            # Matched on identity, not on the title - searching in
            # Explorer renames it, and that is exactly the moment the
            # continuity matters most.
            if self._same_as_last(candidate):
                score += 250

            # Nearer the front wins a tie, and only a tie: 40 points
            # across the whole Z-order cannot outrank a better title.
            place = depth.get(title)
            if place is not None:
                score += max(0, 40 - place * 2)

            if score > best_score:
                best, best_score = candidate, score

        return best

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
        max_depth: int = _DEFAULT_MAX_DEPTH,
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

        # Chromium-based apps - Steam, Discord, Spotify, anything
        # Electron - switch their accessibility tree off when nothing has
        # asked for it, and report an empty window while being perfectly
        # healthy on screen. Steam did exactly this between one run and
        # the next, and recovered on its own moments later. WM_GETOBJECT
        # is the ask a screen reader sends; the app then needs a beat to
        # build the tree, so this waits rather than deciding after one
        # try that the window is empty.
        for pause in (0.4, 1.5, 3.0):
            # Count controls worth having, not raw elements. A dormant
            # Steam still reports its window frame, so a raw count of
            # four looked healthy while the tree held nothing usable -
            # and the wake never fired. The agent-side walk has always
            # counted it this way.
            if _actionable_count(descendants) >= 4:
                break
            self._wake_accessibility(win)
            self.focus(win)
            time.sleep(pause)
            descendants = self._descendants(win, max_depth)

        # How much was there versus how much could be named. A window
        # full of unnamed Custom controls - MultiMC's Launch panel, most
        # Qt and game UIs - looks empty to a name-based search while
        # being perfectly visible on screen.
        self._last_scanned = len(descendants)

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

    def unnamed(self, title_re: str | None = None, pid: int | None = None,
                limit: int = 40) -> tuple[str, list[dict[str, Any]]]:
        """Controls that are visible but have no name.

        A Qt app or a game launcher draws its buttons without labels, so
        a search by name finds nothing while the user is looking right
        at them. These cannot be identified from the tree - only located
        - which is enough to click one and ask what it did.
        """
        win = self.window(title_re, pid)
        self._last_window = win
        self._last_spec = (title_re, pid)

        try:
            wr = win.rectangle()
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"could not measure the window: {exc}") from exc

        width = max(1, wr.width())
        height = max(1, wr.height())
        found: list[dict[str, Any]] = []

        for el in self._descendants(win, 30):
            try:
                if (el.window_text() or "").strip():
                    continue
                info = el.element_info
                if (getattr(info, "automation_id", "") or "").strip():
                    continue
                kind = info.control_type
                if kind in ("Pane", "Group", "Window", "TitleBar", "Thumb"):
                    continue

                r = el.rectangle()
                if r.width() < 16 or r.height() < 10:
                    continue
                if r.width() > width * 0.9 and r.height() > height * 0.9:
                    continue

                cx = (r.left + r.right) // 2
                cy = (r.top + r.bottom) // 2
                found.append({
                    "type": kind,
                    "center": [cx, cy],
                    "rel": [round((cx - wr.left) / width, 4),
                            round((cy - wr.top) / height, 4)],
                    "size": [r.width(), r.height()],
                })
                if len(found) >= limit:
                    break
            except Exception:  # noqa: BLE001
                continue

        found.sort(key=lambda c: (c["center"][1], c["center"][0]))
        for i, c in enumerate(found):
            c["index"] = i

        title = ""
        try:
            title = win.window_text() or ""
        except Exception:  # noqa: BLE001
            pass
        return title, found

    @staticmethod
    def _wake_accessibility(win) -> None:
        """Ask a dormant app to build its accessibility tree."""
        try:
            handle = win.handle
        except Exception:  # noqa: BLE001
            return

        if not handle:
            return

        try:
            import ctypes

            ctypes.windll.user32.SendMessageTimeoutW(
                ctypes.c_void_p(int(handle)),
                0x003D,                     # WM_GETOBJECT
                0,
                ctypes.c_long(-4),          # OBJID_CLIENT
                0x0002,                     # SMTO_ABORTIFHUNG
                600,
                ctypes.byref(ctypes.c_ulong()),
            )
        except Exception:  # noqa: BLE001
            pass

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
        if el is not None and self._still_matches(el, name):
            return el

        # Stale cache (the UI moved on) - re-read the last window once.
        if self._last_window is not None or any(self._last_spec):
            try:
                self.tree(*self._last_spec)
            except UiaError:
                pass
            el = self._resolve_cached(ref, name)
            if el is not None and self._still_matches(el, name):
                return el

        raise UiaError(
            f"no control matches ref={ref} name={name!r} - run 'tree' first, "
            "or the control may not be on screen yet"
        )

    @staticmethod
    def _still_matches(el, name: str | None) -> bool:
        """Is this element still the one that was asked for?

        Cached elements go stale on a live page - a YouTube tree read a
        second ago has been rebuilt underneath, and the handle that was
        "Deji" now points at something called "Unwatched". Clicking it
        anyway and reporting success is worse than failing: the user is
        told the right thing happened while the wrong thing did.
        """
        if not name:
            return True

        want = name.strip().lower()
        if not want:
            return True

        try:
            live = (el.window_text() or "").strip().lower()
        except Exception:  # noqa: BLE001
            return False

        if want in live:
            return True

        # The label may be empty or generic while the id still identifies
        # it - a link whose accessible name is carried by its id.
        try:
            info = el.element_info
            aid = (getattr(info, "automation_id", "") or "").strip().lower()
            if aid and want in aid:
                return True
        except Exception:  # noqa: BLE001
            pass

        return False

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
        self._raise_owner(el)
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

    @staticmethod
    def _raise_owner(el) -> None:
        """Bring the element's window to the front before touching it.

        A mouse click goes to a screen position, not to a control. With
        the target window behind another one, the click lands on
        whatever is on top - during testing a search for "Celeste" was
        typed into an entirely different application and sent. The
        isolated backend has always activated the window first; this is
        the same rule for the user's own desktop.
        """
        try:
            top = el.top_level_parent()
        except Exception:  # noqa: BLE001
            return

        try:
            if top.has_focus():
                return
        except Exception:  # noqa: BLE001
            pass

        for _ in range(2):
            try:
                top.set_focus()
                time.sleep(0.2)
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.2)

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
        if self._last_window is not None:
            self._raise_owner(self._last_window)

        from pywinauto.keyboard import send_keys

        send_keys(
            _escape(text), pause=0.01,
            with_spaces=True, with_tabs=True, with_newlines=True,
        )

    def capture_window(self, title_re: str | None = None,
                       pid: int | None = None) -> tuple[bytes, list[int]]:
        """A PNG of one window, and where it sits on screen.

        Mapping an app means comparing what the accessibility layer can
        locate against what a person can read, so both have to come from
        the same picture.
        """
        win = self.window(title_re, pid)
        self._last_window = win
        self._last_spec = (title_re, pid)

        try:
            rect = win.rectangle()
            image = win.capture_as_image()
        except Exception as exc:  # noqa: BLE001
            raise UiaError(f"could not capture the window: {exc}") from exc

        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), [rect.left, rect.top, rect.right, rect.bottom]

    def click_point(self, x: int, y: int, double: bool = False) -> None:
        """Click a screen position.

        The last resort for a control the accessibility layer will not
        name - a learned landmark rather than a guess.
        """
        from pywinauto import mouse

        if self._last_window is not None:
            self._raise_owner(self._last_window)

        mouse.move(coords=(int(x), int(y)))
        time.sleep(0.06)
        mouse.click(coords=(int(x), int(y)))
        if double:
            time.sleep(0.06)
            mouse.click(coords=(int(x), int(y)))

    def send_key(self, keys: str) -> None:
        from pywinauto.keyboard import send_keys

        send_keys(keys, with_spaces=True)

    def get_text(self, ref: int | None = None, name: str | None = None) -> str:
        """A control's value if it has one, otherwise its label.

        A value-bearing control reports its value INCLUDING when that is
        empty - falling through to the label made a cleared text box
        read back as "Text editor", which a model takes for contents.
        """
        el = self._resolve(ref, name)

        try:
            value = el.get_value()  # type: ignore[attr-defined]
            if value is not None:
                return str(value)
        except Exception:  # noqa: BLE001
            pass

        try:
            legacy = el.legacy_properties()  # type: ignore[attr-defined]
            if isinstance(legacy, dict) and legacy.get("Value") is not None:
                return str(legacy["Value"])
        except Exception:  # noqa: BLE001
            pass

        try:
            return str(el.window_text() or "")
        except Exception:  # noqa: BLE001
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
        # Both separators people write, and both at once: replacing '>'
        # with '->' first turned 'File->Exit' into 'File-->Exit', which
        # then split into 'File-' and 'Exit'.
        parts = [p.strip() for p in _MENU_SEPARATOR.split(path) if p.strip()]
        if not parts:
            raise UiaError(f"could not read a menu path from {path!r}")
        norm = "->".join(parts)

        try:
            self._last_window.menu_select(norm)
            return norm
        except Exception:  # noqa: BLE001
            pass
        # UIA apps often have no real menu bar - click the parts in turn.
        for part in parts:
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


def _actionable_count(descendants) -> int:
    """How many of these are controls a caller could actually use."""
    total = 0
    for el in descendants:
        try:
            if el.element_info.control_type in _ACTIONABLE:
                total += 1
        except Exception:  # noqa: BLE001
            continue
    return total


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


# Reverse of the table above: pywinauto syntax back to plain tokens, for
# backends that speak in key names rather than send_keys strings.
_FROM_PYWINAUTO = {
    "ENTER": "enter", "ESC": "esc", "TAB": "tab", "SPACE": "space",
    "BACKSPACE": "backspace", "DEL": "delete", "HOME": "home",
    "END": "end", "PGUP": "pageup", "PGDN": "pagedown", "UP": "up",
    "DOWN": "down", "LEFT": "left", "RIGHT": "right", "INSERT": "insert",
    "VK_LWIN": "win", "PRTSC": "printscreen", "CAPSLOCK": "capslock",
    **{f"F{i}": f"f{i}" for i in range(1, 25)},
}

_PYWINAUTO_MODIFIER = {"^": "ctrl", "%": "alt", "+": "shift"}


def key_tokens(keys: Any) -> list[list[str]]:
    """The same key spec as a list of chords, e.g. ``[["ctrl", "a"]]``.

    ``normalise_keys`` speaks pywinauto; the in-session agent speaks
    plain key names. This decodes either form - what a model emits, or
    what ``normalise_keys`` produced from it - so the two backends can
    be driven from one argument.
    """
    if isinstance(keys, (list, tuple)):
        tokens = [str(k).strip().lower() for k in keys if str(k).strip()]
        return [tokens] if tokens else []

    if not isinstance(keys, str):
        return []

    text = keys.strip()
    if not text:
        return []

    # '+' is ambiguous: pywinauto means shift, people mean "and". It is
    # only pywinauto's when it leads, as in '+n'; everywhere else the
    # unambiguous markers decide.
    pywinauto = any(ch in text for ch in "^%{}()") or text[0] in "+^%"

    if not pywinauto:
        parts = [p.strip().lower() for p in re.split(r"[+\-]", text) if p.strip()]
        return [parts] if parts else []

    chords: list[list[str]] = []
    modifiers: list[str] = []
    i = 0

    while i < len(text):
        char = text[i]

        if char in _PYWINAUTO_MODIFIER:
            modifiers.append(_PYWINAUTO_MODIFIER[char])
            i += 1
            continue

        if char == "{":
            end = text.find("}", i)
            if end < 0:
                break
            inner = text[i + 1:end]
            i = end + 1

            # An escaped brace/paren is literal text, not a key name.
            if inner in ("{", "}", "(", ")", "+", "^", "%", "~", "[", "]"):
                chords.append([*modifiers, inner])
                modifiers = []
                continue

            named = _FROM_PYWINAUTO.get(inner.upper())
            if named == "win" and i < len(text):
                # {VK_LWIN} before more keys is the Windows modifier.
                modifiers.append("win")
                continue

            chords.append([*modifiers, named or inner.lower()])
            modifiers = []
            continue

        if char == "(":
            end = text.find(")", i)
            if end < 0:
                break
            for member in text[i + 1:end]:
                if member.strip():
                    chords.append([*modifiers, member.lower()])
            i = end + 1
            modifiers = []
            continue

        chords.append([*modifiers, char.lower()])
        modifiers = []
        i += 1

    if modifiers and not chords:
        chords.append(modifiers)

    return [c for c in chords if c]
