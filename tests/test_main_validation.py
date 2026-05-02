"""Tests for main.py CLI validation paths and mode dispatch.

Targets uncovered lines in main.py validation functions:
_validate_url_and_mode, _validate_load_params, _validate_rate_and_traffic,
_resolve_body, _parse_tags_and_thresholds, _parse_and_validate_args,
_determine_and_run_mode, _run_har_import, and main().
"""

import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

from pywrkr.main import (
    _build_har_import_parser,
    _build_parser,
    _parse_and_validate_args,
    _run_har_import,
    main,
    parse_header,
)


def _parse(cli_args: list[str]):
    """Helper: parse CLI args and run validation, returning (config, args)."""
    parser = _build_parser()
    args = parser.parse_args(cli_args)
    return _parse_and_validate_args(parser, args)


# ---------------------------------------------------------------------------
# parse_header edge case
# ---------------------------------------------------------------------------


class TestParseHeader:
    def test_valid_header(self):
        name, value = parse_header("Content-Type: application/json")
        assert name == "Content-Type"
        assert value == "application/json"

    def test_header_no_colon_raises(self):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError, match="Invalid header"):
            parse_header("InvalidHeaderNoColon")


# ---------------------------------------------------------------------------
# _validate_url_and_mode error paths
# ---------------------------------------------------------------------------


class TestValidateUrlAndMode:
    def test_worker_invalid_port(self):
        """Line 529-530: --worker with non-integer port."""
        with pytest.raises(SystemExit):
            _parse(["--worker", "host:notaport"])

    def test_master_requires_url(self):
        """Line 549-550: --master without URL."""
        with pytest.raises(SystemExit):
            _parse(["--master", "--expect-workers", "2"])

    def test_url_required_without_scenario_or_url_file(self):
        """Line 554: no url, no url-file, no scenario."""
        with pytest.raises(SystemExit):
            _parse([])

    def test_invalid_url_scheme(self):
        """Line 559: non-http(s) scheme."""
        with pytest.raises(SystemExit):
            _parse(["ftp://example.com/"])


# ---------------------------------------------------------------------------
# _validate_load_params error paths
# ---------------------------------------------------------------------------


class TestValidateLoadParams:
    def test_connections_zero(self):
        """Line 568: connections < 1."""
        with pytest.raises(SystemExit):
            _parse(["-c", "0", "http://localhost/"])

    def test_threads_zero(self):
        """Line 570: threads < 1."""
        with pytest.raises(SystemExit):
            _parse(["-t", "0", "http://localhost/"])

    def test_duration_zero(self):
        """Line 572: duration <= 0."""
        with pytest.raises(SystemExit):
            _parse(["-d", "0", "http://localhost/"])

    def test_duration_negative(self):
        with pytest.raises(SystemExit):
            _parse(["-d", "-5", "http://localhost/"])

    def test_num_requests_zero(self):
        """Line 574: num_requests < 1."""
        with pytest.raises(SystemExit):
            _parse(["-n", "0", "http://localhost/"])

    def test_user_sim_with_num_requests(self):
        """Line 586: -u with -n is invalid."""
        with pytest.raises(SystemExit):
            _parse(["-u", "10", "-n", "100", "http://localhost/"])

    def test_user_sim_without_duration(self):
        """Line 588: -u without -d."""
        with pytest.raises(SystemExit):
            _parse(["-u", "10", "http://localhost/"])

    def test_num_requests_and_duration_together(self):
        """Line 590: both -n and -d."""
        with pytest.raises(SystemExit):
            _parse(["-n", "100", "-d", "10", "http://localhost/"])

    def test_timeout_zero_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--timeout", "0", "http://localhost/"])

    def test_timeout_negative_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--timeout", "-5", "http://localhost/"])

    def test_ramp_up_negative_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--ramp-up", "-1", "-d", "10", "http://localhost/"])

    def test_think_time_negative_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--think-time", "-0.5", "-u", "1", "-d", "5", "http://localhost/"])

    def test_think_jitter_above_one_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--think-jitter", "1.5", "-u", "1", "-d", "5", "http://localhost/"])

    def test_think_jitter_negative_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--think-jitter", "-0.1", "-u", "1", "-d", "5", "http://localhost/"])


# ---------------------------------------------------------------------------
# _validate_rate_and_traffic error paths
# ---------------------------------------------------------------------------


