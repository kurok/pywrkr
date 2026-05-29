"""Regression tests for confirmed CLI-layer defects in src/pywrkr/main.py.

Each test targets a specific audit finding (cli-1 .. cli-9) and is written to
FAIL against the pre-fix code and PASS after the fix.
"""

import argparse
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pywrkr.config import StepResult
from pywrkr.main import (
    _build_parser,
    _determine_and_run_mode,
    _parse_and_validate_args,
)


def _parse_and_validate(argv):
    """Run the full parse+validate pipeline on argv (no execution)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _parse_and_validate_args(parser, args)


# ---------------------------------------------------------------------------
# cli-1: --autofind silently drops request-shaping flags
# ---------------------------------------------------------------------------


class TestAutofindRejectsRequestShaping(unittest.TestCase):
    """cli-1: --autofind combined with -m/-H/-b/-p/-A/-C/-l must error, not
    silently capacity-test a plain GET."""

    def _assert_rejected(self, extra_argv):
        argv = ["--autofind", "http://127.0.0.1/", *extra_argv]
        with self.assertRaises(SystemExit) as ctx:
            _parse_and_validate(argv)
        self.assertEqual(ctx.exception.code, 2)

    def test_autofind_with_method_rejected(self):
        self._assert_rejected(["-m", "POST"])

    def test_autofind_with_body_rejected(self):
        self._assert_rejected(["-b", '{"k":1}'])

    def test_autofind_with_header_rejected(self):
        self._assert_rejected(["-H", "X-Token: 1"])

    def test_autofind_with_basic_auth_rejected(self):
        self._assert_rejected(["-A", "user:pass"])

    def test_autofind_with_cookie_rejected(self):
        self._assert_rejected(["-C", "session=abc"])

    def test_autofind_with_verify_length_rejected(self):
        self._assert_rejected(["-l"])

    def test_plain_autofind_get_still_accepted(self):
        # A bare autofind GET must remain valid (no false positive).
        config, args = _parse_and_validate(["--autofind", "http://127.0.0.1/"])
        self.assertTrue(args.autofind)


# ---------------------------------------------------------------------------
# cli-2: --autofind ignores thresholds and always exits 0
# ---------------------------------------------------------------------------


def _step(users, passed):
    return StepResult(
        users=users,
        rps=1.0,
        p50=0.01,
        p95=0.02,
        p99=0.03,
        error_rate=0.0,
        total_requests=10,
        total_errors=0,
        passed=passed,
    )


class TestAutofindExitCode(unittest.TestCase):
    """cli-2: autofind must exit non-zero when no sustainable load is found."""

    def _make_autofind_args(self):
        return argparse.Namespace(
            url="http://127.0.0.1/",
            url_file=None,
            master=False,
            autofind=True,
            bind="0.0.0.0",
            port=9220,
            expect_workers=None,
            max_error_rate=1.0,
            max_p95=5.0,
            step_duration=1.0,
            start_users=1,
            max_users=10,
            step_multiplier=2.0,
            think_time=0.0,
            think_jitter=0.5,
            random_param=False,
            timeout=5,
            json=None,
        )

    @patch("pywrkr.main.run_autofind", new_callable=AsyncMock)
    def test_no_sustainable_load_exits_nonzero(self, mock_af):
        from pywrkr.config import BenchmarkConfig

        mock_af.return_value = [_step(1, False), _step(2, False)]
        config = BenchmarkConfig(url="http://127.0.0.1/", duration=5)
        with self.assertRaises(SystemExit) as ctx:
            _determine_and_run_mode(config, self._make_autofind_args())
        self.assertNotEqual(ctx.exception.code, 0)

    @patch("pywrkr.main.run_autofind", new_callable=AsyncMock)
    def test_sustainable_load_exits_zero(self, mock_af):
        from pywrkr.config import BenchmarkConfig

        mock_af.return_value = [_step(1, True), _step(2, False)]
        config = BenchmarkConfig(url="http://127.0.0.1/", duration=5)
        with self.assertRaises(SystemExit) as ctx:
            _determine_and_run_mode(config, self._make_autofind_args())
        self.assertEqual(ctx.exception.code, 0)

    def test_threshold_with_autofind_rejected(self):
        # --threshold cannot be honored in autofind mode; reject rather than
        # silently ignore.
        with self.assertRaises(SystemExit) as ctx:
            _parse_and_validate(["--autofind", "--threshold", "p95 < 300ms", "http://127.0.0.1/"])
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# cli-4: scenario base_url with a non-http(s) scheme bypasses validation
# ---------------------------------------------------------------------------


class TestScenarioBaseUrlScheme(unittest.TestCase):
    """cli-4: a scenario base_url with ftp:// (etc.) must be rejected."""

    def _write_scenario(self, base_url):
        f = tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False)
        f.write('{"name":"s","base_url":"%s","steps":[{"path":"/a"}]}' % base_url)
        f.flush()
        f.close()
        return f.name

    def test_ftp_base_url_rejected(self):
        path = self._write_scenario("ftp://evil/")
        with self.assertRaises(SystemExit) as ctx:
            _parse_and_validate(["--scenario", path, "-d", "1"])
        self.assertEqual(ctx.exception.code, 2)

    def test_http_base_url_accepted(self):
        path = self._write_scenario("http://127.0.0.1/")
        config, args = _parse_and_validate(["--scenario", path, "-d", "1"])
        self.assertEqual(args.url, "http://127.0.0.1/")


# ---------------------------------------------------------------------------
# cli-5: `-u 0` not validated
# ---------------------------------------------------------------------------


