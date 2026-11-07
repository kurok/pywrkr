"""Regression tests for confirmed defects in pywrkr.distributed.

Each test targets one finding id from the audit work order and is written to
FAIL on the pre-fix code and PASS after the fix.
"""

import asyncio
import contextlib
import json
import os
import socket
import time
import unittest
from io import StringIO
from unittest.mock import patch

import pywrkr
from pywrkr.config import DEFAULT_RESERVOIR_SIZE, ReservoirSampler
from pywrkr.config import normalize_timeline as _normalize_timeline
from pywrkr.distributed import (
    _deserialize_config,
    _recv_msg,
    _send_msg,
    _serialize_config,
    _serialize_stats,
    check_worker_target,
    is_loopback_bind,
    merge_worker_stats,
    run_master,
    run_worker_node,
)
from pywrkr.main import _build_parser, _parse_and_validate_args


async def _free_port() -> int:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port


def _frame(obj: dict) -> bytes:
    payload = json.dumps(obj).encode()
    return len(payload).to_bytes(4, "big") + payload


async def _bound_port(port_holder: list, timeout: float = 10.0) -> int:
    """Wait until the patched ``start_server`` has recorded the master's port.

    A fixed ``sleep(0.1)`` raced the bind. On a loaded runner the master was
    not listening yet, the fake worker connected to port 0, ``run_master``
    then waited for a peer that never arrived, and the test failed twenty
    seconds later as a bare ``asyncio.TimeoutError`` with nothing pointing at
    the cause.
    """
    deadline = time.monotonic() + timeout
    while not port_holder[0]:
        if time.monotonic() > deadline:
            raise AssertionError(f"master did not bind a port within {timeout}s")
        await asyncio.sleep(0.01)
    return port_holder[0]


class TestMasterSurvivesBadWorker(unittest.IsolatedAsyncioTestCase):
    """One node's malformed payload must not lose the cluster's results.

    _collect_one caught only TimeoutError/ConnectionError/OSError, but
    _deserialize_stats raises ValueError on any non-conforming JSON. One buggy
    -- or, since the control plane is unauthenticated without --worker-secret,
    hostile -- worker turned the whole run into a traceback.
    """

    async def test_master_survives_malformed_result_from_one_worker(self):
        config = pywrkr.BenchmarkConfig(url="http://example.com", duration=1, _quiet=True)
        port_holder = [0]

        async def _peer(payload: dict):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)
            writer.write(_frame(payload))
            await writer.drain()
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

        good = pywrkr.WorkerStats()
        good.total_requests = 250
        good.latencies.extend([0.01] * 250)

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                peers = [
                    asyncio.create_task(
                        _peer(
                            {
                                "type": "result",
                                "stats": _serialize_stats(good),
                                "duration": 1.0,
                            }
                        )
                    ),
                    # Well-framed, valid JSON, and unusable: a status-code key
                    # that is not a status code.
                    asyncio.create_task(
                        _peer(
                            {
                                "type": "result",
                                "stats": {"status_codes": {"abc": 1}},
                                "duration": 1.0,
                            }
                        )
                    ),
                ]
                with self.assertLogs("pywrkr", level="ERROR") as logs:
                    result = await asyncio.wait_for(
                        run_master(config, "127.0.0.1", 0, expect_workers=2), timeout=20
                    )
                await asyncio.gather(*peers)

        # Pre-fix run_master raised ValueError and there was no result at all.
        self.assertIsNotNone(result)
        merged, _ = result
        self.assertEqual(merged.total_requests, 250)
        self.assertTrue(
            any("malformed result discarded" in line for line in logs.output),
            f"the bad worker was not reported: {logs.output}",
        )


class TestMasterShardsLoad(unittest.IsolatedAsyncioTestCase):
    """The README promises the master splits the workload; it must actually do it."""

    async def test_master_shards_num_requests_and_rate_across_workers(self):
        config = pywrkr.BenchmarkConfig(
            url="http://example.com", num_requests=10, rate=6.0, connections=4, _quiet=True
        )
        port_holder = [0]
        received: list = []

        async def _peer():
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            ln = int.from_bytes(await reader.readexactly(4), "big")
            payload = await reader.readexactly(ln)
            received.append(json.loads(payload)["config"])
            stats = pywrkr.WorkerStats()
            stats.total_requests = 5
            stats.latencies.extend([0.01] * 5)
            writer.write(
                _frame({"type": "result", "stats": _serialize_stats(stats), "duration": 1.0})
            )
            await writer.drain()
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                peers = [asyncio.create_task(_peer()) for _ in range(2)]
                await asyncio.wait_for(
                    run_master(config, "127.0.0.1", 0, expect_workers=2), timeout=20
                )
                await asyncio.gather(*peers)

        self.assertEqual(len(received), 2)
        # Pre-fix each worker was told -n 10 and --rate 6.0, so the cluster ran
        # 20 requests at 12 rps -- double what was asked for.
        self.assertEqual(sum(c["num_requests"] for c in received), 10)
        for shard in received:
            self.assertAlmostEqual(shard["rate"], 3.0, places=6)
            # A node's pool is its own; it is not a share of the cluster's work.
            self.assertEqual(shard["connections"], 4)


