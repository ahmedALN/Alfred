"""The interface's own little web server, on 127.0.0.1 and nowhere else.

Being bound to loopback is necessary and not sufficient. Any web page
you happen to have open can also make requests to 127.0.0.1, and this
server can read your mail, your memories and your screen, and delete
what Alfred believes. So there is a token: generated fresh each time
the server starts, handed to the window in its URL, and required on
every call. A random site cannot guess it.

The Host check is the other half. Without it, a hostile page can point
a domain it controls at 127.0.0.1 and reach this from a context the
browser considers same-origin.
"""

# No "from __future__ import annotations" here, deliberately.
#
# With it every annotation becomes a string, and FastAPI resolves
# that string against the MODULE namespace. The fastapi imports
# live inside build() so importing Alfred does not drag in the web
# stack - so "socket: WebSocket" resolved to nothing, FastAPI read
# the parameter as a query field instead, and every websocket
# handshake was refused 403 before the handler ran. Nothing in the
# logs said so; it looked exactly like a bad token.

import asyncio
import json
import mimetypes
import secrets
import threading
from pathlib import Path
from typing import Any

from src.ui import edits, state
from src.ui.live import BUS, LIVE

_STATIC = Path(__file__).resolve().parent / "static"

# Loopback only. Never bind this to 0.0.0.0.
HOST = "127.0.0.1"
DEFAULT_PORT = 8756


