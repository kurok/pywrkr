"""Regression tests for audited defects in pywrkr.traffic_profiles.

Each test targets one confirmed finding and would fail on the pre-fix code.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from pywrkr.traffic_profiles import (
    CsvProfile,
    RateLimiter,
    SpikeProfile,
    StepProfile,
    parse_traffic_profile,
)

# ---------------------------------------------------------------------------
# cli-3: parse_traffic_profile converts TypeError from bad params into ValueError
# ---------------------------------------------------------------------------


def test_cli3_unknown_param_raises_valueerror():
    # Unknown keyword for a profile that takes params -> TypeError on old code.
    with pytest.raises(ValueError, match="Invalid parameter"):
        parse_traffic_profile("sine:foo=1")


def test_cli3_param_for_parameterless_profile_raises_valueerror():
    # business-hours takes no params; passing one raised TypeError on old code.
    with pytest.raises(ValueError, match="Invalid parameter"):
        parse_traffic_profile("business-hours:cycles=3")


# ---------------------------------------------------------------------------
# tp-1: CsvProfile rejects non-monotonic (unsorted) time columns
# ---------------------------------------------------------------------------


def test_tp1_unsorted_times_raise(tmp_path):
    csv_file = tmp_path / "unsorted.csv"
    csv_file.write_text("time_sec,rate\n60,600\n0,100\n30,300\n")
    with pytest.raises(ValueError, match="non-decreasing"):
        CsvProfile(str(csv_file))


def test_tp1_sorted_times_interpolate_correctly(tmp_path):
    # Sanity: properly ordered CSV still interpolates as expected.
    csv_file = tmp_path / "sorted.csv"
    csv_file.write_text("time_sec,rate\n0,100\n30,300\n60,600\n")
    profile = CsvProfile(str(csv_file))
    # elapsed=15 is halfway between (0,100) and (30,300) -> 200.0
    assert profile.rate_at(15, 60, 1.0) == 200.0


def test_tp1_single_point_csv(tmp_path):
    csv_file = tmp_path / "single.csv"
    csv_file.write_text("time_sec,rate\n0,100\n")
    profile = CsvProfile(str(csv_file))
    assert profile.rate_at(0, 60, 1.0) == 100.0
    assert profile.rate_at(100, 60, 1.0) == 100.0


def test_tp1_duplicate_timestamps_allowed(tmp_path):
    # "non-decreasing" permits equal adjacent timestamps; this must NOT raise,
    # and rate_at on the duplicate exercises the t1==t0 (frac=0) branch.
    csv_file = tmp_path / "dup.csv"
    csv_file.write_text("time_sec,rate\n0,100\n30,300\n30,500\n60,600\n")
    profile = CsvProfile(str(csv_file))
    # elapsed exactly on the duplicated timestamp returns a defined value,
    # not a ZeroDivisionError.
    assert profile.rate_at(30, 60, 1.0) in (300.0, 500.0)


# ---------------------------------------------------------------------------
# tp-2: CsvProfile gives an informative error on non-numeric cells
# ---------------------------------------------------------------------------


def test_tp2_non_numeric_cell_informative_error(tmp_path):
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("time_sec,rate\n0,100\n30,oops\n60,200\n")
    with pytest.raises(ValueError, match="Invalid numeric value") as exc:
        CsvProfile(str(csv_file))
    # Includes file path and the offending line number (line 3 in the file).
    msg = str(exc.value)
    assert "bad.csv" in msg
    assert "line 3" in msg


def test_tp2_no_header_line_number(tmp_path):
    # No header row: data starts at file line 1, so the bad cell is line 2.
    # Guards the enumerate(start=start+1) offset for the start==0 branch.
    csv_file = tmp_path / "noheader.csv"
    csv_file.write_text("0,100\n30,oops\n60,200\n")
    with pytest.raises(ValueError, match="Invalid numeric value") as exc:
        CsvProfile(str(csv_file))
    assert "line 2" in str(exc.value)


# ---------------------------------------------------------------------------
# tp-3: SpikeProfile validates multiplier / baseline / spike_dur
# ---------------------------------------------------------------------------


def test_tp3_negative_multiplier_raises():
    with pytest.raises(ValueError, match="multiplier"):
        SpikeProfile(multiplier=-1)


def test_tp3_negative_baseline_raises():
    with pytest.raises(ValueError, match="baseline"):
        SpikeProfile(baseline=-0.1)


def test_tp3_zero_spike_dur_raises():
    with pytest.raises(ValueError, match="spike_dur"):
        SpikeProfile(spike_dur=0)


def test_tp3_valid_spike_still_constructs():
    profile = SpikeProfile(interval=60, spike_dur=2, multiplier=5, baseline=0.2)
    assert profile.rate_at(0, 60, 100) == 500.0
    assert profile.rate_at(5, 60, 100) == 20.0


# ---------------------------------------------------------------------------
# tp-4: StepProfile clamps negative elapsed to the first level
# ---------------------------------------------------------------------------


def test_tp4_negative_elapsed_returns_first_level():
    # int(-30/60*3) == int(-1.5) == -1 -> levels[-1]=300 on old code (wraparound).
    profile = StepProfile(levels=[100, 200, 300])
    assert profile.rate_at(-30, 60, 1.0) == 100.0


# ---------------------------------------------------------------------------
# tp-5: RateLimiter pauses (does not unthrottle) on a 0-rate phase
# ---------------------------------------------------------------------------


def test_tp5_zero_rate_phase_pauses_not_unthrottled():
    async def run():
        # Step profile: first level is 0 RPS -> acquire() must pause, not pass through.
        profile = StepProfile(levels=[0.0, 1000.0])
        rl = RateLimiter(rate=1000, traffic_profile=profile, duration=60)
        # First call only seeds the start time.
        await rl.acquire()
        # Drive several acquires while still in the 0-RPS phase (elapsed << 30s).
        start = time.monotonic()
        for _ in range(3):
            await rl.acquire()
        elapsed = time.monotonic() - start
        # On old code each acquire took the rate<=0 early return with no sleep,
        # so waits stayed 0 and elapsed was ~0.  Now each pauses a bounded quantum.
        assert rl.waits > 0
        assert elapsed > 0

    asyncio.run(run())


def test_tp5_zero_rate_pause_never_sleeps_past_test_end():
    # When the test has already run past `duration`, the bounded pause must
    # clamp to <= 0 and NOT sleep (otherwise a 0-rate phase at the very end
    # would block for the full 0.1s quantum past test end).
    async def run():
        profile = StepProfile(levels=[0.0, 1000.0])
        rl = RateLimiter(rate=1000, traffic_profile=profile, duration=1)
        # Seed start time well in the past so elapsed >> duration.
        rl._start_time = time.monotonic() - 5.0
        rl._last_time = rl._start_time
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start
        # remaining = duration - elapsed < 0 -> pause clamped, no real sleep.
        assert elapsed < 0.05

    asyncio.run(run())