class TestPartialClusterExitCode(unittest.IsolatedAsyncioTestCase):
    """A run missing nodes carried less load than asked for; it is not a pass."""

    def _peer_factory(self, port_holder):
        async def _peer(payload):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)
            writer.write(_frame(payload))
            await writer.drain()
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

        return _peer

    async def _run(self, allow_partial: bool):
        config = pywrkr.BenchmarkConfig(url="http://example.com", duration=1, _quiet=True)
        # Set rather than passed, so the default path still constructs on a
        # tree without the field and this test fails on the exit code it is
        # about rather than on a TypeError.
        if allow_partial:
            config.allow_partial = True
        port_holder = [0]
        peer = self._peer_factory(port_holder)

        good = pywrkr.WorkerStats()
        good.total_requests = 100
        good.latencies.extend([0.01] * 100)

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                peers = [
                    asyncio.create_task(
                        peer(
                            {
                                "type": "result",
                                "stats": _serialize_stats(good),
                                "duration": 1.0,
                            }
                        )
                    ),
                    # A worker that refused the run outright, e.g. missing the
                    # HTTP/2 backend. Logged, and previously nothing more.
                    asyncio.create_task(peer({"type": "error", "error": "no h2 backend"})),
                ]
                result = await asyncio.wait_for(
                    run_master(config, "127.0.0.1", 0, expect_workers=2), timeout=20
                )
                await asyncio.gather(*peers)
        return result

    async def test_partial_worker_results_give_nonzero_exit(self):
        result = self._run(allow_partial=False)
        with self.assertLogs("pywrkr", level="ERROR") as logs:
            merged, exit_code = await result
        # Pre-fix this was 0: one node reported, so the partial set was merged
        # and the gate passed on half the intended load.
        self.assertEqual(exit_code, 1)
        self.assertEqual(merged.total_requests, 100)
        self.assertTrue(
            any("only 1 of 2 workers returned results" in line for line in logs.output),
            f"the shortfall was not reported: {logs.output}",
        )

    async def test_allow_partial_accepts_the_shortfall(self):
        merged, exit_code = await self._run(allow_partial=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(merged.total_requests, 100)


class TestBoundPortHelper(unittest.IsolatedAsyncioTestCase):
    """The helper the fake workers use to avoid racing the master's bind."""

    async def test_waits_for_a_late_bind(self):
        port_holder = [0]

        async def _bind_later():
            await asyncio.sleep(0.2)
            port_holder[0] = 4242

        binder = asyncio.create_task(_bind_later())
        self.assertEqual(await _bound_port(port_holder), 4242)
        # Bound to _ so CodeQL does not read the bare await as a statement
        # with no effect (py/ineffectual-statement), as elsewhere in the repo.
        _ = await binder

    async def test_times_out_with_a_message_naming_the_cause(self):
        with self.assertRaises(AssertionError) as ctx:
            await _bound_port([0], timeout=0.05)
        self.assertIn("did not bind a port", str(ctx.exception))


# ---------------------------------------------------------------------------
# dist-8: explicit empty body must survive the config round-trip
# ---------------------------------------------------------------------------
class TestDist8EmptyBody(unittest.TestCase):
    def test_empty_body_roundtrip_preserves_bytes(self):
        config = pywrkr.BenchmarkConfig(
            url="http://127.0.0.1/", method="POST", body=b"", _quiet=True
        )
        restored = _deserialize_config(_serialize_config(config))
        # Old code: b"" is falsy -> serialized to None -> reconstructed as None.
        self.assertEqual(restored.body, b"")
        self.assertIsNotNone(restored.body)

    def test_none_body_still_none(self):
        config = pywrkr.BenchmarkConfig(url="http://127.0.0.1/", body=None, _quiet=True)
        restored = _deserialize_config(_serialize_config(config))
        self.assertIsNone(restored.body)

    def test_nonempty_body_roundtrip(self):
        config = pywrkr.BenchmarkConfig(
            url="http://127.0.0.1/", method="POST", body=b"payload", _quiet=True
        )
        restored = _deserialize_config(_serialize_config(config))
        self.assertEqual(restored.body, b"payload")


# ---------------------------------------------------------------------------
# dist-4: _recv_msg translates body-decode failures into ConnectionError
# ---------------------------------------------------------------------------
class TestDist4MalformedBody(unittest.IsolatedAsyncioTestCase):
    async def _feed(self, body: bytes) -> dict:
        reader = asyncio.StreamReader()
        reader.feed_data(len(body).to_bytes(4, "big") + body)
        reader.feed_eof()
        return await _recv_msg(reader)

    async def test_non_json_body_raises_connection_error(self):
        with self.assertRaises(ConnectionError):
            await self._feed(b"not json{{{")

    async def test_non_utf8_body_raises_connection_error(self):
        with self.assertRaises(ConnectionError):
            await self._feed(b"\xff\xfe\xff")

    async def test_valid_body_still_parses(self):
        msg = await self._feed(json.dumps({"type": "ping"}).encode())
        self.assertEqual(msg["type"], "ping")

    async def test_garbage_worker_does_not_crash_master(self):
        """One worker sends framed garbage, another sends a valid result; the
        valid worker's stats are still merged and run_master returns non-None."""
        config = pywrkr.BenchmarkConfig(url="http://example.com", duration=1, _quiet=True)
        port_holder = [0]

        async def _bad_worker():
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)  # drain the config payload
            # Well-framed but non-UTF8/non-JSON body.
            writer.write(b"\x00\x00\x00\x05" + b"\xff\xfe\xff!@")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async def _good_worker():
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)
            stats = pywrkr.WorkerStats()
            stats.total_requests = 42
            stats.latencies.extend([0.01] * 42)
            writer.write(
                _frame({"type": "result", "stats": _serialize_stats(stats), "duration": 1.0})
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                tasks = [
                    asyncio.create_task(_bad_worker()),
                    asyncio.create_task(_good_worker()),
                ]
                result = await asyncio.wait_for(
                    run_master(config, "127.0.0.1", 0, expect_workers=2), timeout=20
                )
                await asyncio.gather(*tasks)

        self.assertIsNotNone(result)
        merged, _ = result
        # Only the good worker's stats survive; the master did not crash.
        self.assertEqual(merged.total_requests, 42)


# ---------------------------------------------------------------------------
# dist-3: weighted reservoir merge preserves total_seen / true weighting
# ---------------------------------------------------------------------------
class TestDist3ReservoirMerge(unittest.TestCase):
    def test_total_seen_summed_across_full_reservoirs(self):
        cap = DEFAULT_RESERVOIR_SIZE
        ws1 = pywrkr.WorkerStats()
        ws1.latencies = ReservoirSampler.from_list([0.1] * cap, total_seen=500_000)
        ws2 = pywrkr.WorkerStats()
        ws2.latencies = ReservoirSampler.from_list([0.2] * cap, total_seen=500_000)

        merged = merge_worker_stats([ws1, ws2])
        # Old code recomputed total_seen from sampled items (~200k); fixed code
        # records the true combined volume.
        self.assertEqual(merged.latencies.total_seen, 1_000_000)
        # Sample stays bounded by capacity.
        self.assertLessEqual(len(merged.latencies), cap)

    def test_low_volume_worker_not_overrepresented(self):
        """A 1,000,000-request fast worker (downsampled) merged with a 1,000-
        request slow worker: the slow samples must be ~0.1% of the merged
        sample, not ~50% as naive concatenation would produce."""
        cap = DEFAULT_RESERVOIR_SIZE
        fast = pywrkr.WorkerStats()
        fast.latencies = ReservoirSampler.from_list([0.01] * cap, total_seen=1_000_000)
        slow = pywrkr.WorkerStats()
        slow.latencies = ReservoirSampler.from_list([5.0] * 1_000, total_seen=1_000)

        merged = merge_worker_stats([fast, slow])
        self.assertEqual(merged.latencies.total_seen, 1_001_000)
        slow_count = sum(1 for x in merged.latencies if x == 5.0)
        frac = slow_count / max(1, len(merged.latencies))
        # True fraction is ~0.0999%. Naive concat would give ~0.99% (10x).
        self.assertLess(frac, 0.004)

    def test_small_lists_keep_all_items(self):
        """Backward-compatible with the small-run case: total_seen == len, so
        every item is retained."""
        ws1 = pywrkr.WorkerStats()
        ws1.latencies = [0.1, 0.2]
        ws2 = pywrkr.WorkerStats()
        ws2.latencies = [0.15, 0.25, 0.35]
        merged = merge_worker_stats([ws1, ws2])
        self.assertEqual(len(merged.latencies), 5)
        self.assertEqual(merged.latencies.total_seen, 5)


# ---------------------------------------------------------------------------
# dist-5: merged timeline aligned to a common [0, duration) axis
# ---------------------------------------------------------------------------
class TestDist5Timeline(unittest.TestCase):
    def test_normalize_rebases_to_zero_origin(self):
        tl = [(1000.0, 100), (1001.0, 150)]
        self.assertEqual(_normalize_timeline(tl), [(0.0, 100), (1.0, 150)])

    def test_normalize_empty(self):
        self.assertEqual(_normalize_timeline([]), [])

    def test_merged_timeline_buckets_summed_across_workers(self):
        """Two workers with different monotonic origins covering the same
        logical seconds yield correctly summed per-second buckets."""
        ws1 = pywrkr.WorkerStats()
        ws1.rps_timeline = [(1000.0, 100), (1001.0, 100), (1002.0, 100)]
        ws2 = pywrkr.WorkerStats()
        ws2.rps_timeline = [(50000.0, 200), (50001.0, 200), (50002.0, 200)]

        merged = merge_worker_stats([ws1, ws2])
        # Render through the master bucketing path with start=0.0, duration=3.
        buf = StringIO()
        pywrkr.print_rps_timeline(merged.rps_timeline, 0.0, 3, file=buf)
        out = buf.getvalue()
        # Each second bucket should sum to 300 req/s (100 + 200); no spurious
        # thousands-of-rows output from huge cross-host buckets.
        self.assertIn("300.0 req/s", out)
        self.assertLessEqual(out.count("req/s"), 5)


# ---------------------------------------------------------------------------
# dist-1: master uses measured worker duration in -n mode, not a fixed 10s
# ---------------------------------------------------------------------------
class TestDist1MeasuredDuration(unittest.IsolatedAsyncioTestCase):
    async def test_rps_threshold_evaluated_against_real_duration(self):
        # num_requests mode (duration=None). Worker reports 100 reqs over 2.0s
        # => real RPS = 50. Old code assumed 10s => RPS = 10, so an
        # `rps > 25` threshold would WRONGLY fail. With measured duration it
        # passes (50 > 25).
        threshold = pywrkr.Threshold(metric="rps", operator=">", value=25.0, raw_expr="rps>25")
        config = pywrkr.BenchmarkConfig(
            url="http://example.com",
            duration=None,
            num_requests=100,
            thresholds=[threshold],
            _quiet=True,
        )
        port_holder = [0]

        async def _fake_worker():
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)
            stats = pywrkr.WorkerStats()
            stats.total_requests = 100
            stats.latencies.extend([0.01] * 100)
            stats.status_codes[200] = 100
            writer.write(
                _frame({"type": "result", "stats": _serialize_stats(stats), "duration": 2.0})
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                worker = asyncio.create_task(_fake_worker())
                result = await asyncio.wait_for(
                    run_master(config, "127.0.0.1", 0, expect_workers=1), timeout=20
                )
                _ = await worker

        self.assertIsNotNone(result)
        merged, exit_code = result
        # Real RPS (50) > 25 -> threshold passes -> exit_code 0. Under the old
        # fabricated 10s window RPS would be 10 -> fail -> exit_code 2.
        self.assertEqual(exit_code, 0)


# ---------------------------------------------------------------------------
# dist-2: extra/late silent peer must not make the master hang
# ---------------------------------------------------------------------------
class TestDist2ExtraWorker(unittest.IsolatedAsyncioTestCase):
    async def test_extra_silent_peer_does_not_block_master(self):
        # One legit worker satisfies expect_workers=1; a surplus silent peer
        # only attempts to connect AFTER the legit worker has already received
        # its config (which proves the master passed ready_event and closed the
        # listener). The master must not block collection on the surplus peer.
        config = pywrkr.BenchmarkConfig(url="http://example.com", duration=1, _quiet=True)
        port_holder = [0]
        config_received = asyncio.Event()

        async def _good_worker():
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)
            config_received.set()
            stats = pywrkr.WorkerStats()
            stats.total_requests = 10
            stats.latencies.extend([0.01] * 10)
            writer.write(
                _frame({"type": "result", "stats": _serialize_stats(stats), "duration": 1.0})
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async def _silent_peer():
            await config_received.wait()
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port_holder[0])
            except OSError:
                return  # server already closed -> connection refused is fine
            try:
                await asyncio.sleep(10)
            finally:
                writer.close()

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                worker = asyncio.create_task(_good_worker())
                peer = asyncio.create_task(_silent_peer())
                t0 = time.monotonic()
                result = await asyncio.wait_for(
                    run_master(config, "127.0.0.1", 0, expect_workers=1), timeout=10
                )
                elapsed = time.monotonic() - t0
                peer.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.gather(worker, peer, return_exceptions=True)

        self.assertIsNotNone(result)
        merged, _ = result
        # The legit worker contributed; the surplus silent peer was ignored.
        self.assertEqual(merged.total_requests, 10)
        # Old code would block ~600s (or the test timeout) on the silent peer.
        self.assertLess(elapsed, 5.0)

    async def test_surplus_connection_in_handler_is_rejected_not_collected(self):
        # Deterministically exercise surplus-connection rejection. The surplus
        # peer connects only AFTER both legit workers have been accepted (i.e.
        # received their config frame), so it is always the third connection and
        # can never displace a legit worker. (Connecting all three concurrently,
        # as this test originally did, made it flaky: the accept order of
        # concurrent connections is not guaranteed, so a legit worker could be
        # the one rejected.) The legit workers withhold their result until the
        # surplus has been handled, keeping the master in result-collection with
        # the listener still open so the in-handler rejection branch is hit.
        config = pywrkr.BenchmarkConfig(url="http://example.com", duration=1, _quiet=True)
        port_holder = [0]
        accepted = [0]
        both_accepted = asyncio.Event()
        surplus_handled = asyncio.Event()

        async def _connect():
            return await asyncio.open_connection("127.0.0.1", await _bound_port(port_holder))

        async def _legit_worker(n: int):
            reader, writer = await _connect()
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)  # received config => accepted as a worker
            accepted[0] += 1
            if accepted[0] >= 2:
                both_accepted.set()
            # Hold the result until the surplus peer has been handled so the
            # master is still collecting (listener open) when it connects.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(surplus_handled.wait(), timeout=8)
            stats = pywrkr.WorkerStats()
            stats.total_requests = n
            stats.latencies.extend([0.01] * n)
            writer.write(
                _frame({"type": "result", "stats": _serialize_stats(stats), "duration": 1.0})
            )
            await writer.drain()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        async def _surplus_peer():
            # Connect only once both legit workers are accepted, so this peer is
            # guaranteed to be the surplus (third) connection.
            await both_accepted.wait()
            try:
                reader, writer = await _connect()
            except OSError:
                surplus_handled.set()  # listener already closed => rejected
                return
            try:
                # A rejected surplus peer's socket is closed by the master, so a
                # read returns EOF (b"") promptly; never block.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(reader.read(100), timeout=5)
            finally:
                writer.close()
                surplus_handled.set()

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                w1 = asyncio.create_task(_legit_worker(7))
                w2 = asyncio.create_task(_legit_worker(11))
                peer = asyncio.create_task(_surplus_peer())
                t0 = time.monotonic()
                result = await asyncio.wait_for(
                    run_master(config, "127.0.0.1", 0, expect_workers=2), timeout=10
                )
                elapsed = time.monotonic() - t0
                peer.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.gather(w1, w2, peer, return_exceptions=True)

        self.assertIsNotNone(result)
        merged, _ = result
        # Only the two legit workers' stats are collected (7 + 11); the surplus
        # connection was rejected/closed and never contributed or blocked.
        self.assertEqual(merged.total_requests, 18)
        self.assertLess(elapsed, 5.0)


