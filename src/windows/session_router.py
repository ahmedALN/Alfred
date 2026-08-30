from __future__ import annotations

import threading

from src.windows.child_session import ChildSessionClient, ChildSessionError


class SessionRouter:
    """Decides WHICH desktop the input/capture tools act on.

    Alfred normally works on the user's own desktop. When a task says
    "without disturbing me", the task worker points this at the isolated
    child session for the duration, and every tool that drives mouse,
    keyboard or screen capture follows - without each of them having to
    know about isolation.

    Connections are cached per target, because reconnecting a named pipe
    for every click would be slow and would drop the agent's capture
    state between calls.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._target: str = "current"
        self._clients: dict[str, ChildSessionClient] = {}

    # ---------------------------------------------------------------- target

    @property
    def target(self) -> str:
        return self._target

    def use_isolated(self) -> None:
        with self._lock:
            self._target = "child"

    def use_users_desktop(self) -> None:
        with self._lock:
            self._target = "current"

    @property
    def isolated(self) -> bool:
        return self._target == "child"

    # ---------------------------------------------------------------- client

    def client(self) -> ChildSessionClient:
        """A connected client for the current target.

        Falls back to the user's desktop if the isolated session has no
        reachable agent - a degraded result the user can see beats a
        task that fails outright.
        """
        return self.client_for(self._target, fallback=True)

    def client_for(
        self, target: str, fallback: bool = False
    ) -> ChildSessionClient:
        """A connected client for a named target, whatever is current.

        The agent accepts one connection at a time, so everything that
        talks to a session has to share the same one - a second
        connection is refused as ERROR_PIPE_BUSY. Callers that must not
        be silently redirected (opening an app on the private desktop,
        say) leave ``fallback`` off and handle the failure themselves.
        """
        with self._lock:
            client = self._clients.get(target)

            if client is not None:
                try:
                    client.ping()
                    return client
                except ChildSessionError:
                    client.close()
                    self._clients.pop(target, None)

            fresh = ChildSessionClient(target)
            try:
                fresh.connect()
            except ChildSessionError:
                fresh.close()
                if fallback and target == "child":
                    print(
                        "[Router] no agent in the isolated session - "
                        "falling back to the user's desktop."
                    )
                    return self._connect_current()
                raise

            self._clients[target] = fresh
            return fresh

    def _connect_current(self) -> ChildSessionClient:
        client = self._clients.get("current")
        if client is None:
            client = ChildSessionClient("current")
            client.connect()
            self._clients["current"] = client
        return client

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
            self._clients.clear()


# The one router the tools and the task worker share.
ROUTER = SessionRouter()
