"""A collector-shaped receiver for the export tests (#320).

CONTRACT: it serves exactly the paths a real receiver serves and **404s every
other path** -- ``POST /v1/metrics`` (OTLP/HTTP) and ``POST /metrics/job/<job>``
(Prometheus Pushgateway), plus ``GET`` on any path so it can double as the
benchmark target in a CLI test. A receiver that accepted any path is what let
#318 (metrics posted to the collector's root) pass its tests; do not make this
one permissive. ``refuse=True`` answers 404 on the signal paths too, so the
failure case can be tested against the same fixture.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PUSHGATEWAY_PATH = re.compile(r"^/metrics/job/[^/]+(/.*)?$")
OTLP_METRICS_PATH = "/v1/metrics"


@dataclass
class ReceivedRequest:
    method: str
    path: str
    status: int
    content_type: str
    body: bytes


@dataclass
class CollectorReceiver:
    """Start with :meth:`start`, stop with :meth:`stop` (or use as a context manager)."""

    refuse: bool = False
    requests: list[ReceivedRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        assert self._server is not None, "receiver not started"
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def received(self, path_prefix: str, status: int | None = None) -> list[ReceivedRequest]:
        with self._lock:
            return [
                r
                for r in self.requests
                if r.method == "POST"
                and r.path.startswith(path_prefix)
                and (status is None or r.status == status)
            ]

    def start(self) -> "CollectorReceiver":
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self, status: int, body: bytes) -> None:
                with receiver._lock:
                    receiver.requests.append(
                        ReceivedRequest(
                            self.command,
                            self.path,
                            status,
                            self.headers.get("Content-Type", ""),
                            body,
                        )
                    )

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = self.path.split("?", 1)[0]
                signal = path == OTLP_METRICS_PATH or _PUSHGATEWAY_PATH.match(path)
                status = 200 if signal and not receiver.refuse else 404
                self._record(status, body)
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                self._record(200, b"")
                payload = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "CollectorReceiver":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
