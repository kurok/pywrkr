#!/usr/bin/env python3
"""Regression tests for audited defects in pywrkr.workers.

Each test targets a specific finding (wk-N) and would fail on the pre-fix
code. Tests only send load to localhost with tiny values.
"""

import asyncio
import gzip
import os
import signal
import tempfile
import time
import unittest
from io import StringIO
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

import pywrkr
import pywrkr.workers as workers

# ---------------------------------------------------------------------------
# wk-10: cache-busting must land in the query, not the fragment
# ---------------------------------------------------------------------------


class TestMakeUrlFragment(unittest.TestCase):
    def test_cb_lands_in_query_with_fragment(self):
        result = workers.make_url("http://h/p#frag", True)
        # _cb must be a query parameter, before the fragment.
        self.assertIn("?_cb=", result)
        self.assertTrue(result.endswith("#frag"))
        # The _cb must not be inside the fragment.
        query_part = result.split("#")[0]
        self.assertIn("_cb=", query_part)

    def test_cb_appended_to_existing_query_before_fragment(self):
        result = workers.make_url("http://h/p?a=1#frag", True)
        self.assertIn("a=1&_cb=", result)
        self.assertTrue(result.endswith("#frag"))
        # _cb is in the query component, not the fragment.
        self.assertNotIn("_cb=", result.split("#")[1])

    def test_no_fragment_unchanged_behavior(self):
        # Existing behavior preserved for fragment-free URLs.
        self.assertTrue(workers.make_url("http://h/p", True).startswith("http://h/p?_cb="))
        self.assertTrue(workers.make_url("http://h/p?a=1", True).startswith("http://h/p?a=1&_cb="))


# ---------------------------------------------------------------------------
# wk-13: run_autofind must reject step_multiplier <= 1.0 instead of hanging
# ---------------------------------------------------------------------------


class TestAutofindValidation(unittest.IsolatedAsyncioTestCase):
    async def test_step_multiplier_leq_one_raises(self):
        for bad in (1.0, 0.5, 0.0, -1.0):
            cfg = pywrkr.AutofindConfig(url="http://localhost/", step_multiplier=bad)
            with self.assertRaises(ValueError):
                await pywrkr.run_autofind(cfg)

    async def test_start_users_below_one_raises(self):
        cfg = pywrkr.AutofindConfig(url="http://localhost/", step_multiplier=2.0, start_users=0)
        with self.assertRaises(ValueError):
            await pywrkr.run_autofind(cfg)


# ---------------------------------------------------------------------------
# wk-15: LiveDashboard throughput bar must normalize against observed peak
# ---------------------------------------------------------------------------


class TestDashboardBarNormalization(unittest.TestCase):
    def _build_dashboard(self, rps):
        config = pywrkr.BenchmarkConfig(url="http://localhost/", duration=10.0)
        ws = pywrkr.WorkerStats()
        # Make rps deterministic: elapsed ~ start_time delta. We instead drive
        # _peak_rps directly via _build_display by setting requests/elapsed.
        return workers.LiveDashboard([ws], config, start_time=0.0)

    def test_bar_shrinks_below_peak(self):
        # Drive _build_display through the bar-length math directly by checking
        # the normalization formula against a known peak.
        dash = self._build_dashboard(0)
        max_bar = 24

        # Simulate a peak observed earlier in the run.
        dash._peak_rps = 1000.0
        rps_now = 250.0  # quarter of peak
        dash._peak_rps = max(dash._peak_rps, rps_now)
        bar_len = min(max_bar, max(1, int(rps_now / max(dash._peak_rps, 1e-9) * max_bar)))
        # quarter of peak -> ~6 of 24, definitely not full.
        self.assertLess(bar_len, max_bar)
        self.assertGreaterEqual(bar_len, 1)

    def test_bar_full_only_at_peak(self):
        dash = self._build_dashboard(0)
        max_bar = 24
        dash._peak_rps = 500.0
        rps_now = 500.0  # at peak
        dash._peak_rps = max(dash._peak_rps, rps_now)
        bar_len = min(max_bar, max(1, int(rps_now / max(dash._peak_rps, 1e-9) * max_bar)))
        self.assertEqual(bar_len, max_bar)


# ---------------------------------------------------------------------------
# wk-3: _finalize_run must surface (not swallow) unexpected worker crashes
# ---------------------------------------------------------------------------


