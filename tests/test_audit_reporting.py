"""Regression tests for audited reporting.py defects (rep-1, 2, 4, 5, 6, 7, 9).

Each test targets one confirmed defect and would FAIL on the pre-fix code.
"""

import io
import json
import math
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pywrkr.config import WorkerStats
from pywrkr.reporting import (
    _EXPORT_METRICS,
    _resolve_metric_value,
    build_results_dict,
    compute_percentiles,
    export_to_prometheus,
    print_latency_histogram,
    print_rps_timeline,
    write_json_output,
)


def _make_stats(latencies, total=None, errors=0):
    stats = WorkerStats()
    stats.latencies = list(latencies)
    stats.total_requests = total if total is not None else len(stats.latencies)
    stats.errors = errors
    return stats


class TestRep1PrometheusLabelEscaping(unittest.TestCase):
    """rep-1: Prometheus tag/label values must be escaped (no injection)."""

    def _capture_body(self, tags):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = req.data.decode("utf-8")
            return MagicMock()

        results = build_results_dict(_make_stats([0.01, 0.02, 0.03]), 10.0, 4)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            export_to_prometheus(results, "http://gw:9091", tags)
        return captured["body"]

    def test_quote_and_newline_escaped_no_injection(self):
        body = self._capture_body({"env": 'a"b', "x": "c\nd"})
        non_comment = [line for line in body.splitlines() if line and not line.startswith("#")]
        # Each metric must appear exactly once; no injected extra series.
        self.assertEqual(len(non_comment), len(_EXPORT_METRICS))
        # Quote and newline are escaped per the exposition format.
        self.assertIn(r'env="a\"b"', body)
        self.assertIn(r'x="c\nd"', body)
        # A raw newline must never appear inside a label string.
        for line in non_comment:
            self.assertNotIn("injected", line)

    def test_backslash_escaped_first(self):
        body = self._capture_body({"path": "a\\b"})
        self.assertIn(r'path="a\\b"', body)

    def test_invalid_label_name_dropped(self):
        # A key that is not a valid Prometheus label name must be dropped,
        # not interpolated raw (which would inject label-list syntax).
        body = self._capture_body({'bad" }injected{y="1': "v", "good": "ok"})
        self.assertIn('good="ok"', body)
        self.assertNotIn("injected", body)


class TestRep2ConsistentMedian(unittest.TestCase):
    """rep-2: latency.median must equal nearest-rank percentiles.p50."""

    def test_even_length_median_matches_p50(self):
        lats = [0.01 * i for i in range(1, 7)]  # even n; median!=nearest-rank
        result = build_results_dict(_make_stats(lats), 10.0, 4)
        self.assertEqual(result["latency"]["median"], result["percentiles"]["p50"])
        # statistics.median would have given 0.035; nearest-rank p50 is 0.03.
        self.assertEqual(result["latency"]["median"], 0.03)


class TestRep4NonFiniteLatency(unittest.TestCase):
    """rep-4: inf/NaN latencies must not crash or poison stats."""

    def test_histogram_does_not_crash_on_inf_nan(self):
        buf = io.StringIO()
        # Pre-fix: int(nan)/inf width raised ValueError.
        print_latency_histogram([0.1, 0.2, 0.3, float("inf"), float("nan")], file=buf)
        # No exception; finite-only bars rendered.
        self.assertIn("histogram", buf.getvalue().lower())

    def test_build_results_dict_finite_summary(self):
        lats = [0.1, 0.2, float("nan"), 0.3, 0.4, float("inf")]
        result = build_results_dict(_make_stats(lats), 10.0, 4)
        for key in ("min", "max", "mean", "median", "stdev"):
            self.assertTrue(math.isfinite(result["latency"][key]))
        for v in result["percentiles"].values():
            self.assertTrue(math.isfinite(v))
        # Max must be the largest finite value, not inf.
        self.assertEqual(result["latency"]["max"], 0.4)