class Interface:
    """The server, and the handle used to start and stop it."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/?k={self.token}"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------- app

    def build(self):
        from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
        from fastapi.responses import (
            FileResponse, HTMLResponse, JSONResponse, Response,
        )

        app = FastAPI(title="Alfred", docs_url=None, redoc_url=None)

        def allowed(request: Request) -> bool:
            host = (request.headers.get("host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return False
            given = (
                request.query_params.get("k")
                or request.headers.get("x-alfred-key")
                or ""
            )
            return secrets.compare_digest(given, self.token)

        @app.middleware("http")
        async def guard(request: Request, call_next):
            # The page itself carries the key in its query string; the
            # assets it then loads inherit it via the referer-free
            # fetches app.js makes, so everything is checked the same way.
            if request.url.path.startswith(("/api", "/ws")) and not allowed(request):
                return JSONResponse({"error": "not for you"}, status_code=403)
            if request.url.path == "/" and not allowed(request):
                return HTMLResponse(
                    "<h1>Alfred</h1><p>Open this from Alfred, not by hand.</p>",
                    status_code=403,
                )
            return await call_next(request)

        # ---------------------------------------------------- the page

        @app.get("/")
        async def index() -> Any:
            page = _STATIC / "index.html"
            if not page.exists():
                return HTMLResponse("<h1>Alfred</h1><p>No interface built.</p>")
            return HTMLResponse(page.read_text(encoding="utf-8"))

        @app.get("/static/{path:path}")
        async def static(path: str) -> Any:
            target = (_STATIC / path).resolve()
            # Nothing outside the static folder, whatever the path says.
            if not str(target).startswith(str(_STATIC.resolve())):
                return Response(status_code=404)
            if not target.exists() or not target.is_file():
                return Response(status_code=404)
            kind, _ = mimetypes.guess_type(str(target))
            return FileResponse(
                target,
                media_type=kind or "application/octet-stream",
                # Everything here is on this machine, so caching saves
                # nothing measurable and costs an edit that appears not
                # to have worked. Always send the file as it is now.
                headers={"Cache-Control": "no-store"},
            )

        # -------------------------------------------------- what it knows

        _PANELS = {
            "overview": state.overview,
            "life": state.life,
            "memory": state.memory,
            "episodes": state.episodes,
            "skills": state.skills,
            "limitations": state.limitations,
            "apps": state.apps,
            "tasks": state.tasks,
            "automations": state.automations,
            "activity": state.activity,
            "undo": state.undo,
            "thinking": state.thinking,
        }

        @app.get("/api/all")
        async def all_of_it() -> Any:
            data = await asyncio.to_thread(state.everything)
            data["alfred"] = {
                "running": LIVE.running,
                "uptime": LIVE.uptime,
                "abilities": LIVE.abilities(),
                "speaking": LIVE.speaking,
                "task": LIVE.current_task,
            }
            return JSONResponse(data)

        @app.get("/api/panel/{name}")
        async def panel(name: str) -> Any:
            reader = _PANELS.get(name)
            if reader is None:
                return JSONResponse({"error": "no such panel"}, status_code=404)
            return JSONResponse({"name": name,
                                 "data": await asyncio.to_thread(reader)})

        @app.get("/api/logs")
        async def logs(limit: int = 200) -> Any:
            return JSONResponse({"lines": BUS.history(limit)})

        # ------------------------------------------------- changing it

        @app.post("/api/act")
        async def act(request: Request) -> Any:
            body = await request.json()
            action = str(body.get("action") or "")
            payload = body.get("payload") or {}
            try:
                result = await asyncio.to_thread(edits.apply, action, payload)
            except edits.EditError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            BUS.publish("changed", action=action)
            return JSONResponse(result)

        # ----------------------------------------------------- talking

        @app.post("/api/say")
        async def say(request: Request) -> Any:
            body = await request.json()
            text = str(body.get("text") or "").strip()
            if not text:
                return JSONResponse({"error": "nothing said"}, status_code=400)
            if LIVE.say is None:
                return JSONResponse(
                    {"error": "Alfred is not running, so there is nobody "
                              "to say that to."}, status_code=409,
                )
            BUS.publish("you_said", text=text)
            try:
                # This runs a model and can take seconds. On the loop it
                # would stall every other window and the log stream with
                # it, so it goes to a thread.
                out = await asyncio.to_thread(LIVE.say, text)
                if asyncio.iscoroutine(out):
                    out = await out
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": str(exc)}, status_code=500)
            return JSONResponse({"ok": True})

        @app.post("/api/mic")
        async def mic(request: Request) -> Any:
            body = await request.json()
            want = bool(body.get("listening"))
            hook = LIVE.wake if want else LIVE.sleep
            if hook is None:
                return JSONResponse(
                    {"error": "The microphone belongs to a running Alfred."},
                    status_code=409,
                )
            try:
                out = hook()
                if asyncio.iscoroutine(out):
                    out = await out
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": str(exc)}, status_code=500)
            return JSONResponse({"ok": True, "listening": want})

        @app.get("/api/screen")
        async def screen() -> Any:
            if LIVE.screenshot is None:
                return JSONResponse(
                    {"error": "Alfred is not running, so it has no eyes."},
                    status_code=409,
                )
            try:
                shot = await asyncio.to_thread(LIVE.screenshot)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": str(exc)}, status_code=500)
            if isinstance(shot, (str, Path)) and Path(shot).exists():
                return FileResponse(str(shot), media_type="image/png")
            if isinstance(shot, (bytes, bytearray)):
                return Response(bytes(shot), media_type="image/png")
            return JSONResponse({"error": "no picture came back"}, status_code=500)

        @app.get("/api/windows")
        async def windows() -> Any:
            if LIVE.windows is None:
                return JSONResponse({"windows": []})
            try:
                found = await asyncio.to_thread(LIVE.windows)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"error": str(exc)}, status_code=500)
            return JSONResponse({"windows": found})

        # ----------------------------------------------------- the feed

        @app.websocket("/ws")
        async def feed(socket: WebSocket) -> None:
            if not allowed(socket):  # type: ignore[arg-type]
                await socket.close(code=1008)
                return
            await socket.accept()
            queue = BUS.subscribe()
            try:
                await socket.send_text(json.dumps({
                    "kind": "hello",
                    "running": LIVE.running,
                    "abilities": LIVE.abilities(),
                    "history": BUS.history(120),
                }))
                while True:
                    event = await queue.get()
                    await socket.send_text(json.dumps(event, default=str))
            except WebSocketDisconnect:
                pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                BUS.unsubscribe(queue)

        return app

    # ----------------------------------------------------------- running

    def start(self) -> str:
        """Bring the server up on its own thread. Returns the URL."""
        if self.running:
            return self.url

        import uvicorn

        app = self.build()
        config = uvicorn.Config(
            app, host=HOST, port=self.port, log_level="warning",
            access_log=False, ws_ping_interval=20.0,
        )
        self._server = uvicorn.Server(config)

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            BUS.bind(loop)
            try:
                loop.run_until_complete(self._server.serve())
            except Exception as exc:  # noqa: BLE001
                print(f"[UI] server stopped: {exc}")
            finally:
                try:
                    loop.close()
                except Exception:  # noqa: BLE001
                    pass

        self._thread = threading.Thread(
            target=run, name="alfred-interface", daemon=True
        )
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


INTERFACE = Interface()