class TestValidateRateAndTraffic:
    def test_rate_ramp_without_rate(self):
        """Line 612: --rate-ramp without --rate."""
        with pytest.raises(SystemExit):
            _parse(["--rate-ramp", "200", "-d", "10", "http://localhost/"])

    def test_rate_ramp_without_duration(self):
        """Line 614: --rate-ramp without -d."""
        with pytest.raises(SystemExit):
            _parse(["--rate", "100", "--rate-ramp", "200", "-n", "100", "http://localhost/"])

    def test_rate_ramp_zero_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--rate", "100", "--rate-ramp", "0", "-d", "10", "http://localhost/"])

    def test_rate_ramp_negative_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--rate", "100", "--rate-ramp", "-50", "-d", "10", "http://localhost/"])

    def test_traffic_profile_without_rate(self):
        """Line 617-618: --traffic-profile without --rate."""
        with pytest.raises(SystemExit):
            _parse(["--traffic-profile", "sine", "-d", "10", "http://localhost/"])

    def test_traffic_profile_without_duration(self):
        """Line 619-620: --traffic-profile without -d."""
        with pytest.raises(SystemExit):
            _parse(["--traffic-profile", "sine", "--rate", "100", "-n", "100", "http://localhost/"])

    def test_traffic_profile_with_rate_ramp(self):
        """Line 621-622: --traffic-profile combined with --rate-ramp."""
        with pytest.raises(SystemExit):
            _parse(
                [
                    "--traffic-profile",
                    "sine",
                    "--rate",
                    "100",
                    "--rate-ramp",
                    "200",
                    "-d",
                    "10",
                    "http://localhost/",
                ]
            )

    def test_invalid_traffic_profile(self):
        """Line 624-626: bad --traffic-profile value."""
        with pytest.raises(SystemExit):
            _parse(
                [
                    "--traffic-profile",
                    "invalid_profile_name",
                    "--rate",
                    "100",
                    "-d",
                    "10",
                    "http://localhost/",
                ]
            )

    def test_valid_traffic_profile_sine(self):
        """Valid sine traffic profile parses correctly."""
        config, _ = _parse(
            [
                "--traffic-profile",
                "sine",
                "--rate",
                "100",
                "-d",
                "10",
                "http://localhost/",
            ]
        )
        assert config.traffic_profile is not None
        assert "sine" in config.traffic_profile.describe()


# ---------------------------------------------------------------------------
# _resolve_body
# ---------------------------------------------------------------------------


class TestResolveBody:
    def test_post_file_not_found(self):
        """Line 636: --post-file with nonexistent file."""
        with pytest.raises(SystemExit):
            _parse(["--post-file", "/nonexistent/file.txt", "http://localhost/"])

    def test_post_file_valid(self):
        """Line 637-638: --post-file with valid file."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
            f.write(b"test body content")
            path = f.name
        try:
            config, _ = _parse(["--post-file", path, "-m", "POST", "http://localhost/"])
            assert config.body == b"test body content"
        finally:
            os.unlink(path)

    def test_body_flag(self):
        """Line 640: --body flag."""
        config, _ = _parse(["--body", "hello world", "-m", "POST", "http://localhost/"])
        assert config.body == b"hello world"


# ---------------------------------------------------------------------------
# _parse_tags_and_thresholds
# ---------------------------------------------------------------------------


class TestParseTagsAndThresholds:
    def test_invalid_tag_format(self):
        """Line 652: tag without '='."""
        with pytest.raises(SystemExit):
            _parse(["--tag", "invalidtag", "http://localhost/"])

    def test_valid_tags(self):
        """Line 653-654: valid tags."""
        config, _ = _parse(
            [
                "--tag",
                "env=prod",
                "--tag",
                "team=platform",
                "http://localhost/",
            ]
        )
        assert config.tags == {"env": "prod", "team": "platform"}

    def test_valid_threshold(self):
        """Line 658-661: valid threshold."""
        config, _ = _parse(
            [
                "--threshold",
                "p95<500ms",
                "http://localhost/",
            ]
        )
        assert len(config.thresholds) == 1
        assert config.thresholds[0].metric == "p95"

    def test_invalid_threshold(self):
        """Line 660-661: invalid threshold expression."""
        with pytest.raises(SystemExit):
            _parse(["--threshold", "not_a_threshold", "http://localhost/"])


# ---------------------------------------------------------------------------
# Scenario loading via CLI
# ---------------------------------------------------------------------------


class TestScenarioValidation:
    def test_scenario_with_base_url_fallback(self):
        """Line 692-693: scenario with base_url fills in args.url."""
        scenario_data = {
            "base_url": "http://localhost:8080",
            "steps": [{"path": "/api/test"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(scenario_data, f)
            path = f.name
        try:
            config, _ = _parse(["--scenario", path])
            assert config.url == "http://localhost:8080"
        finally:
            os.unlink(path)

    def test_scenario_without_base_url_or_positional_url(self):
        """Line 694-695: scenario without base_url and no URL arg."""
        scenario_data = {
            "steps": [{"path": "/api/test"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(scenario_data, f)
            path = f.name
        try:
            with pytest.raises(SystemExit):
                _parse(["--scenario", path])
        finally:
            os.unlink(path)

    def test_scenario_default_duration(self):
        """Line 755: scenario mode defaults to 10s when no duration/count."""
        scenario_data = {
            "base_url": "http://localhost:8080",
            "steps": [{"path": "/api/test"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(scenario_data, f)
            path = f.name
        try:
            config, _ = _parse(["--scenario", path])
            assert config.duration == 10.0
        finally:
            os.unlink(path)

    def test_invalid_scenario_file(self):
        """Line 687-688: invalid scenario JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            path = f.name
        try:
            with pytest.raises(SystemExit):
                _parse(["--scenario", path])
        finally:
            os.unlink(path)

    def test_scenario_file_not_found(self):
        """Line 685-688: missing scenario file."""
        with pytest.raises(SystemExit):
            _parse(["--scenario", "/nonexistent/scenario.json"])


