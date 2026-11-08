"""Keep the shipped sample outputs in examples/ in step with the tool (#329).

The artifacts are regenerated with ``python examples/regenerate.py``; these
tests fail when the JSON results or the terminal report's sections change
without that being re-run.
"""

from __future__ import annotations

import json
import re
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.collector_fixture import CollectorReceiver

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
RUN_NAMES = [
    "basic-benchmark",
    "rate-limited",
    "traffic-profile-sine",
    "user-simulation",
    "latency-breakdown",
    "threshold-check",
]
# Keys a mode adds on top of a plain run's.
MODE_KEYS = {"rate_limit_waits", "target_rps", "traffic_profile", "latency_breakdown"}


def _sections(text: str) -> list[str]:
    """The report's section titles: the lines right after a ``====`` rule."""
    lines = text.splitlines()
    return [
        line.strip()
        for prev, line in zip(lines, lines[1:])
        if re.fullmatch(r"=+", prev) and re.match(r"  [A-Z]", line)
    ]


@pytest.fixture(scope="module")
def live_run(tmp_path_factory):
    """A short plain run against a local server: (terminal text, JSON results)."""
    from pywrkr.main import main

    out = tmp_path_factory.mktemp("live") / "run.json"
    with CollectorReceiver() as server:
        argv = ["pywrkr", "-c", "2", "-d", "1", "--json", str(out), f"{server.url}/"]
        stdout = StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch("sys.stdout", stdout),
            patch("sys.stderr", new_callable=StringIO),
            pytest.raises(SystemExit) as exc,
        ):
            main()
    assert exc.value.code == 0
    return stdout.getvalue(), json.loads(out.read_text())


def test_example_json_has_the_current_schema(live_run):
    _, results = live_run
    current = set(results)
    basic = set(json.loads((EXAMPLES / "basic-benchmark.json").read_text()))
    assert basic == current, "regenerate examples/ with python examples/regenerate.py"
    for name in RUN_NAMES:
        keys = set(json.loads((EXAMPLES / f"{name}.json").read_text()))
        assert current <= keys, f"{name}.json lacks {sorted(current - keys)}"
        assert keys - current <= MODE_KEYS, f"{name}.json has {sorted(keys - current - MODE_KEYS)}"


def test_example_outputs_match_the_current_report(live_run):
    text, _ = live_run
    shipped = (EXAMPLES / "basic-benchmark-output.txt").read_text()
    assert _sections(shipped) == _sections(text)
    for name in RUN_NAMES:
        out = (EXAMPLES / f"{name}-output.txt").read_text()
        # The preamble went to logging.info, which the CLI does not print.
        assert "Running benchmark" not in out and "Running user simulation" not in out, name
        assert "BENCHMARK RESULTS" in _sections(out), name
