"""The accessibility layer, reached in the session it belongs to.

UI Automation cannot cross Windows sessions: a client running in the
user's session sees the user's windows and nothing else. That made
``ui_control`` - the precise, name-addressed way Alfred works inside
apps - useless on its own private desktop, leaving only
screenshot-and-guess.

The real work lives in the agent that runs *inside* that session
(``src/windows/native/ChildInputAgent/Uia.cs``). This is the near side
of that pipe: the same method names, arguments and return types as
``UiaSession``, so ``ui_control`` cannot tell which desktop it is
driving.
"""

from __future__ import annotations

from typing import Any, Callable

from src.windows.child_session import ChildSessionError
from src.windows.uia import Control, UiaError, clean_title, key_tokens


def _control(raw: dict[str, Any]) -> Control:
    """Rebuild a Control from the agent's JSON."""
    centre = raw.get("center") or [0, 0]
    try:
        cx, cy = int(centre[0]), int(centre[1])
    except (TypeError, ValueError, IndexError):
        cx, cy = 0, 0

    return Control(
        ref=int(raw.get("ref", 0)),
        control_type=str(raw.get("type", "")),
        name=str(raw.get("name", "")),
        automation_id=str(raw.get("id") or ""),
        # The agent sends a centre point rather than a rectangle; a
        # zero-area rect at that point keeps Control.as_dict() honest.
        rect=(cx, cy, cx, cy),
        enabled=bool(raw.get("enabled", True)),
        is_password=bool(raw.get("password_field", False)),
    )


class RemoteUia:
    """``UiaSession``'s interface, executed inside Alfred's own session."""

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self._client_factory = client_factory

    # ---------------------------------------------------------------- core

    def _call(self, action: str, **arguments: Any) -> dict[str, Any]:
        try:
            client = self._client_factory()
            return client.uia(action, **arguments)
        except ChildSessionError as exc:
            message = str(exc)

            # An agent built before the accessibility layer existed
            # rejects the whole op. Say what to do about it rather than
            # leaving "unknown_op" for someone to decode.
            if "unknown_op" in message:
                raise UiaError(
                    "the agent in that session is out of date and has no "
                    "accessibility layer - rebuild it with "
                    "scripts/build-native.ps1"
                ) from exc

            raise UiaError(message) from exc
        except Exception as exc:  # noqa: BLE001
            raise UiaError(
                f"could not reach the accessibility agent: {exc}"
            ) from exc

    # ---------------------------------------------------------------- reads

    def windows(self, limit: int = 40) -> list[dict[str, Any]]:
        data = self._call("windows", limit=limit)
        found = data.get("windows")
        return found if isinstance(found, list) else []

    def focus_window(self, title_re: str | None = None,
                     pid: int | None = None) -> str:
        data = self._call(
            "focus", window=clean_title(title_re) if title_re else None, pid=pid
        )
        return str(data.get("focused") or "")

    def tree(
        self,
        title_re: str | None = None,
        pid: int | None = None,
        limit: int = 80,
        max_depth: int = 30,
        contains: str | None = None,
    ) -> tuple[str, list[Control]]:
        data = self._call(
            "tree",
            window=clean_title(title_re) if title_re else None,
            pid=pid,
            limit=limit,
            max_depth=max_depth,
            contains=contains,
        )
        controls = [
            _control(c) for c in data.get("controls", []) if isinstance(c, dict)
        ]
        return str(data.get("window") or ""), controls

    def find(self, query: str, limit: int = 20) -> list[Control]:
        data = self._call("find", query=query, limit=limit)
        return [
            _control(c) for c in data.get("controls", []) if isinstance(c, dict)
        ]

    def control_info(self, ref: int | None = None,
                     name: str | None = None) -> Control | None:
        data = self._call("info", ref=ref, name=name)
        raw = data.get("control")
        return _control(raw) if isinstance(raw, dict) else None

    def get_text(self, ref: int | None = None, name: str | None = None) -> str:
        return str(self._call("get", ref=ref, name=name).get("text") or "")

    # -------------------------------------------------------------- actions

    def click(self, ref: int | None = None, name: str | None = None,
              double: bool = False, right: bool = False) -> str:
        action = "click"
        if double:
            action = "double_click"
        elif right:
            action = "right_click"
        return str(self._call(action, ref=ref, name=name).get("clicked") or "")

    def invoke(self, ref: int | None = None, name: str | None = None) -> str:
        return str(self._call("invoke", ref=ref, name=name).get("invoked") or "")

    def type_text(self, text: str, ref: int | None = None,
                  name: str | None = None) -> None:
        self._call("type", text=text, ref=ref, name=name)

    def send_key(self, keys: Any) -> None:
        chords = key_tokens(keys)

        if not chords:
            raise UiaError(f"could not read a key from {keys!r}")

        # The agent sends one chord per call, which is also what makes a
        # sequence like '{ESC}{ENTER}' arrive in order.
        client = self._client_factory()

        for chord in chords:
            try:
                client.key(chord)
            except ChildSessionError as exc:
                raise UiaError(str(exc)) from exc

    def select(self, item: str, ref: int | None = None,
               name: str | None = None) -> str:
        data = self._call("select", item=item, ref=ref, name=name)
        return str(data.get("selected") or item)

    def expand(self, ref: int | None = None, name: str | None = None) -> str:
        return str(self._call("expand", ref=ref, name=name).get("expanded") or "")

    def scroll(self, direction: str = "down", amount: int = 3,
               ref: int | None = None, name: str | None = None) -> str:
        data = self._call(
            "scroll", direction=direction, amount=amount, ref=ref, name=name
        )
        return str(data.get("scrolled") or f"scrolled {direction}")

    def menu_select(self, path: str) -> str:
        return str(self._call("menu", path=path).get("menu") or path)

    # ---------------------------------------------------------------- waits

    def exists(self, name: str, title_re: str | None = None) -> bool:
        data = self._call(
            "exists", name=name,
            window=clean_title(title_re) if title_re else None,
        )
        return bool(data.get("exists"))

    def wait_for(self, name: str, title_re: str | None = None,
                 timeout: float = 10.0) -> bool:
        data = self._call(
            "wait_for", name=name,
            window=clean_title(title_re) if title_re else None,
            timeout=timeout,
        )
        return bool(data.get("found"))

    def wait_ready(self, title_re: str | None = None, pid: int | None = None,
                   timeout: float = 25.0, min_controls: int = 3) -> bool:
        data = self._call(
            "wait_ready",
            window=clean_title(title_re) if title_re else None,
            pid=pid,
            timeout=timeout,
            min_controls=min_controls,
        )
        return bool(data.get("ready"))