class TestUsersValidation(unittest.TestCase):
    """cli-5: -u 0 (and negative) must be rejected like -c 0 / -n 0."""

    def test_users_zero_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            _parse_and_validate(["-u", "0", "-d", "2", "http://127.0.0.1/"])
        self.assertEqual(ctx.exception.code, 2)

    def test_users_negative_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            _parse_and_validate(["-u", "-3", "-d", "2", "http://127.0.0.1/"])
        self.assertEqual(ctx.exception.code, 2)

    def test_users_one_accepted(self):
        config, args = _parse_and_validate(["-u", "1", "-d", "2", "http://127.0.0.1/"])
        self.assertEqual(config.users, 1)


# ---------------------------------------------------------------------------
# cli-6: conflicting mode flags must be rejected, not silently resolved
# ---------------------------------------------------------------------------


class TestModeConflicts(unittest.TestCase):
    """cli-6: incompatible mode-flag combinations must error (exit 2)."""

    def _write_url_file(self):
        f = tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False)
        f.write("http://127.0.0.1/a\n")
        f.flush()
        f.close()
        return f.name

    def _assert_conflict(self, argv):
        with self.assertRaises(SystemExit) as ctx:
            _parse_and_validate(argv)
        self.assertEqual(ctx.exception.code, 2)

    def test_autofind_with_num_requests(self):
        self._assert_conflict(["--autofind", "-n", "100", "http://127.0.0.1/"])

    def test_autofind_with_rate(self):
        self._assert_conflict(["--autofind", "--rate", "5", "http://127.0.0.1/"])

    def test_autofind_with_users(self):
        self._assert_conflict(["--autofind", "-u", "7", "http://127.0.0.1/"])

    def test_autofind_with_duration(self):
        self._assert_conflict(["--autofind", "-d", "5", "http://127.0.0.1/"])

    def test_master_with_autofind(self):
        self._assert_conflict(
            ["--master", "--expect-workers", "2", "--autofind", "http://127.0.0.1/"]
        )

    def test_url_file_with_master(self):
        path = self._write_url_file()
        self._assert_conflict(
            ["--url-file", path, "--master", "--expect-workers", "2", "http://127.0.0.1/"]
        )

    def test_url_file_with_autofind(self):
        path = self._write_url_file()
        self._assert_conflict(["--url-file", path, "--autofind"])


# ---------------------------------------------------------------------------
# cli-7: -d/-n help text must agree with mutually-exclusive behavior
# ---------------------------------------------------------------------------


class TestDurationNumRequestsHelp(unittest.TestCase):
    """cli-7: help text must state -d/-n are mutually exclusive (no override)."""

    def _help_for(self, option):
        parser = _build_parser()
        for action in parser._actions:
            if option in action.option_strings:
                return action.help
        # raise (not self.fail) so this path is a clear no-return, avoiding a
        # mix of explicit and implicit (fall-through) returns.
        raise AssertionError(f"option {option} not found")

    def test_duration_help_states_mutual_exclusion(self):
        text = self._help_for("-d")
        self.assertIn("mutually exclusive", text)
        self.assertNotIn("ignored if -n", text)

    def test_num_requests_help_no_longer_claims_override(self):
        text = self._help_for("-n")
        self.assertIn("mutually exclusive", text)
        self.assertNotIn("overrides -d", text)

    def test_both_still_rejected(self):
        # Behavior unchanged: passing both is still an error.
        with self.assertRaises(SystemExit) as ctx:
            _parse_and_validate(["-n", "10", "-d", "5", "http://127.0.0.1/"])
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# cli-8: -k/--keepalive must be a real opposite of --no-keepalive
# ---------------------------------------------------------------------------


class TestKeepaliveFlag(unittest.TestCase):
    """cli-8: -k must override --no-keepalive (last-flag-wins), not be dead."""

    def test_k_overrides_no_keepalive(self):
        parser = _build_parser()
        args = parser.parse_args(["http://127.0.0.1/", "--no-keepalive", "-k"])
        self.assertTrue(args.keepalive)

    def test_no_keepalive_after_k_disables(self):
        parser = _build_parser()
        args = parser.parse_args(["http://127.0.0.1/", "-k", "--no-keepalive"])
        self.assertFalse(args.keepalive)

    def test_default_keepalive_on(self):
        parser = _build_parser()
        args = parser.parse_args(["http://127.0.0.1/"])
        self.assertTrue(args.keepalive)

    def test_no_keepalive_propagates_to_config(self):
        config, _ = _parse_and_validate(["http://127.0.0.1/", "--no-keepalive"])
        self.assertFalse(config.keepalive)


# ---------------------------------------------------------------------------
# cli-9: url-file parsed exactly once per invocation
# ---------------------------------------------------------------------------


class TestUrlFileParsedOnce(unittest.TestCase):
    """cli-9: load_url_file must be invoked once across validate + dispatch."""

    @patch("pywrkr.main.run_multi_url", new_callable=AsyncMock)
    @patch("pywrkr.main.load_url_file")
    def test_url_file_loaded_once(self, mock_load, mock_multi):
        from unittest.mock import MagicMock

        from pywrkr.multi_url import UrlEntry

        entry = UrlEntry(url="http://127.0.0.1/a", method="GET")
        mock_load.return_value = [entry]
        result = MagicMock()
        result.exit_code = 0
        mock_multi.return_value = [result]

        parser = _build_parser()
        args = parser.parse_args(["--url-file", "urls.txt", "-n", "1"])
        config, args = _parse_and_validate_args(parser, args)
        with self.assertRaises(SystemExit):
            _determine_and_run_mode(config, args)

        self.assertEqual(mock_load.call_count, 1)


if __name__ == "__main__":
    unittest.main()