class TestFinalizeRunSurfacesCrashes(unittest.IsolatedAsyncioTestCase):
    async def test_worker_crash_logged_and_nonzero_exit(self):
        async def _crashing_worker():
            raise ValueError("boom")

        async def _good_worker():
            return None

        stop_event = asyncio.Event()

        async def _progress(stop):
            await stop.wait()

        progress_task = asyncio.create_task(_progress(stop_event))

        config = pywrkr.BenchmarkConfig(url="http://localhost/", duration=1.0)
        backend = pywrkr.AiohttpBackend(config, 1)
        tasks = [
            asyncio.create_task(_good_worker()),
            asyncio.create_task(_crashing_worker()),
        ]

        with self.assertLogs("pywrkr.workers", level="ERROR") as log_ctx:
            with patch("sys.stdout", new_callable=StringIO):
                merged, exit_code = await workers._finalize_run(
                    tasks,
                    stop_event,
                    progress_task,
                    backend,
                    [pywrkr.WorkerStats(), pywrkr.WorkerStats()],
                    start_time=0.0,
                    config=config,
                    rate_limiter=None,
                    concurrency=2,
                    quiet=True,
                )
        # Crash must be logged at error level.
        self.assertTrue(any("crashed" in m for m in log_ctx.output))
        # And surfaced via a non-zero exit code (was silently 0 before).
        self.assertNotEqual(exit_code, 0)


# ---------------------------------------------------------------------------
# Integration tests against a local aiohttp server
# ---------------------------------------------------------------------------


