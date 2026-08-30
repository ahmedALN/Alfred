from __future__ import annotations

import re
from typing import Any

from src.tools.base import AlfredTool
from src.windows.screens import assess, draws_its_own_ui
from src.windows.uia import UiaError, UiaSession, normalise_keys

_ACTIONS = (
    "windows", "focus", "tree", "find", "click", "double_click",
    "right_click", "invoke", "type", "key", "get", "select", "expand",
    "scroll", "menu", "wait_for", "wait_ready", "exists",
    # Whole jobs rather than single gestures. "Search this app for X"
    # was five calls - read the tree, pick the box, click it, type,
    # press Enter - and every one of them a chance to act on the wrong
    # control. One call is both faster (one model turn, not five) and
    # more accurate, because choosing the field is done here against the
    # real tree instead of guessed from a list.
    "search", "open_item",
)

# What an app's search box tends to be called.
_SEARCH_HINT = re.compile(
    r"search|find|query|filter|look\s?up|what do you want|type here|"
    r"address and search|omnibox|enter a name",
    re.I,
)

# Things you open by name: a library row, a tree node, a tile, a link.
_ROW_TYPES = {"ListItem", "TreeItem", "DataItem"}
_CLICKABLE_TYPES = {"Button", "Hyperlink", "MenuItem", "TabItem"}
# A Text is usually the label INSIDE the thing you meant to click. It
# often works, because the label sits on top of the link - but when both
# are on offer the link is the safer target.
_LABEL_TYPES = {"Text"}

# Actions that act on one control and so need the right window read.
_NEEDS_WINDOW = {
    "click", "double_click", "right_click", "invoke", "get", "select",
    "expand", "type",
}

# Field names that mean "secret" - Alfred never types into these.
_SECRET_NAME = re.compile(
    r"\b(password|passwd|pwd|passcode|pass\s*phrase|passphrase|pin|"
    r"security\s*code|secret|cvv|cvc|card\s*number|otp|"
    r"one[-\s]?time\s*code|2fa|authenticator|recovery\s*key|"
    r"private\s*key|api\s*key|token)\b",
    re.I,
)

_SECRET_REFUSAL = (
    "Alfred will not type credentials. Tell the user the sign-in screen "
    "is ready and ask them to enter it themselves (or use their password "
    "manager); once they say they're signed in, carry on with the rest of "
    "the task."
)


