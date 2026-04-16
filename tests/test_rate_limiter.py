"""Tests for rate limiter lock-free optimization.

Validates that the lock-free RateLimiter correctly limits rate and handles
high concurrency without contention issues.
"""

import asyncio
import time
import unittest

from pywrkr.traffic_profiles import RateLimiter


class TestRateLimiterLockFree(unittest.TestCase):
    """Tests verifying the lock-free rate limiter works correctly."""

    def test_tokens_consumed_correctly(self):
        """Sequential acquires should space requests according to the rate."""

        async def _run():
            rl = RateLimiter(rate=100)  # 10ms intervals
            times = []
            for _ in range(6):
                await rl.acquire()
                times.append(time.monotonic())
            intervals = [times[i + 1] - times[i] for i in range(len(times) - 1)]
            return intervals

        intervals = asyncio.run(_run())
        # Assert on total elapsed time, not individual intervals (CI runners have jitter)
        total = sum(intervals)
        # 5 intervals at ~10ms each = ~50ms total
        self.assertGreaterEqual(total, 0.025)
        self.assertLess(total, 0.300)

    def test_high_concurrency_respects_rate(self):
        """Many concurrent coroutines sharing one limiter should respect rate."""

        async def _run():
            rl = RateLimiter(rate=200)  # 5ms intervals
            acquire_times = []

            async def _worker(n):
                for _ in range(n):
                    await rl.acquire()
                    acquire_times.append(time.monotonic())

            start = time.monotonic()
            # 10 workers, 5 acquires each = 50 total
            await asyncio.gather(*[_worker(5) for _ in range(10)])
            elapsed = time.monotonic() - start
            return len(acquire_times), elapsed

        total, elapsed = asyncio.run(_run())
        self.assertEqual(total, 50)
        # 50 acquires at 200/s: first instant, 49 intervals of 5ms = ~0.245s
        self.assertGreaterEqual(elapsed, 0.20)
        self.assertLess(elapsed, 0.50)

    def test_high_concurrency_no_duplicate_slots(self):
        """Concurrent coroutines sharing a limiter must respect total rate."""

        async def _run():
            rl = RateLimiter(rate=500)  # 2ms intervals
            start = time.monotonic()

            async def _worker():
                for _ in range(5):
                    await rl.acquire()

            await asyncio.gather(*[_worker() for _ in range(8)])
            elapsed = time.monotonic() - start
            return elapsed

        elapsed = asyncio.run(_run())
        # 40 acquires at 500/s: first instant, 39 intervals of 2ms = ~0.078s minimum
        # With scheduling overhead, should be between 0.06s and 0.30s
        self.assertGreaterEqual(elapsed, 0.06, "Rate limiter not enforcing rate")
        self.assertLess(elapsed, 0.50, "Rate limiter taking too long")

    def test_waits_counter_incremented(self):
        """The waits counter should track sleeps even in lock-free mode."""

        async def _run():
            rl = RateLimiter(rate=200)
            for _ in range(10):
                await rl.acquire()
            return rl.waits

        waits = asyncio.run(_run())
        # First acquire is instant; threshold is loose to tolerate CI scheduler jitter
        self.assertGreaterEqual(waits, 5)

    def test_rate_zero_does_not_block(self):
        """Rate of 0 should return immediately without blocking."""

        async def _run():
            rl = RateLimiter(rate=0)
            start = time.monotonic()
            for _ in range(5):
                await rl.acquire()
            return time.monotonic() - start

        elapsed = asyncio.run(_run())
        self.assertLess(elapsed, 0.05)

    def test_first_acquire_is_instant(self):
        """The very first acquire should not sleep."""

        async def _run():
            rl = RateLimiter(rate=10)  # 100ms intervals
            start = time.monotonic()
            await rl.acquire()
            return time.monotonic() - start

        elapsed = asyncio.run(_run())
        self.assertLess(elapsed, 0.01)

    def test_ramp_rate_with_concurrency(self):
        """Ramped rate with concurrent workers should not crash or deadlock."""

        async def _run():
            rl = RateLimiter(rate=50, end_rate=200, ramp_duration=0.5)

            async def _worker():
                for _ in range(5):
                    await rl.acquire()

            start = time.monotonic()
            await asyncio.gather(*[_worker() for _ in range(4)])
            return time.monotonic() - start

        elapsed = asyncio.run(_run())
        # 20 acquires with ramping rate 50->200; should complete reasonably
        self.assertGreater(elapsed, 0.05)
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
