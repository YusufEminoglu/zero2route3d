"""Localhost HTTP server for serving the 02Route 3D WebGL Studio to the system browser."""
from __future__ import annotations

import contextlib
import functools
import http.server
import mimetypes
import socket
import socketserver
import threading
from pathlib import Path
from typing import Optional

# How often the event stream checks the route file, and how long it may stay
# silent before sending a keep-alive comment (proxies drop idle streams).
EVENT_POLL_S = 0.25
KEEPALIVE_S = 15.0


class QuietCorsHandler(http.server.SimpleHTTPRequestHandler):
    """Simple HTTP request handler for the viewer: no-cache headers and quiet logging."""

    def log_message(self, fmt: str, *args: object) -> None:
        return None

    def end_headers(self) -> None:
        # No CORS header: the viewer is served from this same origin. The old
        # "Access-Control-Allow-Origin: *" let any web page the user had open
        # read the current route from localhost.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/events":
            self._stream_route_events()
            return
        super().do_GET()

    def _stream_route_events(self) -> None:
        """Server-sent events: one "route" event whenever current_route.json changes.

        Replaces the viewer's 1.5 s polling of the whole JSON file: the viewer
        now fetches the route only when QGIS has written a new one.
        """
        route_file = Path(self.directory) / "data" / "current_route.json"
        stop: Optional[threading.Event] = getattr(self.server, "stop_event", None)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_mtime: Optional[int] = -1
        silent = 0.0
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while stop is None or not stop.is_set():
                try:
                    mtime: Optional[int] = route_file.stat().st_mtime_ns
                except OSError:
                    mtime = None
                if mtime != last_mtime:
                    last_mtime = mtime
                    self.wfile.write(f"event: route\ndata: {mtime or 0}\n\n".encode("ascii"))
                    self.wfile.flush()
                    silent = 0.0
                elif silent >= KEEPALIVE_S:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    silent = 0.0
                if stop is not None:
                    stop.wait(EVENT_POLL_S)
                else:  # pragma: no cover - the server always sets stop_event
                    threading.Event().wait(EVENT_POLL_S)
                silent += EVENT_POLL_S
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # the browser tab closed


class ReusableTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs) -> None:
        # Open event streams watch this to end when the server stops.
        self.stop_event = threading.Event()
        super().__init__(*args, **kwargs)


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


class Route3DLocalServer:
    """Lightweight 127.0.0.1 HTTP server for serving WebGL ES-modules without file:// CORS blocks."""

    def __init__(self, web_root: Path, host: str = "127.0.0.1", start_port: int = 8920, end_port: int = 8950) -> None:
        self.web_root = Path(web_root).resolve()
        if not self.web_root.is_dir():
            raise FileNotFoundError(f"WebGL assets directory does not exist: {self.web_root}")
        self.host = host
        self.start_port = max(0, min(65535, int(start_port)))
        self.end_port = max(self.start_port, min(65535, int(end_port)))
        self.port: Optional[int] = None
        self._httpd: Optional[ReusableTcpServer] = None
        self._thread: Optional[threading.Thread] = None

        mimetypes.add_type("application/javascript", ".js")
        mimetypes.add_type("application/json", ".geojson")
        mimetypes.add_type("text/css", ".css")
        mimetypes.add_type("text/html", ".html")

    @property
    def is_running(self) -> bool:
        return self._httpd is not None and self._thread is not None and self._thread.is_alive()

    @property
    def url(self) -> str:
        if self.port is None:
            return ""
        return f"http://{self.host}:{self.port}/index.html"

    def start(self) -> str:
        if self.is_running:
            return self.url

        handler = functools.partial(QuietCorsHandler, directory=str(self.web_root))
        for port in range(self.start_port, self.end_port + 1):
            if _port_is_open(self.host, port):
                continue
            try:
                self._httpd = ReusableTcpServer((self.host, port), handler)
                self.port = port
                break
            except OSError:
                continue

        if self._httpd is None:
            # Fallback to random free port assigned by OS
            self._httpd = ReusableTcpServer((self.host, 0), handler)
            self.port = self._httpd.server_address[1]

        self._thread = threading.Thread(target=self._httpd.serve_forever, name="Route3DServer", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is None:
            return
        httpd = self._httpd
        thread = self._thread
        self._httpd = None
        self._thread = None
        self.port = None

        httpd.stop_event.set()
        with contextlib.suppress(Exception):
            httpd.shutdown()
        with contextlib.suppress(Exception):
            httpd.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