def _as_int(value: Any) -> int | None:
    """Models pass refs as "0" or 0 interchangeably - accept both."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


class UIControlTool(AlfredTool):
    """
    Control real Windows apps through the accessibility layer - reads
    buttons/fields/menu items by name and drives them exactly. No
    screenshots, no guessed coordinates. This is the right tool for
    Spotify, browsers, File Explorer, Settings, Office, launchers and
    most game menus.
    """

    name = "ui_control"

    description = (
        "Drive a Windows app precisely via its accessibility tree - the "
        "main way to do work INSIDE an app. For the common jobs reach for "
        "the whole-job actions first: after open_app, wait_ready then "
        "search (types into the app's own search box and presses Enter) "
        "then open_item (opens a result, library row, tile or save by "
        "name). 'open Steam, search Hades, open it' is three calls, not a "
        "dozen. Drop to tree/click/type only when those cannot express "
        "what you need. "
        "Actions: windows (list open windows); focus (bring a window "
        "forward); tree window= [contains=] (list controls, each with a "
        "ref); find query= (search the current window's controls); click / "
        "double_click / right_click / invoke ref=|name=; type text= "
        "into=ref (ALWAYS name the field - giving only the window types "
        "into whatever happens to be focused inside it); key keys= (e.g. '^l' Ctrl+L, '{ENTER}', "
        "'{ESC}', '{TAB}') - 'keys' may also accompany 'type' to press a "
        "key straight after, e.g. type text='drake' into=7 keys='{ENTER}'; "
        "get ref=|name= (read a control's text); select "
        "item= ref=|name= (combo box / list / tab); expand ref=|name=; "
        "scroll direction= [amount=] ; menu path='File->Save As'; "
        "wait_ready window= [timeout=] [min_controls=] (wait for a "
        "just-launched app or a loading web page to become usable - on a "
        "website pass min_controls=40 or you will read it half-built); wait_for name= [timeout=]; exists name=; "
        "search window= text= [submit=false] (find this app's search "
        "box, replace what is in it, type, press Enter - use this "
        "instead of hunting for the field yourself); open_item "
        "window= name= (find a list row, tile, tree node, link or "
        "button by name and open it - double-clicks rows, clicks "
        "buttons; returns 'not_found' with nearby names if there is "
        "no match). "
        "Prefer this over desktop_control - it is exact. Alfred refuses to "
        "type into password fields."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "window": {
                    "type": "string",
                    "description": "Window title substring (or regex).",
                },
                "pid": {"type": "integer"},
                "ref": {"type": "integer", "description": "Control ref from 'tree'."},
                "name": {"type": "string", "description": "Control name to match."},
                "text": {"type": "string", "description": "Text to type."},
                "into": {
                    "type": "integer",
                    "description": "ref of the field to type into.",
                },
                "item": {
                    "type": "string",
                    "description": "Item to pick, for 'select' / 'open_item'.",
                },
                "query": {
                    "type": "string",
                    "description": "Search text, for 'find'.",
                },
                "contains": {
                    "type": "string",
                    "description": "Only list controls mentioning this, for 'tree'.",
                },
                "path": {
                    "type": "string",
                    "description": "Menu path, e.g. 'File->Save As'.",
                },
                "keys": {
                    "type": "string",
                    "description": "Key string, e.g. '^a', '{ENTER}', '%{F4}'.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                },
                "amount": {"type": "integer"},
                "timeout": {"type": "number"},
                "limit": {
                    "type": "integer",
                    "description": (
                        "Max controls for 'tree' (default 80, up to 500). "
                        "Raise it on a busy page - a website's real "
                        "content sits after its navigation."
                    ),
                },
                "max_depth": {
                    "type": "integer",
                    "description": "How deep to walk, for 'tree'. Default 30.",
                },
                "submit": {
                    "type": "boolean",
                    "description": (
                        "For 'search': press Enter afterwards. Default "
                        "true. Set false for a box that filters as you type."
                    ),
                },
                "min_controls": {
                    "type": "integer",
                    "description": (
                        "For 'wait_ready': how many controls count as "
                        "loaded. A web page reports a handful while it is "
                        "still building - ask for 40+ before reading it."
                    ),
                },
            },
            "required": ["action"],
        }

    def __init__(
        self,
        session: UiaSession | None = None,
        router: Any = None,
        remote: Any = None,
    ) -> None:
        self._uia = session or UiaSession()
        # UI Automation is session-scoped, so the local backend can only
        # ever see the user's screen. When Alfred is working on its own
        # desktop the identical calls have to run inside that session,
        # via the agent that lives there.
        self._router = router
        self._remote = remote

    # ----------------------------------------------------------------

    def _backend(self) -> Any:
        if (
            self._remote is not None
            and self._router is not None
            and getattr(self._router, "isolated", False)
        ):
            return self._remote
        return self._uia

    def _secret_guard(self, ui: Any, ref: int | None, name: str | None,
                      text: str) -> dict[str, Any] | None:
        """Refuse to type into a masked or obviously-secret field."""
        info = None
        try:
            info = ui.control_info(ref, name)
        except Exception:  # noqa: BLE001
            info = None

        if info is not None and getattr(info, "is_password", False):
            return {
                "status": "refused",
                "error": "that is a password field",
                "instruction": _SECRET_REFUSAL,
            }

        target_name = (getattr(info, "name", "") or name or "")
        if target_name and _SECRET_NAME.search(target_name):
            return {
                "status": "refused",
                "error": f"the field {target_name!r} asks for a secret",
                "instruction": _SECRET_REFUSAL,
            }

        return None

    # ---------------------------------------------------- picking a target

    @staticmethod
    def _type_into(ui: Any, field: Any, text: str) -> bool:
        """Put text in a field and check it actually arrived."""
        try:
            ui.click(ref=field.ref)
            ui.send_key("^a")
            ui.type_text(text)
        except UiaError:
            return False

        # Reading it back is what turns "I typed" into "it is there".
        try:
            return text.strip().lower() in ui.get_text(field.ref).strip().lower()
        except UiaError:
            return False

    @staticmethod
    def _pick_search_field(controls: list[Any]) -> Any:
        """The control most likely to be this app's search box.

        A named "Search" field beats an unnamed text box, and an unnamed
        text box beats nothing - but the caller is told which it got, so
        a guess is visible rather than silent.
        """
        best = None
        best_score = 0

        for control in controls:
            if control.control_type not in ("Edit", "ComboBox", "Document"):
                continue
            if control.is_password or not control.enabled:
                continue

            score = 1
            haystack = f"{control.name} {control.automation_id}"
            if _SEARCH_HINT.search(haystack):
                score += 10
            if control.control_type == "Edit":
                score += 2
            if control.name:
                score += 1

            if score > best_score:
                best, best_score = control, score

        return best

    @classmethod
    def _pick_item(cls, controls: list[Any], wanted: str) -> Any:
        """The control that best answers "open <wanted>".

        Anything genuinely interactive is tried first. A Text is usually
        the label sitting on top of the thing you meant - in Steam's
        results the row is a Hyperlink "Hades 17 Sep, 2020" with a Text
        "Hades" over it, and the label is the better literal match while
        the link is the thing that navigates. Labels are still used when
        nothing interactive matches at all, because in plenty of apps
        the label IS the control.
        """
        interactive = [
            c for c in controls
            if c.control_type in (_ROW_TYPES | _CLICKABLE_TYPES)
        ]

        return (
            cls._best_match(interactive, wanted)
            or cls._best_match(controls, wanted)
        )

    @staticmethod
    def _best_match(controls: list[Any], wanted: str) -> Any:
        want = wanted.strip().lower()
        if not want:
            return None

        best = None
        best_score = -1000

        for control in controls:
            name = control.name.strip().lower()
            identifier = (control.automation_id or "").strip().lower()

            if name == want or identifier == want:
                score = 100
            elif name.startswith(want):
                # Where the match ends matters. MultiMC lists "1.21.11
                # Instance" beside "1.21.11afk1 Instance" and
                # "1.21.11-Hardcore Instance"; only the first is what
                # "the 1.21.11 instance" means, and it is the one where
                # the name ends at a word boundary.
                rest = name[len(want):]
                score = 85 if (not rest or rest[0] in " 	:-_(" ) else 60
                if rest[:1].isalnum():
                    score = 45
            elif want in name:
                score = 30
            elif identifier and want in identifier:
                score = 20
            else:
                continue

            if control.control_type in _ROW_TYPES:
                score += 8
            elif control.control_type in _CLICKABLE_TYPES:
                score += 6
            elif control.control_type in _LABEL_TYPES:
                score += 1

            if not control.enabled:
                score -= 40

            # A tight match is usually the shorter one: "1.21.11" should
            # beat "1.21.11 (needs update, last played...)".
            score -= min(len(name) // 20, 5)

            if score > best_score:
                best, best_score = control, score

        return best

    @staticmethod
    def _needs_user(ui: Any, window: str | None,
                    pid: int | None) -> dict[str, Any]:
        """Whatever this screen is asking the user, if anything.

        A profile picker or a sign-in screen is a populated, perfectly
        healthy window - nothing downstream can tell it apart from the
        app being ready, so the agent starts clicking and picks an
        account at random.
        """
        try:
            title, controls = ui.tree(window, pid, limit=80)
        except UiaError:
            return {}

        if draws_its_own_ui(controls):
            return {
                "renders_own_ui": True,
                "window": title,
                "instruction": (
                    "This window paints its own interface and exposes "
                    "nothing to the accessibility layer - Roblox and most "
                    "games are like this. ui_control cannot see inside "
                    "it. Do not retry or wait longer. Use "
                    "desktop_control (screenshot, then click by "
                    "coordinates), or do the job on the app's website in "
                    "a browser, which is accessible."
                ),
            }

        need = assess(title, controls)
        if need is None:
            return {}

        found = need.as_dict()
        found["window"] = title
        return found

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        if action not in _ACTIONS:
            return {
                "status": "error",
                "error": f"action must be one of {list(_ACTIONS)}",
            }

        window = arguments.get("window")
        pid = _as_int(arguments.get("pid"))
        ref = _as_int(arguments.get("ref"))
        name = arguments.get("name")
        timeout = arguments.get("timeout")
        timeout = float(timeout) if isinstance(timeout, (int, float)) else None

        ui = self._backend()

        # Targeting a control BY NAME in a named window has to read that
        # window first. Without this, "click 'Don't update yet' in
        # MultiMC" resolved against whatever tree happened to be cached -
        # usually the wrong window, or none at all - and failed with "no
        # control matches" while the control was plainly on screen.
        # A ref is never re-read: that would renumber it.
        if (
            action in _NEEDS_WINDOW
            and window
            and ref is None
            and isinstance(name, str)
            and name
        ):
            try:
                ui.tree(window, pid, limit=200, contains=name)
            except UiaError:
                pass

        try:
            if action == "windows":
                wins = ui.windows()
                return {"status": "success", "count": len(wins), "windows": wins}

            if action == "focus":
                title = ui.focus_window(window, pid)
                return {"status": "success", "focused": title or window}

            if action == "tree":
                # A rich page has hundreds of controls and the
                # interesting ones are rarely in the first 80: on a
                # YouTube channel the videos start around 80, so the
                # default cut the list off just before the content.
                limit = _as_int(arguments.get("limit")) or 80
                depth = _as_int(arguments.get("max_depth"))
                title, controls = ui.tree(
                    window, pid,
                    limit=max(1, min(limit, 500)),
                    contains=arguments.get("contains"),
                    **({"max_depth": depth} if depth else {}),
                )
                return {
                    "status": "success",
                    "window": title,
                    "count": len(controls),
                    "controls": [c.as_dict() for c in controls],
                }

            if action == "find":
                query = arguments.get("query") or name
                if not isinstance(query, str) or not query:
                    return {"status": "error", "error": "'find' needs 'query'."}
                hits = ui.find(query)
                return {
                    "status": "success",
                    "count": len(hits),
                    "controls": [c.as_dict() for c in hits],
                }

            if action in ("click", "double_click", "right_click"):
                out = ui.click(
                    ref, name,
                    double=(action == "double_click"),
                    right=(action == "right_click"),
                )
                return {"status": "success", "clicked": out or name or ref}

            if action == "invoke":
                out = ui.invoke(ref, name)
                return {"status": "success", "invoked": out or name or ref}

            if action == "type":
                text = arguments.get("text")
                if not isinstance(text, str):
                    return {"status": "error", "error": "'type' needs 'text'."}
                into = _as_int(arguments.get("into"))
                if into is None:
                    into = ref
                    # "into" may name the field rather than reference it.
                    raw_into = arguments.get("into")
                    if name is None and isinstance(raw_into, str) and raw_into:
                        name = raw_into
                refused = self._secret_guard(ui, into, name, text)
                if refused is not None:
                    return refused

                # Naming a window and no control used to mean "type into
                # whatever happens to have focus" - which could be any
                # app at all, including one of the user's. If a window is
                # named, it gets the keystrokes.
                if into is None and not name and window:
                    try:
                        ui.focus_window(window, pid)
                    except UiaError:
                        pass

                ui.type_text(text, into, name if into is None else None)
                result: dict[str, Any] = {"status": "success", "typed": text}

                # Models write type(text=..., keys='{ENTER}') to mean
                # "type this, then press Enter". That used to be dropped
                # in silence, so a search was filled in and never run.
                follow_up = normalise_keys(
                    arguments.get("keys") or arguments.get("key")
                )
                if follow_up:
                    ui.send_key(follow_up)
                    result["then_pressed"] = follow_up

                return result

            if action == "key":
                keys = normalise_keys(
                    arguments.get("keys") or arguments.get("key")
                )
                if not keys:
                    return {
                        "status": "error",
                        "error": "'key' needs 'keys', e.g. '^a', 'ctrl+a' or "
                                 "'{ENTER}'.",
                    }
                ui.send_key(keys)
                return {"status": "success", "keys": keys}

            if action == "get":
                return {"status": "success", "text": ui.get_text(ref, name)}

            if action == "select":
                item = arguments.get("item")
                if not isinstance(item, str) or not item:
                    return {"status": "error", "error": "'select' needs 'item'."}
                return {
                    "status": "success",
                    "selected": ui.select(item, ref, name),
                }

            if action == "expand":
                return {
                    "status": "success",
                    "expanded": ui.expand(ref, name),
                }

            if action == "scroll":
                return {
                    "status": "success",
                    "scrolled": ui.scroll(
                        str(arguments.get("direction", "down")),
                        int(arguments.get("amount", 3) or 3),
                        ref, name,
                    ),
                }

            if action == "menu":
                path = arguments.get("path")
                if not isinstance(path, str) or not path:
                    return {"status": "error", "error": "'menu' needs 'path'."}
                return {"status": "success", "menu": ui.menu_select(path)}

            if action == "search":
                text = arguments.get("text") or arguments.get("query")
                if not isinstance(text, str) or not text.strip():
                    return {
                        "status": "error",
                        "error": "'search' needs 'text' - what to search for.",
                    }

                # A filtered read finds "Search the store" in a fraction
                # of the time a full one takes, and most apps label
                # their box. The whole tree is the fallback.
                _, controls = ui.tree(window, pid, limit=60, contains="search")
                field = self._pick_search_field(controls)

                if field is None:
                    _, controls = ui.tree(window, pid, limit=300)
                    field = self._pick_search_field(controls)

                if field is None:
                    # The picker skips masked fields, so a sign-in form
                    # looks like "no search box". Say what is actually
                    # being asked for instead.
                    if any(getattr(c, "is_password", False) for c in controls):
                        return {
                            "status": "refused",
                            "error": "that window is asking for a password",
                            "instruction": _SECRET_REFUSAL,
                        }

                    asking = self._needs_user(ui, window, pid)
                    if asking:
                        return {"status": "needs_user", **asking}

                    return {
                        "status": "error",
                        "error": "no search box found in that window",
                        "instruction": (
                            "Read the tree and look for the field yourself, "
                            "then use type into=<ref>."
                        ),
                    }

                refused = self._secret_guard(ui, field.ref, field.name, text)
                if refused is not None:
                    return refused

                # Click rather than SetValue: many search boxes only run
                # their handler on real keystrokes.
                landed = self._type_into(ui, field, text)

                if not landed:
                    # Apps interrupt: Steam threw a "Special Offers"
                    # window over the top mid-search. Saying it worked
                    # when the box is still empty is the worst outcome,
                    # so try once more and then say so.
                    landed = self._type_into(ui, field, text)

                if not landed:
                    return {
                        "status": "error",
                        "error": (
                            f"could not get {text!r} into "
                            f"{field.name or 'the search box'} - something "
                            "took the focus"
                        ),
                        "instruction": (
                            "Check 'windows' for a popup that appeared over "
                            "the app, close or dismiss it, then search again."
                        ),
                    }

                out: dict[str, Any] = {
                    "status": "success",
                    "searched_for": text,
                    "field": field.name or field.automation_id or "the text box",
                    "guessed_field": not bool(
                        _SEARCH_HINT.search(f"{field.name} {field.automation_id}")
                    ),
                }

                if arguments.get("submit", True):
                    ui.send_key("{ENTER}")
                    out["submitted"] = True

                return out

            if action == "open_item":
                wanted = (
                    arguments.get("name")
                    or arguments.get("item")
                    or arguments.get("text")
                )
                if not isinstance(wanted, str) or not wanted.strip():
                    return {
                        "status": "error",
                        "error": "'open_item' needs 'name' - what to open.",
                    }

                # A filtered read is much smaller and much faster; fall
                # back to the whole tree when the name is worded loosely.
                _, controls = ui.tree(window, pid, limit=300, contains=wanted)
                target = self._pick_item(controls, wanted)

                if target is None:
                    _, controls = ui.tree(window, pid, limit=400)
                    target = self._pick_item(controls, wanted)

                if target is None:
                    nearby = [
                        c.name for c in controls
                        if c.name and c.control_type in (
                            _ROW_TYPES | _CLICKABLE_TYPES | _LABEL_TYPES
                        )
                    ][:12]
                    asking = self._needs_user(ui, window, pid)
                    if asking:
                        return {"status": "needs_user", **asking}

                    return {
                        "status": "not_found",
                        "error": f"nothing named like {wanted!r} in that window",
                        "nearby": nearby,
                        "instruction": (
                            "Relay these names and ask which one they meant, "
                            "or scroll and try again."
                        ),
                    }

                # A library row opens on double click; a button or link
                # opens on one.
                double = target.control_type in _ROW_TYPES
                ui.click(ref=target.ref, double=double)

                return {
                    "status": "success",
                    "opened": target.name or wanted,
                    "control": target.control_type,
                    "via": "double click" if double else "click",
                }

            if action == "exists":
                if not isinstance(name, str):
                    return {"status": "error", "error": "'exists' needs 'name'."}
                return {
                    "status": "success",
                    "exists": ui.exists(name, window),
                }

            if action == "wait_for":
                if not isinstance(name, str):
                    return {"status": "error", "error": "'wait_for' needs 'name'."}
                found = ui.wait_for(name, window, timeout or 10.0)
                return {"status": "success", "found": found}

            if action == "wait_ready":
                # A website's accessibility tree fills in lazily: a
                # YouTube channel reports 18 controls one moment and 170
                # the next. Waiting for a real number of controls is the
                # difference between reading a half-built page and the
                # actual content.
                minimum = _as_int(arguments.get("min_controls")) or 3
                ready = ui.wait_ready(
                    window, pid, timeout or 25.0, min_controls=minimum,
                )
                if ready:
                    out = {"status": "success", "ready": True}
                    out.update(self._needs_user(ui, window, pid))
                    return out

                # Say what IS on screen. Steam waiting on "Sign in to
                # Steam" is a different problem from Steam not starting,
                # and timing out silently makes them look identical.
                try:
                    open_now = [w["title"] for w in ui.windows()][:10]
                except UiaError:
                    open_now = []

                failure: dict[str, Any] = {
                    "status": "error",
                    "ready": False,
                    "error": (
                        f"{window or 'the window'} did not become usable "
                        "in time"
                    ),
                    "windows_open": open_now,
                    "instruction": (
                        "Look at windows_open. If one is a sign-in or "
                        "update prompt, tell the user and ask them to "
                        "deal with it - do not type credentials. If the "
                        "app simply needs longer, wait_ready again."
                    ),
                }
                # A specific diagnosis replaces the general advice.
                failure.update(self._needs_user(ui, window, pid))
                return failure

            return {"status": "error", "error": "unhandled action"}

        except UiaError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
