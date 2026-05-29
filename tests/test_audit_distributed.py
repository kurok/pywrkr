"""Regression tests for confirmed defects in pywrkr.distributed.

Each test targets one finding id from the audit work order and is written to
FAIL on the pre-fix code and PASS after the fix.
"""

import asyncio
import contextlib
import json
import time
import unittest
from io import StringIO
from unittest.mock import patch

import pywrkr
from pywrkr.config import DEFAULT_RESERVOIR_SIZE, ReservoirSampler
from pywrkr.distributed import (
    _deserialize_config,
    _normalize_timeline,
    _recv_msg,
    _serialize_config,
    _serialize_stats,
    merge_worker_stats,
    run_master,
)


async def _free_port() -> int:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port


def _frame(obj: dict) -> bytes:
    payload = json.dumps(obj).encode()
    return len(payload).to_bytes(4, "big") + payload


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
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_connection("127.0.0.1", port_holder[0])
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)  # drain the config payload
            # Well-framed but non-UTF8/non-JSON body.
            writer.write(b"\x00\x00\x00\x05" + b"\xff\xfe\xff!@")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async def _good_worker():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_connection("127.0.0.1", port_holder[0])
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
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_connection("127.0.0.1", port_holder[0])
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
                await worker

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
            while port_holder[0] == 0:
                await asyncio.sleep(0.01)
            reader, writer = await asyncio.open_connection("127.0.0.1", port_holder[0])
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
        # Exercise the in-handler rejection branch deterministically. With
        # expect_workers=2 we connect THREE peers concurrently before the master
        # resumes from ready_event.wait(); whichever connection is accepted
        # third sees len(worker_connections) >= 2 and is rejected by
        # handle_worker, never appended to the collection set, so it can never
        # stall result collection. To guarantee the third connection is accepted
        # before the listener closes, the first two legit workers withhold their
        # result until all three peers report they have connected.
        config = pywrkr.BenchmarkConfig(url="http://example.com", duration=1, _quiet=True)
        port_holder = [0]
        connected = [0]
        all_connected = asyncio.Event()
        rejected_seen = asyncio.Event()

        async def _connect():
            while port_holder[0] == 0:
                await asyncio.sleep(0.01)
            return await asyncio.open_connection("127.0.0.1", port_holder[0])

        async def _legit_worker(n: int):
            reader, writer = await _connect()
            connected[0] += 1
            if connected[0] >= 3:
                all_connected.set()
            ln = int.from_bytes(await reader.readexactly(4), "big")
            await reader.readexactly(ln)
            stats = pywrkr.WorkerStats()
            stats.total_requests = n
            stats.latencies.extend([0.01] * n)
            writer.write(
                _frame({"type": "result", "stats": _serialize_stats(stats), "duration": 1.0})
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async def _surplus_peer():
            try:
                reader, writer = await _connect()
            except OSError:
                connected[0] += 1
                if connected[0] >= 3:
                    all_connected.set()
                rejected_seen.set()
                return
            connected[0] += 1
            if connected[0] >= 3:
                all_connected.set()
            try:
                # A rejected surplus peer's socket is closed by the master, so a
                # read returns EOF (b"") promptly; a *selected* peer would be
                # sent a config frame instead. Either way, never block.
                with contextlib.suppress(Exception):
                    data = await asyncio.wait_for(reader.read(100), timeout=5)
                    if data == b"":
                        rejected_seen.set()
            finally:
                writer.close()

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
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_connection("127.0.0.1", port_holder[0])
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


if __name__ == "__main__":
    unittest.main()
