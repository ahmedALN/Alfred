from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from src.windows.system_probe import foreground_app, is_fullscreen_foreground

NORMAL = "normal"
GAME = "game"

# Fullscreen apps that are NOT games - don't trigger low-resource mode.
_NOT_GAMES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "vlc.exe", "mpv.exe", "mpc-hc64.exe", "mpc-hc.exe", "podcastexe",
    "powerpnt.exe", "wmplayer.exe", "netflix.exe", "spotify.exe",
    "obs64.exe", "explorer.exe", "photos.exe", "prevhost.exe",
}


async def _run(fn: Callable[[], Any]) -> None:
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:  # noqa: BLE001
        print(f"[ResourceMode] step failed: {exc}")


class ResourceMode:
    """
    NORMAL <-> GAME.

    GAME: unloads the Ollama models (frees VRAM), pauses the brain and
    the task queue, stops screen capture. Voice + wake word stay live so
    the user can talk Alfred back to NORMAL. Auto-engages when a
    fullscreen game holds the foreground (opt-out), and auto-disengages
    when it goes away (only if it auto-engaged).
    """

    def __init__(
        self,
        *,
        providers: Any,
        speak: Callable[[str], Awaitable[None]],
        brain: Any = None,
        task_queue: Any = None,
        child_client: Any = None,
        autodetect: bool = True,
        detect_seconds: float = 30.0,
        clear_seconds: float = 15.0,
        fullscreen_probe: Callable[[], bool] = is_fullscreen_foreground,
        foreground_probe: Callable[[], str | None] = foreground_app,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers
        self._speak = speak
        self._brain = brain
        self._task_queue = task_queue
        self._child = child_client
        self._autodetect = autodetect
        self._detect = detect_seconds
        self._clear = clear_seconds
        self._fullscreen = fullscreen_probe
        self._foreground = foreground_probe
        self._monotonic = monotonic

        self._state = NORMAL
        self._entered_by: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_brain(self, brain: Any) -> None:
        self._brain = brain

    # ----------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def in_game_mode(self) -> bool:
        return self._state == GAME

    async def enter_game(self, source: str = "user") -> None:
        if self._state == GAME:
            return

        self._state = GAME
        self._entered_by = source
        print(f"[ResourceMode] -> GAME ({source})")

        if self._brain is not None:
            await _run(lambda: self._brain.set_paused(True))
        if self._task_queue is not None:
            await _run(self._task_queue.pause)
        if self._child is not None:
            await _run(self._child.capture_stop)

        for provider in (
            getattr(self._providers, "chat", None),
            getattr(self._providers, "plan_chat", None),
            getattr(self._providers, "vision", None),
            getattr(self._providers, "embedder", None),
        ):
            if provider is not None:
                await _run(lambda p=provider: asyncio.to_thread(p.unload))

        await self._announce(
            "Game mode - I've freed up the GPU and paused background work. "
            "Say 'back to normal' when you're done."
        )

    async def exit_game(self, source: str = "user") -> None:
        if self._state == NORMAL:
            return

        self._state = NORMAL
        self._entered_by = None
        print(f"[ResourceMode] -> NORMAL ({source})")

        if self._brain is not None:
            await _run(lambda: self._brain.set_paused(False))
        if self._task_queue is not None:
            await _run(self._task_queue.resume)
        # Screen capture restarts lazily on the next screenshot request.

        await self._announce("Back to normal - everything's running again.")

    async def _announce(self, text: str) -> None:
        try:
            await self._speak(f"(System: proactive) {text}")
        except Exception as exc:  # noqa: BLE001
            print(f"[ResourceMode] announce failed: {exc}")

    # ---- thread-safe entry points (tray, wake thread) --------------

    def request(self, target: str) -> None:
        if self._loop is None:
            return
        coro = self.enter_game("tray") if target == GAME else self.exit_game("tray")
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as exc:  # noqa: BLE001
            print(f"[ResourceMode] request failed: {exc}")

    def toggle(self) -> None:
        self.request(NORMAL if self._state == GAME else GAME)

    # ---- auto-detect loop -----------------------------------------

    def _looks_like_a_game(self) -> bool:
        if not self._fullscreen():
            return False
        app = (self._foreground() or "").lower()
        return app not in _NOT_GAMES

    async def run(self, poll_seconds: float = 10.0) -> None:
        self._loop = asyncio.get_running_loop()

        if not self._autodetect:
            while True:
                await asyncio.sleep(3600)

        fs_since: float | None = None
        clear_since: float | None = None

        while True:
            await asyncio.sleep(poll_seconds)

            now = self._monotonic()

            try:
                is_game = self._looks_like_a_game()
            except Exception:  # noqa: BLE001
                is_game = False

            if is_game:
                clear_since = None
                fs_since = fs_since if fs_since is not None else now
                if self._state == NORMAL and now - fs_since >= self._detect:
                    await self.enter_game("auto: fullscreen game")
            else:
                fs_since = None
                clear_since = clear_since if clear_since is not None else now
                if (
                    self._state == GAME
                    and self._entered_by
                    and self._entered_by.startswith("auto")
                    and now - clear_since >= self._clear
                ):
                    await self.exit_game("auto: game closed")
