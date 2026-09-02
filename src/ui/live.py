"""The parts of the interface that have to come from the running Alfred.

state.py reads files and works whether or not Alfred is up. This does
not: the log stream, the microphone, the text box and the screen only
mean anything while there is a process to ask.

Everything here is optional and every hook defaults to absent, because
the window must still open - and still be useful - when Alfred is not
running. A dead Alfred is exactly when you want to read its logs.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any


class Bus:
    """One publisher, many windows, no blocking.

    Alfred writes to this from whatever thread it happens to be on -
    the brain loop, the voice thread, a tool. Subscribers are asyncio
    queues living on the server's loop, so handing an item over has to
    cross threads, which is what call_soon_threadsafe is for.

    Subscribers that stop draining are dropped rather than allowed to
    grow without limit. A window that has gone away must not be able to
    exhaust the memory of the process it was watching.
    """

    def __init__(self, keep: int = 600) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=keep)
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, kind: str, **data: Any) -> None:
        event = {"kind": kind, "at": time.time(), **data}
        with self._lock:
            self._history.append(event)
            subscribers = list(self._subscribers)
            loop = self._loop

        if loop is None or loop.is_closed():
            return
        for queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, event)
            except RuntimeError:
                pass

    @staticmethod
    def _offer(queue: asyncio.Queue, event: dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # The window is not keeping up. Losing a log line is better
            # than growing forever on its behalf.
            pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=400)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)[-limit:]

    @property
    def listeners(self) -> int:
        with self._lock:
            return len(self._subscribers)


BUS = Bus()


class Tee:
    """Alfred's own printing, mirrored into the window.

    Alfred already narrates itself to stdout in some detail, and that
    narration is the log panel. Rather than invent a second logging
    system and have to convert every print, the stream is wrapped: the
    terminal still gets everything, and the window gets a copy.
    """

    def __init__(self, wrapped: Any, bus: Bus, stream: str = "out") -> None:
        self._wrapped = wrapped
        self._bus = bus
        self._stream = stream
        self._partial = ""

    def write(self, text: str) -> int:
        try:
            written = self._wrapped.write(text)
        except Exception:  # noqa: BLE001
            written = len(text)

        # Print arrives in fragments; the window wants whole lines.
        self._partial += text
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            if line.strip():
                self._bus.publish("log", stream=self._stream, line=line[:2000])
        return written

    def flush(self) -> None:
        try:
            self._wrapped.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._wrapped.isatty())
        except Exception:  # noqa: BLE001
            return False

    def __getattr__(self, item: str) -> Any:
        return getattr(self._wrapped, item)


_teed = False


def capture_output(bus: Bus = BUS) -> None:
    """Start mirroring stdout and stderr into the bus. Safe to repeat."""
    global _teed
    if _teed:
        return
    sys.stdout = Tee(sys.stdout, bus, "out")
    sys.stderr = Tee(sys.stderr, bus, "err")
    _teed = True


class Live:
    """Handles into the running Alfred, all of them optional.

    main() fills these in as it builds. The server checks each before
    using it and says so plainly when one is absent, rather than
    presenting a dead button.
    """

    def __init__(self) -> None:
        self.started_at: float = time.time()
        self.session_id: str = ""

        # Callables, set by main(). None means "Alfred is not running,
        # or was built without that part".
        self.say: Callable[[str], Any] | None = None
        self.wake: Callable[[], Any] | None = None
        self.sleep: Callable[[], Any] | None = None
        self.screenshot: Callable[[], Any] | None = None
        self.windows: Callable[[], Any] | None = None
        self.cancel_task: Callable[[str], Any] | None = None
        self.steer: Callable[[str], Any] | None = None

        self._speaking = False
        self._level = 0.0
        self._current: dict[str, Any] | None = None

    # -------------------------------------------------------- what it is

    @property
    def running(self) -> bool:
        """Is there an Alfred behind this window, or only its files?"""
        return self.say is not None

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    def abilities(self) -> dict[str, bool]:
        return {
            "talk": self.say is not None,
            "mic": self.wake is not None,
            "screen": self.screenshot is not None,
            "steer": self.steer is not None,
            "cancel": self.cancel_task is not None,
        }

    # ------------------------------------------------------------- voice

    @property
    def speaking(self) -> bool:
        return self._speaking

    def set_speaking(self, speaking: bool) -> None:
        """Sound effects duck under this, so it is published either way."""
        if speaking != self._speaking:
            self._speaking = speaking
            BUS.publish("speaking", speaking=speaking)

    def set_level(self, level: float) -> None:
        """Microphone loudness, 0..1, for the visualiser."""
        self._level = max(0.0, min(1.0, float(level)))
        BUS.publish("level", level=self._level)

    @property
    def level(self) -> float:
        return self._level

    # ------------------------------------------------------------ hello

    def hello(self, line: str, aloud: bool = True) -> None:
        """Alfred came up, and this is what it said about it."""
        BUS.publish("hello_said", text=line, aloud=aloud)

    # -------------------------------------------------------------- task

    @property
    def current_task(self) -> dict[str, Any] | None:
        return dict(self._current) if self._current else None

    def task_started(self, task_id: str, goal: str) -> None:
        self._current = {"id": task_id, "goal": goal, "steps": [],
                         "started": time.time()}
        BUS.publish("task_started", id=task_id, goal=goal)

    def task_step(self, what: str, detail: str = "") -> None:
        if self._current is not None:
            self._current["steps"].append({"what": what, "detail": detail})
        BUS.publish("task_step", what=what, detail=detail)

    def task_ended(self, task_id: str, status: str, summary: str = "") -> None:
        self._current = None
        BUS.publish("task_ended", id=task_id, status=status, summary=summary)


LIVE = Live()