# ---------------------------------------------------------------------------
# _determine_and_run_mode (mocked execution)
# ---------------------------------------------------------------------------


class TestDetermineAndRunMode:
    def test_url_file_validation_passes(self):
        """Line 536-543: valid url-file passes validation."""
        url_entries = "http://localhost/a\nhttp://localhost/b\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(url_entries)
            path = f.name
        try:
            config, args = _parse(["--url-file", path])
            assert args.url_file == path
        finally:
            os.unlink(path)

    def test_url_file_invalid_scheme(self):
        """Line 542-543: url-file with non-http URL."""
        url_entries = "ftp://localhost/a\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(url_entries)
            path = f.name
        try:
            with pytest.raises(SystemExit):
                _parse(["--url-file", path])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# _run_har_import
# ---------------------------------------------------------------------------


class TestRunHarImport:
    def test_har_import_to_stdout(self):
        """Line 802-828: har-import outputs to stdout."""
        har_data = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "GET",
                            "url": "http://example.com/api/test",
                            "headers": [],
                        },
                        "response": {"status": 200},
                    }
                ]
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".har", delete=False) as f:
            json.dump(har_data, f)
            path = f.name
        try:
            parser = _build_har_import_parser()
            args = parser.parse_args([path, "--format", "url-file"])
            # Should not raise
            with patch("builtins.print") as mock_print:
                _run_har_import(args)
                assert mock_print.called
        finally:
            os.unlink(path)

    def test_har_import_to_file(self):
        """Line 824-826: har-import writes to output file."""
        har_data = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "GET",
                            "url": "http://example.com/api/test",
                            "headers": [],
                        },
                        "response": {"status": 200},
                    }
                ]
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".har", delete=False) as f:
            json.dump(har_data, f)
            har_path = f.name

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            out_path = f.name
        try:
            parser = _build_har_import_parser()
            args = parser.parse_args([har_path, "--format", "url-file", "-o", out_path])
            with patch("builtins.print") as mock_print:
                _run_har_import(args)
                # Should print confirmation message
                assert any("Wrote" in str(call) for call in mock_print.call_args_list)
            assert os.path.exists(out_path)
        finally:
            os.unlink(har_path)
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_har_import_file_not_found(self):
        """Line 820-822: har-import with nonexistent file."""
        parser = _build_har_import_parser()
        args = parser.parse_args(["/nonexistent/file.har", "--format", "url-file"])
        with pytest.raises(SystemExit):
            _run_har_import(args)


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_har_import_subcommand(self):
        """Line 839-843: main() dispatches to har-import subcommand."""
        har_data = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "GET",
                            "url": "http://example.com/api",
                            "headers": [],
                        },
                        "response": {"status": 200},
                    }
                ]
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".har", delete=False) as f:
            json.dump(har_data, f)
            har_path = f.name
        try:
            argv = ["pywrkr", "har-import", har_path, "--format", "url-file"]
            with patch.object(sys, "argv", argv):
                with patch("builtins.print"):
                    main()
        finally:
            os.unlink(har_path)

    def test_main_no_args_exits(self):
        """Line 845-848: main() with no args triggers parser error."""
        with patch.object(sys, "argv", ["pywrkr"]):
            with pytest.raises(SystemExit):
                main()


# ---------------------------------------------------------------------------
# Default duration behavior
# ---------------------------------------------------------------------------


class TestDefaultDuration:
    def test_default_duration_when_no_mode_specified(self):
        """When neither -d, -n, nor -u is given, defaults to DEFAULT_DURATION."""
        config, _ = _parse(["http://localhost/"])
        assert config.duration is not None  # Should use default (10s)

    def test_num_requests_mode_no_default_duration(self):
        """With -n, duration should remain None."""
        config, _ = _parse(["-n", "100", "http://localhost/"])
        assert config.duration is None
        assert config.num_requests == 100


# ---------------------------------------------------------------------------
# Master mode validation
# ---------------------------------------------------------------------------


class TestMasterMode:
    def test_master_without_expect_workers(self):
        """Line 547-548: --master without --expect-workers."""
        with pytest.raises(SystemExit):
            _parse(["--master", "http://localhost/"])

    def test_master_with_zero_workers(self):
        """--master with --expect-workers 0."""
        with pytest.raises(SystemExit):
            _parse(["--master", "--expect-workers", "0", "http://localhost/"])
