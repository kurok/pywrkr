"""Target URLs are logged without credentials or query strings (#309)."""

from __future__ import annotations

import logging

import pytest

from pywrkr.config import loggable_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://h.example/a/b?token=SECRET&y=1", "https://h.example/a/b?<redacted>"),
        ("https://user:pass@h.example:8443/a", "https://h.example:8443/a"),
        ("http://h.example/", "http://h.example/"),
        ("http://[::1]:9000/x?k=v", "http://[::1]:9000/x?<redacted>"),
    ],
)
def test_loggable_url(url, expected):
    assert loggable_url(url) == expected


def test_multi_url_endpoint_log_line_redacts_the_query(caplog, monkeypatch):
    import asyncio

    from pywrkr import multi_url
    from pywrkr.config import BenchmarkConfig, WorkerStats

    async def fake_run_benchmark(config):
        return WorkerStats()

    monkeypatch.setattr(multi_url, "run_benchmark", fake_run_benchmark)
    monkeypatch.setattr(multi_url, "print_results", lambda *a, **k: None, raising=False)
    entry = multi_url.UrlEntry(url="https://api.example/v1?api_key=SECRET", method="GET")
    with caplog.at_level(logging.INFO, logger="pywrkr.multi_url"):
        try:
            asyncio.run(
                multi_url.run_multi_url(
                    [entry], BenchmarkConfig(url=entry.url, duration=0.01, connections=1)
                )
            )
        except Exception:
            pass  # only the log line is under test
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET" not in text
    assert "https://api.example/v1?<redacted>" in text