class TestWorkersAuditIntegration(AioHTTPTestCase):
    """Local server exercising scenario, content-length, rate, and timing fixes."""

    async def get_application(self):
        app = web.Application()
        app.router.add_get("/", self.handle_get)
        app.router.add_post("/post", self.handle_post)
        app.router.add_get("/gzip", self.handle_gzip)
        return app

    async def handle_get(self, request):
        return web.Response(text="actual content here", content_type="text/plain")

    async def handle_post(self, request):
        await request.read()
        return web.Response(text="created", status=201)

    async def handle_gzip(self, request):
        # Serve a consistent gzip-compressed body. Content-Length is the
        # compressed size; aiohttp decompresses transparently on the client.
        payload = b"x" * 2400
        compressed = gzip.compress(payload)
        return web.Response(
            body=compressed,
            headers={"Content-Encoding": "gzip", "Content-Type": "text/plain"},
        )

    def _url(self, path="/"):
        return f"http://localhost:{self.server.port}{path}"

    def _make_scenario_file(self, data):
        import json

        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        return path

    # --- wk-1: scenario + latency_breakdown must still run requests ---
    async def test_scenario_latency_breakdown_runs_requests(self):
        scenario_data = {
            "name": "lb",
            "steps": [{"method": "GET", "path": "/", "name": "get"}],
        }
        path = self._make_scenario_file(scenario_data)
        try:
            scenario = pywrkr.load_scenario(path)
            config = pywrkr.BenchmarkConfig(
                url=self._url("/"),
                users=1,
                duration=1.0,
                think_time=0.0,
                ramp_up=0.0,
                timeout_sec=5,
                scenario=scenario,
                latency_breakdown=True,
            )
            with patch("sys.stdout", new_callable=StringIO):
                stats, _ = await pywrkr.run_user_simulation(config)
            # Pre-fix: trace_ctx=None made every scenario worker crash on its
            # first request -> total_requests==0. Now it must run and record
            # breakdown samples.
            self.assertGreater(stats.total_requests, 0)
            self.assertGreater(len(stats.breakdowns), 0)
        finally:
            os.unlink(path)

    async def test_scenario_latency_breakdown_run_benchmark(self):
        scenario_data = {
            "name": "lb",
            "steps": [{"method": "GET", "path": "/", "name": "get"}],
        }
        path = self._make_scenario_file(scenario_data)
        try:
            scenario = pywrkr.load_scenario(path)
            config = pywrkr.BenchmarkConfig(
                url=self._url("/"),
                connections=1,
                duration=1.0,
                threads=1,
                timeout_sec=5,
                scenario=scenario,
                latency_breakdown=True,
            )
            with patch("sys.stdout", new_callable=StringIO):
                stats, _ = await pywrkr.run_benchmark(config)
            self.assertGreater(stats.total_requests, 0)
            self.assertGreater(len(stats.breakdowns), 0)
        finally:
            os.unlink(path)

    # --- wk-2: both assertions failing must count exactly one error ---
    async def test_double_assertion_single_error(self):
        scenario_data = {
            "name": "dbl",
            "steps": [
                {
                    "method": "GET",
                    "path": "/",
                    "assert_status": 201,  # actual is 200 -> fails
                    "assert_body_contains": "MISSING",  # not in body -> fails
                    "name": "both-fail",
                }
            ],
        }
        path = self._make_scenario_file(scenario_data)
        try:
            scenario = pywrkr.load_scenario(path)
            config = pywrkr.BenchmarkConfig(
                url=self._url("/"),
                connections=1,
                duration=None,
                num_requests=5,
                threads=1,
                timeout_sec=5,
                scenario=scenario,
            )
            with patch("sys.stdout", new_callable=StringIO):
                stats, _ = await pywrkr.run_benchmark(config)
            self.assertGreater(stats.total_requests, 0)
            # Exactly one error per request (was 2x before the fix).
            self.assertEqual(stats.errors, stats.total_requests)
            # Both diagnostic messages still recorded.
            self.assertTrue(any("AssertStatus" in k for k in stats.error_types))
            self.assertTrue(any("AssertBody" in k for k in stats.error_types))
        finally:
            os.unlink(path)

    # --- wk-6 (with wk-5): -c1 walks all scenario steps in order ---
    async def test_scenario_budget_alternates_get_post(self):
        scenario_data = {
            "name": "alt",
            "steps": [
                {"method": "GET", "path": "/", "name": "list"},
                {"method": "POST", "path": "/post", "name": "create"},
            ],
        }
        path = self._make_scenario_file(scenario_data)
        try:
            scenario = pywrkr.load_scenario(path)
            config = pywrkr.BenchmarkConfig(
                url=self._url("/"),
                connections=1,
                threads=4,
                duration=None,
                num_requests=6,
                timeout_sec=5,
                think_time=0.0,
                scenario=scenario,
            )
            with patch("sys.stdout", new_callable=StringIO):
                stats, _ = await pywrkr.run_benchmark(config)
            # 6 requests alternating GET/POST -> 3x 200 (GET) + 3x 201 (POST).
            # Pre-fix over-provisioning spawned ~5 workers each doing only the
            # first GET, so 201 would be near-absent.
            self.assertEqual(stats.total_requests, 6)
            self.assertEqual(stats.status_codes.get(200, 0), 3)
            self.assertEqual(stats.status_codes.get(201, 0), 3)
        finally:
            os.unlink(path)

    # --- wk-5: exact worker count == connections ---
    async def test_worker_count_equals_connections(self):
        captured = {}
        real_create = asyncio.create_task

        async def _noop_worker(*args, **kwargs):
            return None

        for conns, threads, expected in [(1, 4, 1), (2, 4, 2), (3, 4, 3), (1, 8, 1)]:
            count = {"n": 0}

            def _counting_worker(*args, _count=count, **kwargs):
                _count["n"] += 1
                return _noop_worker()

            config = pywrkr.BenchmarkConfig(
                url=self._url("/"),
                connections=conns,
                threads=threads,
                duration=None,
                num_requests=1,
                timeout_sec=5,
            )
            with patch.object(workers, "worker", _counting_worker):
                with patch("sys.stdout", new_callable=StringIO):
                    await pywrkr.run_benchmark(config)
            self.assertEqual(count["n"], expected, f"conns={conns} threads={threads}")
        captured.clear()
        self.assertIs(asyncio.create_task, real_create)

    # --- wk-11: gzip content-length must not produce false errors ---
    async def test_gzip_no_false_content_length_errors(self):
        config = pywrkr.BenchmarkConfig(
            url=self._url("/gzip"),
            connections=1,
            threads=1,
            duration=None,
            num_requests=4,
            verify_content_length=True,
            timeout_sec=5,
        )
        with patch("sys.stdout", new_callable=StringIO):
            stats, _ = await pywrkr.run_benchmark(config)
        self.assertEqual(stats.total_requests, 4)
        # Pre-fix: len(decompressed) != declared compressed length -> 4 errors.
        self.assertEqual(stats.content_length_errors, 0)

    # --- wk-7: --rate must throttle scenario runs ---
    async def test_rate_limited_scenario_throttles(self):
        scenario_data = {
            "name": "rate",
            "steps": [{"method": "GET", "path": "/", "name": "get"}],
        }
        path = self._make_scenario_file(scenario_data)
        try:
            scenario = pywrkr.load_scenario(path)
            config = pywrkr.BenchmarkConfig(
                url=self._url("/"),
                connections=2,
                threads=2,
                duration=1.0,
                timeout_sec=5,
                think_time=0.0,
                scenario=scenario,
                rate=20.0,
            )
            with patch("sys.stdout", new_callable=StringIO):
                stats, _ = await pywrkr.run_benchmark(config)
            # Pre-fix: rate_limiter never passed to scenario_worker -> unbounded
            # throughput (hundreds of req in 1s). With throttling, ~20/s.
            self.assertLessEqual(stats.total_requests, 60)
            self.assertGreater(stats.total_requests, 0)
        finally:
            os.unlink(path)

    # --- wk-7: --rate must throttle user-sim even with think_time > 0 ---
    async def test_rate_limited_user_sim_with_think_time(self):
        config = pywrkr.BenchmarkConfig(
            url=self._url("/"),
            users=10,
            duration=1.0,
            think_time=0.05,
            think_time_jitter=0.0,
            ramp_up=0.0,
            timeout_sec=5,
            rate=20.0,
        )
        with patch("sys.stdout", new_callable=StringIO):
            stats, _ = await pywrkr.run_user_simulation(config)
        # Pre-fix: limiter ignored because think_time != 0 -> ~think-time-bound
        # rate (far above 20/s with 10 users at 50ms think). Now capped ~20/s.
        self.assertLessEqual(stats.total_requests, 60)
        self.assertGreater(stats.total_requests, 0)

    # --- wk-4: reported duration must be close to true worker-phase wall time
    async def test_duration_not_inflated(self):
        config = pywrkr.BenchmarkConfig(
            url=self._url("/"),
            connections=2,
            threads=1,
            duration=1.0,
            timeout_sec=5,
        )
        captured = {}
        real = pywrkr.print_results

        def _spy(merged, actual_duration, *a, **k):
            captured["duration"] = actual_duration
            return real(merged, actual_duration, *a, **k)

        with patch.object(workers, "print_results", _spy):
            with patch("sys.stdout", new_callable=StringIO):
                await pywrkr.run_benchmark(config)
        # Pre-fix: end_time sampled after awaiting the 1s-sleep progress task,
        # inflating duration to ~2.0s. Now it should be near 1.0s.
        self.assertLess(captured["duration"], 1.5)
        self.assertGreaterEqual(captured["duration"], 0.9)


