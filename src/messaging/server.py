"""The little HTTP server WhatsApp posts messages to.

It listens on localhost only. Nothing is opened on the router: a tunnel
(Cloudflare's is free) makes the outbound connection and forwards to
this port, so the machine is never directly addressable from outside.

It answers two things and nothing else. Anything else gets a 404 -
there is no reason for this process to serve anybody a page.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

_MAX_BODY = 512 * 1024      # a webhook payload is small; anything huge is not


class _Handler(BaseHTTPRequestHandler):
    channel: Any = None
    path_prefix: str = "/webhook"

    # The default logs every request to stderr, which drowns Alfred's
    # own output.
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _plain(self, code: int, body: str = "") -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith(self.path_prefix):
            self._plain(404)
            return

        query = parse_qs(parsed.query)
        challenge = self.channel.verify_subscription(
            (query.get("hub.mode") or [""])[0],
            (query.get("hub.verify_token") or [""])[0],
            (query.get("hub.challenge") or [""])[0],
        )

        if challenge is None:
            print("[Webhook] refused a subscription check")
            self._plain(403)
            return

        print("[Webhook] subscription verified")
        self._plain(200, challenge)

    def do_POST(self) -> None:
        if not urlparse(self.path).path.startswith(self.path_prefix):
            self._plain(404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0

        if length <= 0 or length > _MAX_BODY:
            self._plain(400)
            return

        body = self.rfile.read(length)
        signature = self.headers.get("X-Hub-Signature-256", "")

        # Always 200: Meta retries anything else, and a message that was
        # refused should not be delivered again and again.
        self._plain(200, "ok")

        try:
            self.channel.deliver(body, signature)
        except Exception as exc:  # noqa: BLE001
            print(f"[Webhook] handling failed: {exc}")


class WebhookServer:
    """Runs the webhook in the background."""

    def __init__(self, channel: Any, port: int = 8770,
                 path: str = "/webhook") -> None:
        self._channel = channel
        self._port = int(port)
        self._path = path or "/webhook"
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> bool:
        handler = type(
            "AlfredWebhookHandler",
            (_Handler,),
            {"channel": self._channel, "path_prefix": self._path},
        )

        try:
            # Localhost only. The tunnel reaches it from inside the
            # machine; nothing else can.
            self._httpd = ThreadingHTTPServer(("127.0.0.1", self._port),
                                              handler)
        except OSError as exc:
            print(f"[Webhook] could not listen on {self._port}: {exc}")
            return False

        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="alfred-webhook",
            daemon=True,
        )
        self._thread.start()
        print(f"[Webhook] listening on 127.0.0.1:{self._port}{self._path}")
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._httpd = None
