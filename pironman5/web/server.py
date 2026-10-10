"""HTTP and WebSocket server on the standard library. One thread for each connection."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import select
import socket
import socketserver
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ..core import Core
from ..logger import get_logger
from .api import ROUTES, ApiError

log = get_logger("web")

STATIC_DIR = Path(__file__).parent / "static"
ASSETS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
}
MAX_BODY = 65536
MAX_WS_FRAME = 4096  # clients send only control frames

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_WS_TEXT, _WS_CLOSE, _WS_PING, _WS_PONG = 0x1, 0x8, 0x9, 0xA


def ws_frame(opcode: int, payload: bytes) -> bytes:
    size = len(payload)
    if size < 126:
        return bytes([0x80 | opcode, size]) + payload
    if size < 65536:
        return struct.pack("!BBH", 0x80 | opcode, 126, size) + payload
    return struct.pack("!BBQ", 0x80 | opcode, 127, size) + payload


def _recv(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed by peer")
        data += chunk
    return data


def ws_read(sock: socket.socket) -> tuple[int, bytes]:
    """Read one client frame. Return its opcode and payload."""
    head, size = _recv(sock, 2)
    size &= 0x7F
    if size == 126:
        (size,) = struct.unpack("!H", _recv(sock, 2))
    if size > MAX_WS_FRAME or size == 127:
        raise ConnectionError("frame too large")
    mask = _recv(sock, 4)
    return head & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(_recv(sock, size)))


def ws_alive(sock: socket.socket) -> bool:
    """Answer the pending client frames. Return False after a close frame."""
    while select.select([sock], [], [], 0)[0]:
        opcode, payload = ws_read(sock)
        if opcode == _WS_CLOSE:
            sock.sendall(ws_frame(_WS_CLOSE, payload[:2]))
            return False
        if opcode == _WS_PING:
            sock.sendall(ws_frame(_WS_PONG, payload))
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "pironman5"
    sys_version = ""
    timeout = 30

    def do_GET(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s " + format, self.address_string(), *args)

    def _send(self, status: int, content_type: str, body: bytes, etag: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if etag else "no-store")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        self._send(status, "application/json", json.dumps(value).encode())

    def _body(self) -> Any:
        """The JSON request body, or None if there is none."""
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            self.close_connection = True
            raise ApiError(413, "body too large")
        try:
            return json.loads(self.rfile.read(size)) if size else None
        except ValueError as exc:
            raise ApiError(422, "body must be JSON") from exc

    def _dispatch(self) -> None:
        url = urlsplit(self.path)
        try:
            body = self._body()
            # A browser sends Origin on cross-site requests. Other sites must not control the case.
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                raise ApiError(403, "cross-origin request")
            if (self.command, url.path) in ROUTES:
                handler = ROUTES[self.command, url.path]
                self._json(200, handler(self.server.core, dict(parse_qsl(url.query)), body))
            elif url.path == "/api/v1/stream":
                self._stream()
            else:
                self._static(url.path)
        except ApiError as exc:
            self._json(exc.status, {"detail": exc.detail})

    def _static(self, path: str) -> None:
        file = STATIC_DIR / ("index.html" if path == "/" else path.removeprefix("/static/"))
        if (
            self.command != "GET"
            or file.parent != STATIC_DIR
            or file.suffix not in ASSETS
            or not file.is_file()
        ):
            raise ApiError(404, "Not Found")
        stat = file.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self._send(304, ASSETS[file.suffix], b"", etag)
        else:
            self._send(200, ASSETS[file.suffix], file.read_bytes(), etag)

    def _stream(self) -> None:
        """Upgrade to WebSocket and push each metrics frame."""
        key = self.headers.get("Sec-WebSocket-Key")
        if self.command != "GET" or self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            raise ApiError(400, "WebSocket upgrade required")
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True

        core: Core = self.server.core
        sock = self.connection
        frames = core.hub.frames()
        try:
            # An immediate frame, so the page shows data before the next tick.
            sock.sendall(ws_frame(_WS_TEXT, json.dumps(core.status_frame()).encode()))
            for data in frames:
                if not ws_alive(sock):
                    break
                sock.sendall(ws_frame(_WS_TEXT, data))
        except OSError:
            log.debug("stream client gone", exc_info=True)
        finally:
            frames.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, core: Core, host: str, port: int) -> None:
        super().__init__((host, port), Handler)
        self.core = core
        self._thread = threading.Thread(target=self.serve_forever, name="web", daemon=True)

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)  # HTTPServer adds a slow reverse DNS lookup

    def start(self) -> None:
        self._thread.start()
        log.info("web UI on http://%s:%d", *self.server_address[:2])

    def stop(self) -> None:
        self.shutdown()
        self.server_close()

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        level = logging.DEBUG if isinstance(exc, OSError) else logging.ERROR
        log.log(level, "request from %s failed", client_address, exc_info=exc)
