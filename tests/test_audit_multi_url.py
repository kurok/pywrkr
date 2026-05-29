"""Regression tests for audited defects in pywrkr.multi_url.

Covers:
- mu-3: a fully-failing endpoint (100% errors) must yield a non-zero exit_code
  in multi-URL mode even when no SLO thresholds are configured.
- mu-4: a bare HTTP-method line (no URL) is malformed and must raise ValueError.
- mu-5: per-URL config clone must deep-copy all mutable fields (tags,
  thresholds, ssl_config) so mutation cannot leak across endpoints.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from pywrkr.config import BenchmarkConfig, SSLConfig, Threshold
from pywrkr.multi_url import UrlEntry, load_url_file, run_multi_url


class TestMu3FullyFailingEndpointExitCode(unittest.TestCase):
    """mu-3: 100%-error endpoint must surface a non-zero exit_code."""

    def test_all_errors_yields_nonzero_exit_code(self):
        # "GET" is an invalid URL: every request raises InvalidUrlClientError,
        # so total_requests == errors with no real network traffic.
        base = BenchmarkConfig(
            url="placeholder",
            num_requests=2,
            connections=1,
            duration=None,
            _quiet=True,
        )
        entries = [UrlEntry(url="GET", method="GET")]
        results = asyncio.run(run_multi_url(entries, base))

        self.assertEqual(len(results), 1)
        r = results[0]
        # Sanity: the run was genuinely a total failure.
        self.assertGreater(r.stats.total_requests, 0)
        self.assertEqual(r.stats.errors, r.stats.total_requests)
        # The bug: this used to be 0. main.py uses max(r.exit_code) for the
        # overall process exit code, so a 0 here hid the failure from CI.
        self.assertNotEqual(r.exit_code, 0)

    def test_successful_endpoint_keeps_exit_code_zero(self):
        # Guard against over-eager escalation: a run with zero requests (and
        # thus zero errors) must not be marked as failed.
        base = BenchmarkConfig(
            url="placeholder",
            num_requests=0,
            connections=1,
            duration=None,
            _quiet=True,
        )
        entries = [UrlEntry(url="GET", method="GET")]
        results = asyncio.run(run_multi_url(entries, base))
        self.assertEqual(results[0].stats.total_requests, 0)
        self.assertEqual(results[0].exit_code, 0)


class TestMu4MalformedMethodOnlyLine(unittest.TestCase):
    """mu-4: a bare HTTP-method line with no URL must raise ValueError."""

    def _write_temp(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self.addCleanup(os.unlink, path)
        return path

    def test_method_only_line_raises(self):
        path = self._write_temp("POST\n")
        with self.assertRaises(ValueError):
            load_url_file(path)

    def test_method_only_line_case_insensitive_raises(self):
        path = self._write_temp("delete\n")
        with self.assertRaises(ValueError):
            load_url_file(path)

    def test_bare_url_without_method_still_accepted(self):
        # A single non-method token is a valid URL and must NOT raise
        # (this is the behavior the dead else-branch wrongly obscured).
        path = self._write_temp("http://example.com/test\n")
        entries = load_url_file(path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].url, "http://example.com/test")
        self.assertEqual(entries[0].method, "GET")


class TestMu5ConfigCloneDeepCopy(unittest.TestCase):
    """mu-5: clone must deep-copy tags/thresholds/ssl_config, not alias them."""

    def test_mutable_fields_are_isolated_per_endpoint(self):
        base = BenchmarkConfig(
            url="placeholder",
            num_requests=1,
            connections=1,
            duration=None,
            _quiet=True,
            headers={"X-Base": "1"},
            cookies=["a=b"],
            tags={"env": "staging"},
            thresholds=[Threshold("p95", "<", 0.3, "p95 < 300ms")],
            ssl_config=SSLConfig(),
        )

        captured: list[BenchmarkConfig] = []

        async def fake_runner(cfg):
            captured.append(cfg)
            from pywrkr.config import WorkerStats

            return WorkerStats(), 0

        with patch("pywrkr.multi_url.run_benchmark", side_effect=fake_runner):
            entries = [
                UrlEntry(url="http://127.0.0.1/a", method="GET"),
                UrlEntry(url="http://127.0.0.1/b", method="GET"),
            ]
            asyncio.run(run_multi_url(entries, base))

        self.assertEqual(len(captured), 2)
        c0, c1 = captured

        # Each clone must hold distinct objects, not aliases of base or each other.
        for field in ("headers", "cookies", "tags", "thresholds", "ssl_config"):
            self.assertIsNot(getattr(c0, field), getattr(base, field), field)
            self.assertIsNot(getattr(c0, field), getattr(c1, field), field)

        # Mutating one clone's mutable fields must not leak into base or siblings.
        c0.tags["leaked"] = "yes"
        c0.thresholds.append(Threshold("rps", ">", 1, "rps > 1"))
        c0.headers["X-Leak"] = "yes"
        c0.cookies.append("c=d")

        self.assertNotIn("leaked", base.tags)
        self.assertNotIn("leaked", c1.tags)
        self.assertEqual(len(base.thresholds), 1)
        self.assertEqual(len(c1.thresholds), 1)
        self.assertNotIn("X-Leak", base.headers)
        self.assertNotIn("X-Leak", c1.headers)
        self.assertNotIn("c=d", base.cookies)
        self.assertNotIn("c=d", c1.cookies)


class TestUnknownMethodAudit(unittest.TestCase):
    """mu-4: a 2-token line whose first token is an unknown method must be
    rejected, not silently parsed as a URL (dropping the real URL in parts[1])."""

    def _write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_unknown_method_with_url_raises(self):
        path = self._write("FOO http://example.com/x\n")
        with self.assertRaises(ValueError):
            load_url_file(path)

    def test_known_method_with_url_ok(self):
        path = self._write("POST http://example.com/x\n")
        entries = load_url_file(path)
        self.assertEqual(entries[0].method, "POST")
        self.assertEqual(entries[0].url, "http://example.com/x")


if __name__ == "__main__":
    unittest.main()
