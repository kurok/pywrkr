"""Exports against a receiver that 404s like a real one (#320).

The OTel cases live in ``tests/test_pywrkr.py::TestOtelExport``, the class the
CI ``test-otel`` job runs with the SDK installed. These need no optional
dependency: the receiver contract itself, and the Prometheus Pushgateway push.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from pywrkr.reporting import export_to_prometheus
from tests.collector_fixture import CollectorReceiver

RESULTS = {
    "total_requests": 100,
    "total_errors": 1,
    "requests_per_sec": 50.0,
    "duration_sec": 2.0,
    "latency": {"mean": 0.01, "max": 0.05},
    "percentiles": {"p50": 0.01, "p95": 0.03, "p99": 0.04},
}


@pytest.fixture
def receiver():
    with CollectorReceiver() as r:
        yield r


@pytest.fixture
def refusing_receiver():
    with CollectorReceiver(refuse=True) as r:
        yield r


def _post(url: str) -> int:
    req = urllib.request.Request(url, data=b"x", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 - local fixture
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_receiver_serves_only_the_signal_paths(receiver):
    """The fixture's contract: a permissive receiver is what hid #318."""
    assert _post(f"{receiver.url}/v1/metrics") == 200
    assert _post(f"{receiver.url}/metrics/job/pywrkr") == 200
    for path in ("/", "/v1/traces/x", "/metrics", "/api/v1/write", "/v1/metrics/extra"):
        assert _post(f"{receiver.url}{path}") == 404, path


def test_prometheus_export_reaches_its_receiver(receiver):
    assert export_to_prometheus(RESULTS, receiver.url, {"env": "test"}) is True
    pushed = receiver.received("/metrics/job/pywrkr", status=200)
    assert len(pushed) == 1
    text = pushed[0].body.decode()
    assert "pywrkr_requests_total" in text
    assert 'env="test"' in text


def test_prometheus_export_reports_failure_when_the_receiver_404s(refusing_receiver, caplog):
    with caplog.at_level("ERROR", logger="pywrkr.reporting"):
        assert export_to_prometheus(RESULTS, refusing_receiver.url, {}) is False
    assert refusing_receiver.received("/metrics/job/pywrkr", status=404)
    assert "Prometheus export failed" in caplog.text