class TestRep5RpsLastPartialBucket(unittest.TestCase):
    """rep-5: final partial RPS bucket divides by its real span."""

    def test_steady_rate_last_bucket(self):
        # 61s of steady 30 req/s; bucket_size = int(61/20) = 3.
        # Final bucket holds only the 61st second's 30 reqs.
        timeline = [(float(s), 30) for s in range(61)]
        buf = io.StringIO()
        print_rps_timeline(timeline, 0.0, 61.0, file=buf)
        lines = [ln for ln in buf.getvalue().splitlines() if "req/s" in ln and "s |" in ln]
        last = lines[-1]
        rps_val = float(last.rsplit("|", 1)[1].strip().split()[0])
        # Pre-fix: 30/3 = 10.0 (false dip). Now ~30.
        self.assertAlmostEqual(rps_val, 30.0, delta=1.0)


class TestRep6LatencyAbsentWhenNoRequests(unittest.TestCase):
    """rep-6: no fabricated 0 latency when zero requests collected."""

    def test_prometheus_omits_latency_lines(self):
        results = build_results_dict(WorkerStats(), 10.0, 4)
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = req.data.decode("utf-8")
            return MagicMock()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            export_to_prometheus(results, "http://gw:9091", {})
        body = captured["body"]
        for suffix in ("latency_p50_ms", "latency_p95_ms", "latency_mean_ms", "latency_max_ms"):
            self.assertNotIn("pywrkr_" + suffix, body)
        # Non-latency metrics still present.
        self.assertIn("pywrkr_requests_total", body)

    def test_resolve_returns_none_for_missing_latency(self):
        results = build_results_dict(WorkerStats(), 10.0, 4)
        self.assertIsNone(_resolve_metric_value(results, "percentiles", "p50", 1000))
        self.assertIsNone(_resolve_metric_value(results, "latency", "mean", 1000))

    def test_resolve_real_zero_still_emitted(self):
        # rep-6 must distinguish "no data" (None) from a real zero. A present
        # top-level key with value 0 must still resolve to 0, not None, so a
        # genuine zero (e.g. total_errors=0) is exported, not dropped.
        results = build_results_dict(WorkerStats(), 10.0, 4)
        self.assertEqual(results["total_requests"], 0)
        self.assertEqual(_resolve_metric_value(results, "total_requests", None, 1), 0)
        self.assertEqual(_resolve_metric_value(results, "total_errors", None, 1), 0)


class TestRep7JsonValid(unittest.TestCase):
    """rep-7: JSON export must not emit Infinity/NaN tokens."""

    def test_non_finite_raises_value_error(self):
        results = {"latency": {"min": float("inf"), "max": float("nan")}}
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with self.assertRaises(ValueError):
                write_json_output(path, results)
        finally:
            os.unlink(path)

    def test_finite_results_parse_strictly(self):
        results = build_results_dict(_make_stats([0.01, 0.02, 0.03]), 10.0, 4)
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            write_json_output(path, results)

            def _reject(_c):
                raise ValueError("non-finite token in JSON")

            with open(path) as f:
                json.load(f, parse_constant=_reject)  # must not raise
        finally:
            os.unlink(path)


class TestRep9TailPercentileResolution(unittest.TestCase):
    """rep-9: p99.9/p99.99 suppressed for samples too small to resolve them."""

    def test_small_sample_omits_high_tail(self):
        result = compute_percentiles([0.01 * i for i in range(1, 51)])  # n=50
        pcts = [p for p, _ in result]
        self.assertNotIn(99.9, pcts)
        self.assertNotIn(99.99, pcts)
        self.assertEqual(pcts, [50, 75, 90, 95, 99])

    def test_p99_9_appears_at_1000(self):
        result = compute_percentiles([0.001 * i for i in range(1, 1001)])  # n=1000
        pcts = [p for p, _ in result]
        self.assertIn(99.9, pcts)
        self.assertNotIn(99.99, pcts)

    def test_p99_99_appears_at_10000(self):
        result = compute_percentiles([0.0001 * i for i in range(1, 10001)])  # n=10000
        pcts = [p for p, _ in result]
        self.assertIn(99.9, pcts)
        self.assertIn(99.99, pcts)

    def test_build_results_dict_small_sample_keys(self):
        result = build_results_dict(_make_stats([0.01 * i for i in range(1, 11)]), 10.0, 4)
        self.assertNotIn("p99.9", result["percentiles"])
        self.assertNotIn("p99.99", result["percentiles"])


if __name__ == "__main__":
    unittest.main()