# ---------------------------------------------------------------------------
# dist-6: concurrent collection bounded by slowest worker, not the sum
# ---------------------------------------------------------------------------
class TestDist6ConcurrentCollection(unittest.IsolatedAsyncioTestCase):
    async def test_collection_not_head_of_line_blocked(self):
        config = pywrkr.BenchmarkConfig(url="http://example.com", duration=1, _quiet=True)
        port_holder = [0]
        connect_order: list = []

        async def _worker(send_delay: float, n: int):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", await _bound_port(port_holder)
            )
            connect_order.append(writer)
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)
            await asyncio.sleep(send_delay)
            stats = pywrkr.WorkerStats()
            stats.total_requests = n
            stats.latencies.extend([0.01] * n)
            writer.write(
                _frame({"type": "result", "stats": _serialize_stats(stats), "duration": 1.0})
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            server = await orig_start(cb, host, 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                # First-connected worker delays its send by 1.5s; second sends
                # immediately. Sequential collection would take >=1.5s; concurrent
                # collection finishes when the slowest (1.5s) replies, but the fast
                # one is read promptly. Total bounded by ~1.5s, NOT 1.5 + ~0.
                t0 = time.monotonic()
                workers = [
                    asyncio.create_task(_worker(1.5, 100)),
                    asyncio.create_task(_worker(0.0, 200)),
                ]
                result = await asyncio.wait_for(
                    run_master(config, "127.0.0.1", 0, expect_workers=2), timeout=20
                )
                await asyncio.gather(*workers)
                elapsed = time.monotonic() - t0

        self.assertIsNotNone(result)
        merged, _ = result
        # Both workers contribute.
        self.assertEqual(merged.total_requests, 300)
        # Bounded by the slowest worker (~1.5s + overhead), well under a
        # sequential-with-full-timeout worst case.
        self.assertLess(elapsed, 5.0)


class TestMasterRequiresSecretOffLoopback(unittest.IsolatedAsyncioTestCase):
    """#303: a master reachable off-host must not run an unauthenticated channel.

    Nothing here binds a non-loopback interface: ``start_server`` is patched to
    bind 127.0.0.1 on a random port, so the decision is taken on the address
    the caller asked for while the socket stays local and xdist-safe.
    """

    _ENV = {k: v for k, v in os.environ.items() if k != "PYWRKR_WORKER_SECRET"}

    def _parse(self, argv: list[str]):
        parser = _build_parser()
        args = parser.parse_args(argv)
        return _parse_and_validate_args(parser, args)

    def test_master_requires_worker_secret_on_non_loopback_bind(self):
        base = ["--master", "--expect-workers", "1", "http://127.0.0.1:1/"]
        with patch.dict(os.environ, self._ENV, clear=True):
            for bind in ("0.0.0.0", "::", "10.0.0.5", "master.internal"):
                with self.subTest(bind=bind):
                    err = StringIO()
                    with patch("sys.stderr", err), self.assertRaises(SystemExit) as cm:
                        self._parse([*base, "--bind", bind])
                    self.assertEqual(cm.exception.code, 2)
                    self.assertIn("--worker-secret", err.getvalue())
            # The default bind is every interface, so it is refused too.
            with patch("sys.stderr", StringIO()), self.assertRaises(SystemExit):
                self._parse(base)
            # Loopback masters keep working without a secret.
            for bind in ("127.0.0.1", "127.8.9.10", "::1"):
                with self.subTest(bind=bind):
                    self._parse([*base, "--bind", bind])
            # Bracketed / padded forms are normalised to the address that is
            # actually bound (start_server rejects "[::1]").
            for bind, bound in (("[::1]", "::1"), (" 127.0.0.1 ", "127.0.0.1")):
                with self.subTest(bind=bind):
                    _, args = self._parse([*base, "--bind", bind])
                    self.assertEqual(args.bind, bound)
            # A secret from the flag or the environment admits a public bind.
            self._parse([*base, "--bind", "0.0.0.0", "--worker-secret", "s3cret"])
        with patch.dict(os.environ, {**self._ENV, "PYWRKR_WORKER_SECRET": "s3cret"}, clear=True):
            self._parse([*base, "--bind", "0.0.0.0"])

    def test_is_loopback_bind(self):
        for host in ("127.0.0.1", "127.255.0.1", "::1"):
            self.assertTrue(is_loopback_bind(host), host)
        # Judged as the literal string start_server would receive.
        for host in ("[::1]", " 127.0.0.1"):
            self.assertFalse(is_loopback_bind(host), host)
        # The real resolver differs per host (some map localhost to fe80::1%lo0),
        # so the positive name case runs against a pinned answer.
        loop_only = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0)),
        ]
        with patch("pywrkr.distributed.socket.getaddrinfo", return_value=loop_only):
            for host in ("localhost", "LOCALHOST"):
                self.assertTrue(is_loopback_bind(host), host)
        for host in (
            None,
            "",
            "0.0.0.0",
            "::",
            "[::]",
            "10.0.0.5",
            "example.com",
            "localhost.evil",
        ):
            self.assertFalse(is_loopback_bind(host), host)

    def test_host_lists_and_scoped_loopback(self):
        self.assertTrue(is_loopback_bind(["127.0.0.1", "::1"]))
        self.assertFalse(is_loopback_bind(["127.0.0.1", "0.0.0.0"]))
        self.assertFalse(is_loopback_bind([]))
        scoped = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%lo0", 0, 0, 1)),
        ]
        with patch("pywrkr.distributed.socket.getaddrinfo", return_value=scoped):
            with patch("pywrkr.distributed.socket.if_indextoname", return_value="lo0"):
                self.assertTrue(is_loopback_bind("localhost"))
            for name in ("en0", "lowpan0", "lo-mgmt"):
                with patch("pywrkr.distributed.socket.if_indextoname", return_value=name):
                    self.assertFalse(is_loopback_bind("localhost"), name)

    def test_localhost_mapped_off_loopback_is_not_trusted(self):
        """A hosts file can point ``localhost`` at a reachable address."""
        mapped = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        with patch("pywrkr.distributed.socket.getaddrinfo", return_value=mapped):
            self.assertFalse(is_loopback_bind("localhost"))
        with patch("pywrkr.distributed.socket.getaddrinfo", side_effect=OSError):
            self.assertFalse(is_loopback_bind("localhost"))

    def test_refusal_comes_before_any_input_file_is_read(self):
        """The secret check must not wait for body/scenario files to load."""
        argv = ["--master", "--expect-workers", "1", "--bind", "0.0.0.0"]
        argv += ["-p", "/nonexistent/pywrkr-303-body", "http://127.0.0.1:1/"]
        err = StringIO()
        with patch.dict(os.environ, self._ENV, clear=True):
            with patch("sys.stderr", err), self.assertRaises(SystemExit):
                self._parse(argv)
        self.assertIn("--worker-secret", err.getvalue())

    async def test_master_rejects_unauthenticated_peer_without_secret(self):
        """run_master itself refuses, so no peer is ever sent the config."""
        config = pywrkr.BenchmarkConfig(
            url="http://127.0.0.1:1", duration=0.01, basic_auth="ci-user:dummy", _quiet=True
        )
        with patch("pywrkr.distributed.asyncio.start_server") as start:
            for host, secret in (("0.0.0.0", None), ("0.0.0.0", ""), (None, None)):
                with self.subTest(host=host, secret=secret), self.assertRaises(ValueError) as cm:
                    # Bounded: the pre-fix master waits for workers instead of raising.
                    await asyncio.wait_for(
                        run_master(config, host, 0, expect_workers=1, worker_secret=secret),
                        timeout=5,
                    )
                self.assertIn("--worker-secret", str(cm.exception))
        start.assert_not_called()

    async def test_master_with_secret_still_admits_a_real_worker(self):
        secret = "test-secret-303"
        config = pywrkr.BenchmarkConfig(
            url="http://127.0.0.1:1", duration=0.01, connections=1, _quiet=True
        )
        port_holder = [0]
        bound_hosts: list = []
        orig_start = asyncio.start_server

        async def _patched_start(cb, host, port):
            bound_hosts.append(host)
            server = await orig_start(cb, "127.0.0.1", 0)
            port_holder[0] = server.sockets[0].getsockname()[1]
            return server

        with patch("pywrkr.distributed.asyncio.start_server", side_effect=_patched_start):
            with patch("sys.stdout", new_callable=StringIO):
                master = asyncio.create_task(
                    run_master(config, "0.0.0.0", 0, expect_workers=1, worker_secret=secret)
                )
                port = await _bound_port(port_holder)
                worker = asyncio.create_task(
                    run_worker_node("127.0.0.1", port, worker_secret=secret)
                )
                result = await asyncio.wait_for(master, timeout=15)
                worker_exit = await asyncio.wait_for(worker, timeout=15)

        self.assertEqual(bound_hosts, ["0.0.0.0"])
        self.assertIsNotNone(result)
        self.assertEqual(worker_exit, 0)