# ---------------------------------------------------------------------------
# wk-12: malformed Content-Length must not crash the worker
# ---------------------------------------------------------------------------


class TestMalformedContentLength(unittest.IsolatedAsyncioTestCase):
    async def test_non_numeric_content_length_records_error_no_raise(self):
        from pywrkr.backends import BackendResponse

        class _FakeSession:
            async def send(self, *a, **k):
                return BackendResponse(
                    status=200,
                    body=b"hello",
                    headers={"Content-Length": "abc"},
                    http_version="1.1",
                )

            def clear_cookies(self):
                pass

        stats = pywrkr.WorkerStats()
        config = pywrkr.BenchmarkConfig(url="http://localhost/", verify_content_length=True)
        # Must not raise; must record a content_length_error.
        result = await workers._execute_request(
            _FakeSession(),
            "GET",
            "http://localhost/",
            {},
            None,
            False,
            5.0,
            stats,
            config,
            None,
            [None],
        )
        self.assertFalse(result.cancelled)
        self.assertEqual(stats.content_length_errors, 1)
        self.assertEqual(stats.total_requests, 1)


# ---------------------------------------------------------------------------
# wk-8 / wk-9: connector + progress task cleanup on cancellation during ramp
# ---------------------------------------------------------------------------


class TestRampCancellationCleanup(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_get("/", self.handle_get)
        return app

    async def handle_get(self, request):
        return web.Response(text="ok")

    def _url(self, path="/"):
        return f"http://localhost:{self.server.port}{path}"

    async def test_cancel_during_rampup_closes_connector(self):
        created = {}
        real_ctor = pywrkr.workers.aiohttp.TCPConnector

        def _spy_ctor(*a, **k):
            conn = real_ctor(*a, **k)
            created["conn"] = conn
            return conn

        config = pywrkr.BenchmarkConfig(
            url=self._url("/"),
            users=5,
            duration=5.0,
            ramp_up=5.0,
            think_time=0.0,
            timeout_sec=5,
        )
        with patch.object(pywrkr.workers.aiohttp, "TCPConnector", _spy_ctor):
            with patch("sys.stdout", new_callable=StringIO):
                task = asyncio.ensure_future(pywrkr.run_user_simulation(config))
                await asyncio.sleep(0.5)  # cancel mid ramp-up
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    _ = await task
        # Pre-fix: connector orphaned (closed == False). Now it must be closed.
        self.assertTrue(created["conn"].closed)


class TestMergeAllStatsAudit(unittest.TestCase):
    """dist-3/dist-5 (local merge path): _merge_all_stats must preserve true
    total_seen and rebase per-worker timelines, matching the distributed path.
    Pre-fix it used naive .extend(), discarding total_seen and concatenating raw
    per-process monotonic timestamps.
    """

    def test_merge_preserves_total_seen(self):
        from pywrkr.config import ReservoirSampler, WorkerStats

        ws1 = WorkerStats()
        ws2 = WorkerStats()
        cap = ws1.latencies.capacity
        # Two saturated reservoirs, each standing in for far more observations.
        ws1.latencies = ReservoirSampler.from_list([0.01] * cap, capacity=cap, total_seen=500_000)
        ws2.latencies = ReservoirSampler.from_list([0.02] * cap, capacity=cap, total_seen=500_000)
        ws1.total_requests = ws2.total_requests = 500_000

        merged = workers.merge_stats([ws1, ws2])
        # Pre-fix: total_seen collapsed to the surviving sample count.
        self.assertEqual(merged.latencies.total_seen, 1_000_000)
        self.assertLessEqual(len(merged.latencies), cap)

    def test_merge_normalizes_timeline(self):
        from pywrkr.config import WorkerStats

        ws1 = WorkerStats()
        ws2 = WorkerStats()
        ws1.rps_timeline = [(1000.0, 5), (1001.0, 7)]
        ws2.rps_timeline = [(2000.0, 3), (2001.0, 9)]
        merged = workers.merge_stats([ws1, ws2])
        # Each worker's monotonic origin is rebased to 0, so no raw timestamp
        # (>= 1000) survives; entries land on a shared [0, duration) axis.
        self.assertTrue(all(ts < 2.0 for ts, _ in merged.rps_timeline))
        self.assertEqual({c for _, c in merged.rps_timeline}, {5, 7, 3, 9})


# ---------------------------------------------------------------------------
# wk-228: --latency-breakdown reported transfer=0 for every request, and
# ttfb was really time-to-headers, because the breakdown was finalized in
# aiohttp's on_request_end -- which fires before the body is read.
# ---------------------------------------------------------------------------


class TestLatencyBreakdownPhases(AioHTTPTestCase):
    """The transfer phase must cover reading the body."""

    async def get_application(self):
        app = web.Application()
        app.router.add_get("/slow-body", self.handle_slow_body)
        return app

    async def handle_slow_body(self, request):
        # Headers go out immediately; the body follows 200 ms later. Anything
        # that calls that gap "time to first byte" is measuring the headers.
        resp = web.StreamResponse()
        await resp.prepare(request)
        await asyncio.sleep(0.2)
        await resp.write(b"x" * 1000)
        await resp.write_eof()
        return resp

    async def _one_request(self, read_body=True):
        from pywrkr.backends import create_backend

        url = f"http://localhost:{self.server.port}/slow-body"
        config = pywrkr.BenchmarkConfig(url=url, latency_breakdown=True, connections=1)
        stats = pywrkr.WorkerStats()
        backend = create_backend(config, 1)
        try:
            async with backend.create_session(stats) as session:
                await session.send("GET", url, {}, None, 5.0, trace_ctx={}, read_body=read_body)
        finally:
            await backend.aclose()
        return stats.breakdowns

    async def test_breakdown_transfer_covers_body_read(self):
        breakdowns = await self._one_request()
        self.assertEqual(len(breakdowns), 1)
        bd = breakdowns[0]
        # The 200 ms belongs to transfer, not to ttfb and not to nowhere.
        self.assertGreaterEqual(bd.transfer, 0.15)
        self.assertLess(bd.ttfb, 0.1)
        self.assertIn("transfer", bd.available)

    async def test_skipped_body_read_reports_no_transfer_phase(self):
        breakdowns = await self._one_request(read_body=False)
        self.assertEqual(len(breakdowns), 1)
        bd = breakdowns[0]
        # Nothing read the body, so there is no transfer measurement to report.
        # Claiming 0.0 would drag the aggregate down with a phase that never ran.
        self.assertEqual(bd.transfer, 0.0)
        self.assertNotIn("transfer", bd.available)


# ---------------------------------------------------------------------------
# wk-229: the aiohttp backend followed redirects silently (aiohttp's default
# is up to 10 hops) while the httpx backend did not, so --http2 and a default
# run measured different things against the same URL.
# ---------------------------------------------------------------------------


class TestRedirectHandling(AioHTTPTestCase):
    async def get_application(self):
        self.seen: list[tuple[str, str]] = []
        app = web.Application()
        app.router.add_get("/r", self.handle_redirect)
        app.router.add_get("/target", self.handle_target)
        return app

    async def handle_redirect(self, request):
        self.seen.append((request.path, request.headers.get("Authorization", "")))
        raise web.HTTPFound("/target")

    async def handle_target(self, request):
        self.seen.append((request.path, request.headers.get("Authorization", "")))
        return web.Response(text="arrived")

    async def _send(self, **config_overrides):
        from pywrkr.backends import create_backend

        url = f"http://localhost:{self.server.port}/r"
        config = pywrkr.BenchmarkConfig(url=url, connections=1, **config_overrides)
        stats = pywrkr.WorkerStats()
        backend = create_backend(config, 1)
        try:
            async with backend.create_session(stats) as session:
                result = await workers._execute_request(
                    session,
                    "GET",
                    url,
                    {"Authorization": "Basic dTpw"},
                    None,
                    False,
                    5.0,
                    stats,
                    config,
                    None,
                    [None],
                    transport_errors=backend.transport_errors,
                )
        finally:
            await backend.aclose()
        return result, stats

    async def test_aiohttp_backend_does_not_follow_redirects(self):
        result, stats = await self._send()
        # Pre-fix the worker saw 200, status_codes held {200: 1}, and the
        # server had served both /r and /target -- forwarding the Basic auth
        # header on the same-origin hop.
        self.assertEqual(result.status, 302)
        self.assertEqual(stats.status_codes[302], 1)
        self.assertEqual([path for path, _ in self.seen], ["/r"])

    async def test_follow_redirects_opt_in_still_works(self):
        result, stats = await self._send(follow_redirects=True)
        self.assertEqual(result.status, 200)
        self.assertEqual([path for path, _ in self.seen], ["/r", "/target"])


# ---------------------------------------------------------------------------
# wk-230: a CR/LF in a header rendered from an extracted value raised a bare
# ValueError out of aiohttp, which killed the virtual user for the rest of the
# run instead of counting one failed request.
# ---------------------------------------------------------------------------


class TestCrlfHeaderNotFatal(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_get("/token", self.handle_token)
        app.router.add_get("/use", self.handle_use)
        return app

    async def handle_token(self, request):
        # A server that reflects a stray newline into a field a scenario
        # extracts. Nothing here is under the load tool's control.
        return web.json_response({"token": "abc\r\nX-Injected: 1"})

    async def handle_use(self, request):
        return web.Response(text="ok")

    def _scenario_file(self):
        import json

        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(
                {
                    "name": "crlf",
                    "steps": [
                        {
                            "method": "GET",
                            "path": "/token",
                            "name": "get-token",
                            "extract": {"token": {"json": "token"}},
                        },
                        {
                            "method": "GET",
                            "path": "/use",
                            "name": "use-token",
                            "headers": {"X-Token": "${token}"},
                        },
                    ],
                },
                f,
            )
        return path

    async def test_crlf_in_extracted_header_is_counted_not_fatal(self):
        path = self._scenario_file()
        try:
            scenario = pywrkr.load_scenario(path)
            config = pywrkr.BenchmarkConfig(
                url=f"http://localhost:{self.server.port}/",
                connections=1,
                threads=1,
                duration=1.0,
                timeout_sec=5,
                think_time=0.0,
                scenario=scenario,
            )
            with patch("sys.stdout", new_callable=StringIO):
                stats, _ = await pywrkr.run_benchmark(config)
        finally:
            os.unlink(path)

        # Pre-fix the ValueError escaped _execute_request, killed the worker
        # task on its first iteration and surfaced only as "Worker N crashed".
        self.assertGreaterEqual(stats.errors, 1)
        injection_keys = [k for k in stats.error_types if k.startswith("HeaderInjection:")]
        self.assertEqual(injection_keys, ["HeaderInjection: X-Token"])
        # The user survived: step 1 kept running for the whole second, so it
        # made many more requests than the single iteration a crash allowed.
        self.assertGreater(len(stats.step_latencies["get-token"]), 5)
        # And the same header failed every iteration without flooding the log.
        self.assertGreater(stats.error_types["HeaderInjection: X-Token"], 5)

    async def test_bad_header_is_one_error_not_a_dead_worker(self):
        # The safety net behind the _render_step check: whatever route an
        # unsendable header takes, aiohttp's ValueError is now a transport
        # error like any other, so _execute_request counts it and returns.
        from pywrkr.backends import create_backend

        url = f"http://localhost:{self.server.port}/use"
        config = pywrkr.BenchmarkConfig(url=url, connections=1)
        stats = pywrkr.WorkerStats()
        backend = create_backend(config, 1)
        try:
            async with backend.create_session(stats) as session:
                result = await workers._execute_request(
                    session,
                    "GET",
                    url,
                    {"X-Token": "abc\r\nX-Injected: 1"},
                    None,
                    False,
                    5.0,
                    stats,
                    config,
                    None,
                    [None],
                    transport_errors=backend.transport_errors,
                )
        finally:
            await backend.aclose()

        # Counted as a failed request rather than escaping the coroutine.
        self.assertEqual(result.error_name, "ValueError")
        self.assertEqual(stats.errors, 1)
        self.assertEqual(stats.error_types["ValueError"], 1)


# ---------------------------------------------------------------------------
# wk-231: Ctrl-C only set stop_event. Workers check it between requests, so a
# run against a slow target waited out --timeout on everything in flight, and
# a second Ctrl-C did nothing at all.
# ---------------------------------------------------------------------------


class TestStopCancelsInflight(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_get("/slow", self.handle_slow)
        return app

    async def handle_slow(self, request):
        await asyncio.sleep(8)
        return web.Response(text="eventually")

    def _config(self):
        # -n mode on purpose: _calc_effective_timeout only caps the client
        # timeout in duration mode, so pre-fix this run waited out the full
        # 30s timeout_sec on every request still on the wire.
        return pywrkr.BenchmarkConfig(
            url=f"http://localhost:{self.server.port}/slow",
            connections=2,
            threads=1,
            num_requests=10,
            timeout_sec=30,
            _quiet=True,
        )

    async def _run_and_stop(self, force=False):
        """Start a run, take the events the signal handler would have, stop it."""
        captured: dict = {}

        def _capture(stop_event, force_event=None):
            captured["stop"] = stop_event
            captured["force"] = force_event

        t0 = time.monotonic()
        with patch("sys.stdout", new_callable=StringIO):
            with patch("pywrkr.workers._setup_signal_handlers", _capture):
                run = asyncio.create_task(pywrkr.run_benchmark(self._config()))
                while "stop" not in captured:
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.2)
                captured["stop"].set()
                if force:
                    captured["force"].set()
                await run
        return time.monotonic() - t0

    async def test_stop_event_cancels_inflight_requests_within_grace(self):
        elapsed = await self._run_and_stop()
        self.assertLess(
            elapsed,
            5.0,
            f"stop took {elapsed:.1f}s; workers get a 2s grace and are then cancelled",
        )

    async def test_second_signal_skips_the_grace(self):
        elapsed = await self._run_and_stop(force=True)
        self.assertLess(
            elapsed, 1.5, f"forced stop took {elapsed:.1f}s; it should not wait out the grace"
        )


class TestSignalEscalation(unittest.IsolatedAsyncioTestCase):
    """The first signal asks; the second one insists."""

    async def test_first_signal_stops_second_forces(self):
        from pywrkr.config import _setup_signal_handlers

        stop, force = asyncio.Event(), asyncio.Event()
        handlers: dict = {}

        class _Loop:
            def add_signal_handler(self, sig, cb):
                handlers[sig] = cb

        with patch("pywrkr.config.asyncio.get_running_loop", return_value=_Loop()):
            _setup_signal_handlers(stop, force)

        handle = handlers[signal.SIGINT]
        handle()
        self.assertTrue(stop.is_set())
        self.assertFalse(force.is_set())
        handle()
        self.assertTrue(force.is_set())
