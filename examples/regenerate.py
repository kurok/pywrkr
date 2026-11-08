"""Regenerate the sample outputs in this directory.

Runs the commands from ``examples/README.md`` (sections 1-6) against a local
test server, so the shipped ``*-output.txt``, ``*.json``, ``percentiles.csv``
and ``report.html`` are what the current pywrkr prints and writes. Run it from
the repository root after a change to the terminal report or the JSON results:

    uv run python examples/regenerate.py

The server answers every request with a small HTML page after 5-30 ms, so the
numbers describe a loopback server, not ``https://example.com/``.
``tests/test_examples.py`` fails when the JSON schema or the report's sections
drift from these files.
"""

from __future__ import annotations

import asyncio
import random
import subprocess
import sys
import threading
from pathlib import Path

from aiohttp import web

HOST = "127.0.0.1"
PORT = 8080
TARGET = f"http://{HOST}:{PORT}/"
EXAMPLES = Path("examples")

PAGE = (
    "<!doctype html><html><head><title>Example</title></head><body>" + "x" * 512 + "</body></html>"
)

# (output name, arguments before the target), as in examples/README.md.
RUNS: list[tuple[str, list[str]]] = [
    (
        "basic-benchmark",
        [
            "-c", "10", "-d", "5",
            "--json", "examples/basic-benchmark.json",
            "-e", "examples/percentiles.csv",
            "--html-report", "examples/report.html",
        ],
    ),
    ("rate-limited", ["--rate", "50", "-d", "5", "--json", "examples/rate-limited.json"]),
    (
        "traffic-profile-sine",
        [
            "--rate", "50", "-d", "5",
            "--traffic-profile", "sine:cycles=2,min=0.2",
            "--json", "examples/traffic-profile-sine.json",
        ],
    ),
    (
        "user-simulation",
        [
            "-u", "5", "-d", "5",
            "--think-time", "0.5", "--ramp-up", "2",
            "--json", "examples/user-simulation.json",
        ],
    ),
    (
        "latency-breakdown",
        ["-c", "5", "-d", "5", "--latency-breakdown", "--json", "examples/latency-breakdown.json"],
    ),
    (
        "threshold-check",
        [
            "-c", "5", "-d", "5",
            "--threshold", "p95 < 500ms",
            "--threshold", "error_rate < 5%",
            "--json", "examples/threshold-check.json",
        ],
    ),
]  # fmt: skip


async def _page(_request: web.Request) -> web.Response:
    await asyncio.sleep(random.uniform(0.005, 0.030))
    return web.Response(text=PAGE, content_type="text/html")


def _serve(ready: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _page)
    runner = web.AppRunner(app, access_log=None)
    loop.run_until_complete(runner.setup())
    loop.run_until_complete(web.TCPSite(runner, HOST, PORT).start())
    ready.set()
    loop.run_forever()


def _tidy(path: Path) -> None:
    """Strip trailing whitespace and end with one newline, as the pre-commit hooks require."""
    text = path.read_text()
    lines = [line.rstrip() for line in text.replace("\r", "\n").splitlines()]
    path.write_text("\n".join(lines).strip("\n") + "\n")


def main() -> int:
    if not (EXAMPLES / "README.md").is_file():
        print("run this from the repository root", file=sys.stderr)
        return 1
    ready = threading.Event()
    threading.Thread(target=_serve, args=(ready,), daemon=True).start()
    if not ready.wait(10):
        print(f"the test server did not start on {TARGET}", file=sys.stderr)
        return 1
    for name, args in RUNS:
        cmd = [sys.executable, "-m", "pywrkr", *args, TARGET]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        (EXAMPLES / f"{name}-output.txt").write_text(proc.stdout + proc.stderr)
        print(f"{name}: exit {proc.returncode}")
        if proc.returncode != 0:
            return proc.returncode
        written = [a for a in args if a.startswith("examples/")]
        for path in [EXAMPLES / f"{name}-output.txt", *map(Path, written)]:
            _tidy(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