class TestWorkerRequiresSecretAndValidTarget(unittest.IsolatedAsyncioTestCase):
    """#304: a worker without a secret only trusts a loopback master, and never
    runs a config whose target the CLI itself would refuse."""

    _ENV = {k: v for k, v in os.environ.items() if k != "PYWRKR_WORKER_SECRET"}

    def _main(self, argv: list[str]):
        from pywrkr.main import main

        with patch.dict(os.environ, self._ENV, clear=True), patch("sys.argv", ["pywrkr", *argv]):
            with patch("pywrkr.main.run_worker_node") as run, contextlib.suppress(SystemExit):
                main()
        return run

    def test_cli_refuses_worker_without_secret_off_loopback(self):
        from pywrkr.main import main

        for target in ("10.0.0.1:9220", "master.internal:9220", "0.0.0.0:9220"):
            with self.subTest(target=target):
                err = StringIO()
                with (
                    patch.dict(os.environ, self._ENV, clear=True),
                    patch("sys.argv", ["pywrkr", "--worker", target]),
                    patch("pywrkr.main.run_worker_node") as run,
                    patch("sys.stderr", err),
                    self.assertRaises(SystemExit) as cm,
                ):
                    main()
                self.assertEqual(cm.exception.code, 2)
                self.assertIn("--worker-secret", err.getvalue())
                run.assert_not_called()

    def test_cli_allows_loopback_master_or_a_secret(self):
        cases = (
            (["--worker", "127.0.0.1:9220"], "127.0.0.1", None),
            (["--worker", "[::1]:9220"], "::1", None),
            (["--worker", "10.0.0.1:9220", "--worker-secret", "s"], "10.0.0.1", "s"),
        )
        for argv, host, secret in cases:
            with self.subTest(argv=argv):
                run = self._main(argv)
                run.assert_called_once_with(host, 9220, worker_secret=secret)

    async def test_worker_refuses_to_run_without_worker_secret(self):
        """API callers get the same rule: no connection is even attempted."""
        with patch("pywrkr.distributed.asyncio.open_connection") as conn:
            for secret in (None, ""):
                with self.subTest(secret=secret):
                    rc = await asyncio.wait_for(
                        run_worker_node("10.0.0.5", 9220, worker_secret=secret), timeout=5
                    )
                    self.assertEqual(rc, 1)
        conn.assert_not_called()

    async def _run_against_fake_master(self, config: dict) -> tuple[int, dict | None]:
        reply: asyncio.Future = asyncio.get_running_loop().create_future()

        async def _handle(reader, writer):
            await _send_msg(writer, {"type": "config", "config": config})
            try:
                reply.set_result(await asyncio.wait_for(_recv_msg(reader), timeout=5))
            except Exception:
                reply.set_result(None)
            writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with patch("pywrkr.distributed.run_benchmark") as bench:
                with patch("pywrkr.distributed.run_user_simulation") as users:
                    rc = await asyncio.wait_for(run_worker_node("127.0.0.1", port), timeout=10)
            bench.assert_not_called()
            users.assert_not_called()
            return rc, await asyncio.wait_for(reply, timeout=5)
        finally:
            server.close()
            await server.wait_closed()

    async def test_worker_rejects_config_with_non_http_target(self):
        good = pywrkr.BenchmarkConfig(url="http://127.0.0.1:1/", duration=0.01, _quiet=True)
        for bad_url in ("file:///etc/passwd", "gopher://127.0.0.1/", "ws://127.0.0.1/", 123):
            with self.subTest(url=bad_url):
                config = _serialize_config(good)
                config["url"] = bad_url
                rc, reply = await self._run_against_fake_master(config)
                self.assertEqual(rc, 1)
                self.assertIsNotNone(reply)
                self.assertEqual(reply["type"], "error")
                self.assertIn("Invalid URL scheme", reply["error"])

    async def test_worker_rejects_scenario_base_url_with_non_http_scheme(self):
        from pywrkr.config import Scenario, ScenarioStep

        good = pywrkr.BenchmarkConfig(
            url="http://127.0.0.1:1/",
            duration=0.01,
            scenario=Scenario(name="s", steps=[ScenarioStep(path="/")], base_url="file:///etc"),
            _quiet=True,
        )
        rc, reply = await self._run_against_fake_master(_serialize_config(good))
        self.assertEqual(rc, 1)
        self.assertIn("base_url", reply["error"])

    def test_link_local_target_is_logged(self):
        cfg = pywrkr.BenchmarkConfig(url="http://169.254.169.254/latest/", _quiet=True)
        with self.assertLogs("pywrkr.distributed", level="WARNING") as logs:
            check_worker_target(cfg)
        self.assertIn("link-local", logs.output[0])

    def test_ordinary_target_passes_quietly(self):
        for url in ("http://10.0.0.1/", "https://example.com/x", "http://[::1]:8080/"):
            with self.subTest(url=url):
                check_worker_target(pywrkr.BenchmarkConfig(url=url, _quiet=True))


if __name__ == "__main__":
    unittest.main()
