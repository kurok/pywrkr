"""Target URLs are logged without credentials or query strings (#309)."""

from __future__ import annotations

import asyncio
import io
import logging

import pytest

from pywrkr.config import BenchmarkConfig, loggable_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://h.example/a/b?token=SECRET&y=1", "https://h.example/a/b?<redacted>"),
        ("https://user:pass@h.example:8443/a", "https://h.example:8443/a"),
        ("https://u:p@ss@h.example/", "https://h.example/"),
        ("http://h.example/", "http://h.example/"),
        ("http://[::1]:9000/x?k=v", "http://[::1]:9000/x?<redacted>"),
        ("https://h/p;jsessionid=SECRET#frag", "https://h/p;<redacted>#<redacted>"),
        ("user:SECRET@host/p?x=1", "host/p?<redacted>"),
        ("http://[::1/x", "<unparseable url>"),
    ],
)
def test_loggable_url(url, expected):
    assert loggable_url(url) == expected


def test_a_multi_url_run_never_logs_or_prints_the_query_string(caplog):
    """A real run against a closed port: the endpoint line, the workers'
    "Running benchmark" line, the 100%-errors line and the summary table
    all show the redacted URL."""
    from pywrkr.multi_url import UrlEntry, run_multi_url
    from pywrkr.reporting import print_multi_url_summary

    url = "http://127.0.0.1:9/v1?api_key=SECRET"
    config = BenchmarkConfig(url=url, connections=1, num_requests=1, timeout_sec=1.0)
    with caplog.at_level(logging.DEBUG, logger="pywrkr"):
        results = asyncio.run(run_multi_url([UrlEntry(url=url)], config))
    table = io.StringIO()
    print_multi_url_summary(results, file=table)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "http://127.0.0.1:9/v1?<redacted>" in logged
    assert "had 100% errors" in logged
    assert "SECRET" not in logged
    assert "SECRET" not in table.getvalue()


def test_url_host_drops_userinfo():
    from pywrkr.config import WorkerStats
    from pywrkr.reporting import build_results_dict

    config = BenchmarkConfig(url="https://user:pass@h.example/x")
    results = build_results_dict(WorkerStats(), 1.0, 0, config=config)
    assert results["config"]["url_host"] == "h.example"
