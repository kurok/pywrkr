"""Tests for the CI summary generator and the GitHub Action that calls it.

The action itself is YAML, so the parts worth testing were pushed into
:mod:`pywrkr.ci`: what the markdown says, what the gate decides, and whether a
second run edits its comment or posts a new one. The YAML is checked
structurally here and exercised end-to-end by ``.github/workflows/action-test.yml``.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import yaml

from pywrkr import ci
from pywrkr.compare import EXIT_REGRESSION, compare_results, parse_fail_on
from pywrkr.main import _build_summary_parser, _run_summary
from pywrkr.reporting import parse_threshold

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_results(**overrides) -> dict:
    """A results dict shaped like ``build_results_dict`` output."""
    results = {
        "duration_sec": 10.0,
        "total_requests": 1000,
        "total_errors": 10,
        "total_bytes": 512000,
        "requests_per_sec": 100.0,
        "transfer_per_sec_bytes": 51200.0,
        "latency": {"min": 0.001, "max": 0.5, "mean": 0.05, "median": 0.04, "stdev": 0.02},
        "percentiles": {"p50": 0.04, "p75": 0.06, "p90": 0.09, "p95": 0.12, "p99": 0.3},
        "status_codes": {"200": 990},
        "error_types": {"timeout": 10},
        "config": {
            "url_host": "api.example.com",
            "mode": "duration",
            "connections": 20,
            "duration": 10.0,
        },
    }
    results.update(overrides)
    return results


class TestEvaluateFromResults(unittest.TestCase):
    def test_a_satisfied_threshold_passes(self):
        outcomes = ci.evaluate_from_results(make_results(), [parse_threshold("p95 < 500ms")])
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].passed)
        self.assertAlmostEqual(outcomes[0].actual, 0.12)

    def test_a_breached_threshold_fails(self):
        outcomes = ci.evaluate_from_results(make_results(), [parse_threshold("p95 < 50ms")])
        self.assertFalse(outcomes[0].passed)

    def test_every_operator_is_honoured(self):
        results = make_results()
        cases = [
            ("rps > 50", True),
            ("rps > 500", False),
            ("rps >= 100", True),
            ("p95 <= 120ms", True),
            ("p95 < 120ms", False),
        ]
        for expr, expected in cases:
            with self.subTest(expr=expr):
                outcome = ci.evaluate_from_results(results, [parse_threshold(expr)])[0]
                self.assertIs(outcome.passed, expected)

    def test_a_missing_metric_fails_rather_than_passing(self):
        """The whole point of the gate: no measurement is not a pass.

        ``reporting.evaluate_thresholds`` used to substitute 0.0 here, making
        ``p95 < 500ms`` green on a run that never got a response. It was fixed
        to match in #213; ``TestBothPathsAgree`` in
        tests/test_thresholds_unmeasured.py holds the two together.
        """
        results = make_results(percentiles={})
        outcome = ci.evaluate_from_results(results, [parse_threshold("p95 < 500ms")])[0]
        self.assertIsNone(outcome.actual)
        self.assertFalse(outcome.passed)

    def test_error_rate_is_read_as_a_percentage(self):
        # 10 errors out of 1000 is 1%, so "< 2%" holds and "< 0.5%" does not.
        results = make_results()
        self.assertTrue(
            ci.evaluate_from_results(results, [parse_threshold("error_rate < 2%")])[0].passed
        )
        self.assertFalse(
            ci.evaluate_from_results(results, [parse_threshold("error_rate < 0.5%")])[0].passed
        )

    def test_no_thresholds_yields_no_outcomes(self):
        self.assertEqual(ci.evaluate_from_results(make_results(), None), [])
        self.assertEqual(ci.evaluate_from_results(make_results(), []), [])


class TestSummaryOutputs(unittest.TestCase):
    def test_outputs_carry_the_headline_numbers(self):
        outputs = ci.summary_outputs(make_results(), [])
        self.assertEqual(outputs["p95"], "0.1200")
        self.assertEqual(outputs["rps"], "100.00")
        self.assertEqual(outputs["error_rate"], "1.0000")
        self.assertEqual(outputs["total_requests"], "1000")

    def test_passed_reflects_the_thresholds(self):
        results = make_results()
        green = ci.evaluate_from_results(results, [parse_threshold("p95 < 500ms")])
        red = ci.evaluate_from_results(results, [parse_threshold("p95 < 5ms")])
        self.assertEqual(ci.summary_outputs(results, green)["passed"], "true")
        self.assertEqual(ci.summary_outputs(results, red)["passed"], "false")

    def test_a_missing_metric_becomes_an_empty_string_not_a_zero(self):
        """An action consumer must be able to tell "no data" from "zero"."""
        outputs = ci.summary_outputs(make_results(percentiles={}), [])
        self.assertEqual(outputs["p95"], "")
        self.assertNotEqual(outputs["rps"], "")

    def test_outputs_are_single_line_and_key_value_safe(self):
        """Anything with a newline would corrupt $GITHUB_OUTPUT."""
        for key, value in ci.summary_outputs(make_results(), []).items():
            with self.subTest(key=key):
                self.assertNotIn("\n", key)
                self.assertNotIn("\n", value)
                self.assertNotIn("=", key)


class TestRenderMarkdown(unittest.TestCase):
    def test_the_golden_report(self):
        """The whole rendered body, pinned.

        A summary is a user interface; an accidental reflow or a dropped row is
        a real regression that no property-style assertion would catch.
        """
        results = make_results()
        outcomes = ci.evaluate_from_results(
            results, [parse_threshold("p95 < 500ms"), parse_threshold("error_rate < 0.5%")]
        )
        expected = "\n".join(
            [
                "## pywrkr performance report",
                "",
                "`api.example.com` · `duration` · `20 connections` · `10s`",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                "| Requests | 1,000 |",
                "| Errors | 10 |",
                "| Error rate | 1.00% |",
                "| Throughput (req/s) | 100.00 |",
                "| Latency p50 | 40.00ms |",
                "| Latency p95 | 120.00ms |",
                "| Latency p99 | 300.00ms |",
                "| Latency max | 500.00ms |",
                "",
                "### Thresholds",
                "",
                "- ✅ `p95 < 500ms` — actual 120.00ms",
                "- ❌ `error_rate < 0.5%` — actual 1.00%",
                "",
                "**Failed:** 1 threshold(s) breached.",
                "",
            ]
        )
        self.assertEqual(ci.render_markdown(results, outcomes), expected)

    def test_the_marker_is_opt_in_and_leads_the_body(self):
        plain = ci.render_markdown(make_results())
        self.assertNotIn(ci.COMMENT_MARKER, plain)
        marked = ci.render_markdown(make_results(), include_marker=True)
        self.assertTrue(marked.startswith(ci.COMMENT_MARKER))

    def test_rows_for_absent_metrics_are_dropped_not_zeroed(self):
        body = ci.render_markdown(make_results(percentiles={}))
        self.assertNotIn("Latency p95", body)
        self.assertIn("Requests", body)

    def test_a_missing_threshold_metric_is_named_as_such(self):
        outcomes = ci.evaluate_from_results(
            make_results(percentiles={}), [parse_threshold("p95 < 500ms")]
        )
        body = ci.render_markdown(make_results(percentiles={}), outcomes)
        self.assertIn("not measured", body)
        self.assertIn("❌", body)

    def test_the_verdict_line_covers_every_combination(self):
        results = make_results()
        green = ci.evaluate_from_results(results, [parse_threshold("p95 < 500ms")])
        red = ci.evaluate_from_results(results, [parse_threshold("p95 < 5ms")])

        self.assertIn("No thresholds or baseline", ci.render_markdown(results))
        self.assertIn("**Passed:**", ci.render_markdown(results, green))
        self.assertIn("1 threshold(s) breached.", ci.render_markdown(results, red))

        baseline = make_results(percentiles={"p95": 0.01})
        comparison = compare_results(baseline, results, [parse_fail_on("p95 > +10%")])
        self.assertTrue(comparison.regressed)
        self.assertIn("regressed against the baseline", ci.render_markdown(results, [], comparison))
        both = ci.render_markdown(results, red, comparison)
        self.assertIn("breached and a regression", both)

    def test_the_comparison_table_is_rendered(self):
        results = make_results()
        baseline = make_results(percentiles={"p95": 0.10, "p50": 0.04, "p99": 0.3})
        comparison = compare_results(baseline, results, [], ["base.json"])
        body = ci.render_markdown(results, [], comparison)
        self.assertIn("### Compared to baseline", body)
        self.assertIn("Baseline: `base.json`", body)
        self.assertIn("| Metric | Baseline | Current | Change |", body)
        self.assertIn("+20.00%", body)  # p95 0.10 -> 0.12

    def test_a_config_mismatch_is_surfaced_as_a_warning(self):
        results = make_results()
        baseline = make_results()
        baseline["config"] = dict(baseline["config"], connections=200)
        comparison = compare_results(baseline, results, [])
        self.assertTrue(comparison.config_warnings)
        body = ci.render_markdown(results, [], comparison)
        self.assertIn("**Warning:** config differs — connections: baseline 200 vs current 20", body)

    def test_the_title_and_target_are_overridable(self):
        body = ci.render_markdown(make_results(), title="Nightly soak", target="staging")
        self.assertIn("## Nightly soak", body)
        self.assertIn("`staging`", body)

    def test_a_user_simulation_reports_users_not_connections(self):
        results = make_results()
        results["config"] = {"url_host": "api.example.com", "mode": "users", "users": 50}
        self.assertIn("50 users", ci.render_markdown(results))

    def test_a_results_file_without_config_still_renders(self):
        results = make_results()
        del results["config"]
        body = ci.render_markdown(results)
        self.assertIn("| Requests | 1,000 |", body)


class TestFindMarkerComment(unittest.TestCase):
    def test_no_marker_means_no_match(self):
        comments = [{"id": 1, "body": "looks good"}, {"id": 2, "body": "ship it"}]
        self.assertIsNone(ci.find_marker_comment(comments))

    def test_the_marker_is_found(self):
        comments = [{"id": 1, "body": "hi"}, {"id": 7, "body": ci.COMMENT_MARKER + "\n## report"}]
        self.assertEqual(ci.find_marker_comment(comments), 7)

    def test_the_newest_marked_comment_wins(self):
        comments = [
            {"id": 3, "body": ci.COMMENT_MARKER + " old"},
            {"id": 9, "body": ci.COMMENT_MARKER + " new"},
        ]
        self.assertEqual(ci.find_marker_comment(comments), 9)

    def test_malformed_comments_are_ignored_rather_than_raising(self):
        comments = [{"body": None}, {"id": "not-an-int", "body": ci.COMMENT_MARKER}, {}]
        self.assertIsNone(ci.find_marker_comment(comments))


class FakeGitHub:
    """Records calls so the upsert decision can be asserted without a network."""

    def __init__(self, existing=None):
        self.existing = existing if existing is not None else []
        self.calls = []

    def __call__(self, method, url, token, payload=None):
        self.calls.append((method, url, payload))
        if method == "GET":
            return self.existing
        return {"id": 42}


class PaginatedGitHub:
    """A GitHub that pages, which is the only kind that exists.

    The simple double returns the same list for every GET, so it cannot
    distinguish "read the first page" from "read all of them".
    """

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, method, url, token, payload=None):
        self.calls.append((method, url, payload))
        if method != "GET":
            return {"id": 42}
        page = 1
        if "page=" in url:
            page = int(url.rsplit("page=", 1)[1].split("&")[0])
        return self.pages[page - 1] if page <= len(self.pages) else []


class NeverEndingGitHub:
    """Always returns a full page, to prove the page loop is bounded.

    Defined at module scope like the other doubles. As a class nested inside a
    test method, CodeQL could not resolve its __call__ and reported
    py/call-to-non-callable against the production call sites it reaches.
    """

    def __init__(self):
        self.gets = 0

    def __call__(self, method, url, token, payload=None):
        if method == "GET":
            self.gets += 1
            return [{"id": i, "body": "x"} for i in range(100)]
        return {"id": 42}


class TestUpsertPrComment(unittest.TestCase):
    def test_marker_found_beyond_first_page(self):
        """GitHub caps per_page at 100 and returns oldest-first.

        On a PR with more than 100 comments the action's own comment -- posted
        early, edited since -- is not on page one, so the single-page search
        never found it and every push posted another. That is the comment spam
        the marker exists to prevent, and it only began once a PR got busy
        enough for anyone to mind.
        """
        page1 = [{"id": i, "body": f"review note {i}"} for i in range(100)]
        page2 = [{"id": 555, "body": ci.COMMENT_MARKER + "\nold numbers"}]
        api = PaginatedGitHub([page1, page2])

        action = ci.upsert_pr_comment("o/r", 5, "## new numbers", token="t", request=api)

        self.assertEqual(action, "updated")
        self.assertEqual([c[0] for c in api.calls], ["GET", "GET", "PATCH"])
        self.assertIn("issues/comments/555", api.calls[-1][1])

    def test_pagination_stops_on_a_short_page(self):
        """A short page means the end; asking for another is a wasted call."""
        api = PaginatedGitHub([[{"id": 1, "body": "unrelated"}]])
        self.assertEqual(ci.upsert_pr_comment("o/r", 5, "x", token="t", request=api), "created")
        self.assertEqual([c[0] for c in api.calls], ["GET", "POST"])

    def test_pagination_is_bounded(self):
        """An API that always returns a full page must not hang the job."""
        api = NeverEndingGitHub()
        self.assertEqual(ci.upsert_pr_comment("o/r", 5, "x", token="t", request=api), "created")
        self.assertLessEqual(api.gets, 100)

    def test_the_first_run_creates_a_comment(self):
        api = FakeGitHub()
        action = ci.upsert_pr_comment("o/r", 5, "## report", token="t", request=api)
        self.assertEqual(action, "created")
        methods = [c[0] for c in api.calls]
        self.assertEqual(methods, ["GET", "POST"])

    def test_a_second_run_edits_instead_of_posting_again(self):
        """Comment spam is the failure mode this whole marker exists to avoid."""
        api = FakeGitHub([{"id": 88, "body": ci.COMMENT_MARKER + "\nold numbers"}])
        action = ci.upsert_pr_comment("o/r", 5, "## new numbers", token="t", request=api)
        self.assertEqual(action, "updated")
        methods = [c[0] for c in api.calls]
        self.assertEqual(methods, ["GET", "PATCH"])
        self.assertIn("issues/comments/88", api.calls[1][1])
        self.assertIn("new numbers", api.calls[1][2]["body"])

    def test_someone_elses_comments_are_left_alone(self):
        api = FakeGitHub([{"id": 1, "body": "unrelated review comment"}])
        self.assertEqual(ci.upsert_pr_comment("o/r", 5, "x", token="t", request=api), "created")

    def test_the_marker_is_added_when_the_body_lacks_it(self):
        api = FakeGitHub()
        ci.upsert_pr_comment("o/r", 5, "## report", token="t", request=api)
        self.assertTrue(api.calls[1][2]["body"].startswith(ci.COMMENT_MARKER))

    def test_an_existing_marker_is_not_duplicated(self):
        api = FakeGitHub()
        ci.upsert_pr_comment("o/r", 5, ci.COMMENT_MARKER + "\n## r", token="t", request=api)
        self.assertEqual(api.calls[1][2]["body"].count(ci.COMMENT_MARKER), 1)

    def test_a_custom_api_url_is_honoured_for_github_enterprise(self):
        api = FakeGitHub()
        ci.upsert_pr_comment(
            "o/r", 5, "x", token="t", api_url="https://ghe.corp/api/v3", request=api
        )
        self.assertTrue(api.calls[0][1].startswith("https://ghe.corp/api/v3/repos/o/r/"))

    def test_a_non_https_api_url_is_refused(self):
        with self.assertRaises(ValueError):
            ci._github_request("GET", "http://api.github.com/x", "token")


class TestLoadResults(unittest.TestCase):
    def test_a_json_object_loads(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"total_requests": 1}, handle)
            path = handle.name
        try:
            self.assertEqual(ci.load_results(path)["total_requests"], 1)
        finally:
            os.unlink(path)

    def test_a_json_array_is_rejected_with_a_useful_message(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump([1, 2], handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError) as ctx:
                ci.load_results(path)
            self.assertIn("expected a JSON object", str(ctx.exception))
        finally:
            os.unlink(path)


class TestSummaryCommand(unittest.TestCase):
    """The `pywrkr summary` subcommand -- the action's only entry point."""

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp()
        self.results = os.path.join(self.dir, "results.json")
        with open(self.results, "w", encoding="utf-8") as handle:
            json.dump(make_results(), handle)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def parse(self, *argv):
        return _build_summary_parser().parse_args([self.results, *argv])

    def test_a_passing_run_exits_zero_and_prints_the_report(self):
        with patch("sys.stdout", new_callable=StringIO) as out:
            _run_summary(self.parse("--threshold", "p95 < 500ms"))
        self.assertIn("**Passed:**", out.getvalue())

    def test_a_breach_exits_two(self):
        with patch("sys.stdout", new_callable=StringIO), self.assertRaises(SystemExit) as ctx:
            _run_summary(self.parse("--threshold", "p95 < 5ms"))
        self.assertEqual(ctx.exception.code, 2)

    def test_a_regression_exits_three(self):
        baseline = os.path.join(self.dir, "baseline.json")
        with open(baseline, "w", encoding="utf-8") as handle:
            json.dump(make_results(percentiles={"p95": 0.01}), handle)
        with patch("sys.stdout", new_callable=StringIO), self.assertRaises(SystemExit) as ctx:
            _run_summary(self.parse("--baseline", baseline, "--fail-on", "p95 > +10%"))
        self.assertEqual(ctx.exception.code, EXIT_REGRESSION)

    def test_output_and_github_output_files_are_written(self):
        report = os.path.join(self.dir, "report.md")
        outputs = os.path.join(self.dir, "gh_output")
        _run_summary(
            self.parse("--threshold", "p95 < 500ms", "--output", report, "--github-output", outputs)
        )
        self.assertIn("## pywrkr performance report", Path(report).read_text(encoding="utf-8"))
        written = dict(
            line.split("=", 1)
            for line in Path(outputs).read_text(encoding="utf-8").splitlines()
            if line
        )
        self.assertEqual(written["passed"], "true")
        self.assertEqual(written["p95"], "0.1200")

    def test_github_output_is_appended_not_truncated(self):
        """$GITHUB_OUTPUT is shared with every other step in the job."""
        outputs = os.path.join(self.dir, "gh_output")
        Path(outputs).write_text("preexisting=1\n", encoding="utf-8")
        with patch("sys.stdout", new_callable=StringIO):
            _run_summary(self.parse("--github-output", outputs))
        self.assertIn("preexisting=1", Path(outputs).read_text(encoding="utf-8"))

    def test_a_bad_threshold_expression_is_a_usage_error(self):
        with (
            patch("sys.stderr", new_callable=StringIO),
            self.assertRaises(SystemExit) as ctx,
        ):
            _run_summary(self.parse("--threshold", "not a threshold"))
        self.assertEqual(ctx.exception.code, 1)

    def test_fail_on_without_a_baseline_is_a_usage_error(self):
        with (
            patch("sys.stderr", new_callable=StringIO) as err,
            self.assertRaises(SystemExit) as ctx,
        ):
            _run_summary(self.parse("--fail-on", "p95 > +10%"))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("--fail-on requires --baseline", err.getvalue())

    def test_a_missing_results_file_is_a_usage_error(self):
        args = _build_summary_parser().parse_args([os.path.join(self.dir, "nope.json")])
        with patch("sys.stderr", new_callable=StringIO), self.assertRaises(SystemExit) as ctx:
            _run_summary(args)
        self.assertEqual(ctx.exception.code, 1)

    def test_the_marker_flag_is_off_by_default(self):
        with patch("sys.stdout", new_callable=StringIO) as out:
            _run_summary(self.parse())
        self.assertNotIn(ci.COMMENT_MARKER, out.getvalue())
        with patch("sys.stdout", new_callable=StringIO) as out:
            _run_summary(self.parse("--marker"))
        self.assertIn(ci.COMMENT_MARKER, out.getvalue())

    def test_baseline_sources_reach_the_report(self):
        baseline = os.path.join(self.dir, "baseline.json")
        with open(baseline, "w", encoding="utf-8") as handle:
            json.dump(make_results(), handle)
        with patch("sys.stdout", new_callable=StringIO) as out:
            _run_summary(self.parse("--baseline", baseline))
        self.assertIn("### Compared to baseline", out.getvalue())


class TestActionDefinition(unittest.TestCase):
    """Structural checks on action.yml -- the parts a typo would silently break."""

    @classmethod
    def setUpClass(cls):
        cls.action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
        cls.steps = cls.action["runs"]["steps"]

    def test_it_is_a_composite_action(self):
        self.assertEqual(self.action["runs"]["using"], "composite")

    def test_every_declared_output_points_at_a_real_step(self):
        step_ids = {s["id"] for s in self.steps if "id" in s}
        for name, spec in self.action["outputs"].items():
            with self.subTest(output=name):
                value = spec["value"]
                self.assertTrue(value.startswith("${{ steps."))
                self.assertIn(value.split(".")[1], step_ids)

    def test_the_summary_outputs_match_what_the_code_emits(self):
        """action.yml and summary_outputs() must not drift apart."""
        emitted = set(ci.summary_outputs(make_results(), []))
        referenced = {
            spec["value"].split("outputs.")[1].rstrip(" }")
            for spec in self.action["outputs"].values()
            if "steps.summary.outputs." in spec["value"]
        }
        # Everything the summary step promises must actually be emitted, other
        # than the two values the YAML computes for itself.
        self.assertTrue(
            (referenced - {"verdict", "summary-file"}).issubset(emitted),
            f"action.yml references outputs pywrkr never writes: {referenced - emitted}",
        )

    def test_no_input_is_interpolated_into_a_run_body(self):
        """A `${{ inputs.x }}` inside `run:` is a shell injection on the consumer."""
        for step in self.steps:
            with self.subTest(step=step["name"]):
                self.assertNotIn("${{ inputs.", step.get("run", ""))

    def test_every_input_reaches_a_step(self):
        used = "".join(json.dumps(step) for step in self.steps)
        for name in self.action["inputs"]:
            with self.subTest(input=name):
                self.assertIn(f"inputs.{name}", used)

    def test_it_pulls_in_no_other_actions(self):
        """Zero `uses:` -- a composite action's dependencies become its consumers'."""
        for step in self.steps:
            self.assertNotIn("uses", step, f"{step.get('name')} depends on another action")

    def test_every_step_script_is_valid_bash(self):
        import subprocess
        import tempfile

        for step in self.steps:
            script = step.get("run")
            if not script:
                continue
            with self.subTest(step=step["name"]):
                with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
                    handle.write(script)
                    path = handle.name
                try:
                    proc = subprocess.run(  # noqa: S603
                        ["bash", "-n", path], capture_output=True, text=True
                    )
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                finally:
                    os.unlink(path)

    def test_the_dogfood_workflow_exercises_the_action(self):
        path = REPO_ROOT / ".github" / "workflows" / "action-test.yml"
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["dogfood"]["steps"]
        self.assertGreaterEqual(sum(1 for s in steps if s.get("uses") == "./"), 3)


if __name__ == "__main__":
    unittest.main()


class TestStepThresholds(unittest.TestCase):
    """`step:<name>` thresholds must read that step's block.

    evaluate_from_results ignored threshold.step and called metric_value on the
    aggregate, which does not resolve step metrics -- so every per-step
    threshold came back actual=None, passed=False. Per-step gating was
    unusable from `pywrkr summary` and the PR comment showed no value.
    """

    RESULTS = {
        "p95": 0.05,
        "step_stats": {
            "checkout": {
                "count": 10,
                "errors": 0,
                "requests_per_sec": 5.0,
                "min": 0.3,
                "max": 0.6,
                "mean": 0.4,
                "median": 0.5,
                "p50": 0.5,
                "p95": 0.5,
                "p99": 0.6,
            }
        },
    }

    def outcome(self, expr: str, results: dict | None = None):
        return ci.evaluate_from_results(results or self.RESULTS, [parse_threshold(expr)])[0]

    def test_step_threshold_reads_step_block(self):
        # The aggregate is well under the bound; the step is well over it.
        out = self.outcome("step:checkout p95 < 100ms")
        self.assertEqual(out.actual, 0.5)
        self.assertFalse(out.passed)

    def test_step_threshold_can_pass(self):
        out = self.outcome("step:checkout p95 < 900ms")
        self.assertEqual(out.actual, 0.5)
        self.assertTrue(out.passed)

    def test_absent_step_is_unmeasured_not_zero(self):
        # A typo in the step name must not read as a satisfied threshold.
        out = self.outcome("step:nosuch p95 < 100ms")
        self.assertIsNone(out.actual)
        self.assertFalse(out.passed)

    def test_metric_the_block_does_not_carry(self):
        # A step with a single sample has no p99 in its block. Absent is not
        # zero, and a threshold on it must not read as satisfied.
        results = {"step_stats": {"checkout": {"count": 1, "errors": 0, "p50": 0.1}}}
        out = self.outcome("step:checkout p99 < 1s", results)
        self.assertIsNone(out.actual)
        self.assertFalse(out.passed)

    def test_step_error_rate_is_derived(self):
        results = {
            "step_stats": {"checkout": {"count": 8, "errors": 2, "p95": 0.1}},
        }
        out = self.outcome("step:checkout error_rate < 5%", results)
        self.assertAlmostEqual(out.actual, 20.0)
        self.assertFalse(out.passed)


class TestDockerfile(unittest.TestCase):
    """The published image is what people actually run.

    These read the Dockerfile rather than the built image, so they catch a
    regression in a unit run; docker-publish.yml asks the built image the same
    questions, which is the check that cannot be fooled by a later layer.
    """

    @property
    def dockerfile(self) -> str:
        root = pathlib.Path(__file__).resolve().parent.parent
        return (root / "Dockerfile").read_text()

    def test_dockerfile_has_user_and_pinned_base(self):
        text = self.dockerfile

        # A load generator opens sockets and writes reports. Neither needs uid 0,
        # and this runs on Fargate.
        self.assertRegex(text, r"(?m)^USER\s+\S+", "the runtime stage must drop root")

        # Every FROM pinned by digest: a floating tag makes the build-provenance
        # attestation attest to inputs that can change underneath it.
        froms = re.findall(r"(?m)^FROM\s+(\S+)", text)
        self.assertTrue(froms, "no FROM found")
        for ref in froms:
            self.assertIn("@sha256:", ref, f"unpinned base image: {ref}")

        # Same for the tool image copied in.
        for ref in re.findall(r"COPY --from=(\S+)", text):
            if "/" in ref:  # a registry reference rather than a build stage
                self.assertIn("@sha256:", ref, f"unpinned COPY --from image: {ref}")

    def test_dockerfile_is_not_on_a_prerelease_interpreter(self):
        # The published image ran python:3.15-rc-alpine, which has no prebuilt
        # musllinux wheels, so aiohttp compiled from source at build time.
        self.assertNotIn("-rc-", self.dockerfile)

    def test_healthcheck_uses_a_command_that_exists(self):
        lines = self.dockerfile.splitlines()
        idx = [i for i, ln in enumerate(lines) if ln.startswith("HEALTHCHECK")]
        self.assertTrue(idx, "no HEALTHCHECK")

        # The directive plus any continuation lines -- scoped deliberately,
        # because the comment above it mentions --version and a whole-file
        # search would match that instead of the command.
        directive = []
        i = idx[0]
        while i < len(lines):
            directive.append(lines[i])
            if not lines[i].rstrip().endswith("\\"):
                break
            i += 1
        command = "\n".join(directive)

        # pywrkr has no --version flag, so that probe would fail on every
        # interval and mark the container permanently unhealthy.
        self.assertNotIn("--version", command, f"unusable health command: {command}")
        self.assertIn("--help", command)


class TestWorkflowHardening(unittest.TestCase):
    """The workflow is the thing that actually gates a merge."""

    @property
    def _root(self) -> pathlib.Path:
        return pathlib.Path(__file__).resolve().parent.parent

    @property
    def workflow(self) -> dict:
        text = (self._root / ".github/workflows/python-package.yml").read_text()
        return yaml.safe_load(text)

    def test_test_job_enforces_coverage_threshold(self):
        """The 85% target lived in codecov.yml and CLAUDE.md and was enforced
        nowhere.

        Codecov's status is not a required check, so a pull request that
        dropped coverage to 60% still merged green. The floor belongs where
        every invocation sees it, not only CI's.
        """
        pyproject = (self._root / "pyproject.toml").read_text()
        match = re.search(r"(?m)^fail_under\s*=\s*(\d+)", pyproject)
        self.assertIsNotNone(match, "no coverage floor in pyproject.toml")
        self.assertGreaterEqual(int(match.group(1)), 80)

        # And scoped to the package: a bare --cov also measures the tests,
        # which put the headline at 98% while src alone was 95%.
        self.assertRegex(pyproject, r'source\s*=\s*\[\s*"src/pywrkr"\s*\]')

    def test_jobs_have_timeouts(self):
        """No job had one, so a hung aiohttp test server burned the 6-hour
        default before anyone noticed."""
        for name, job in self.workflow["jobs"].items():
            with self.subTest(job=name):
                self.assertIn("timeout-minutes", job, f"job {name} has no timeout")
                self.assertLessEqual(job["timeout-minutes"], 60)

    def test_superseded_runs_are_cancelled(self):
        """A superseded push kept its whole matrix running -- six test jobs
        plus five others, producing results nobody would read."""
        concurrency = self.workflow.get("concurrency") or {}
        self.assertTrue(concurrency.get("group"), "no concurrency group")
        self.assertTrue(concurrency.get("cancel-in-progress"))


class TestWorkflowActionPinning(unittest.TestCase):
    """A floating tag is a tag someone else can move."""

    @property
    def _workflows(self) -> list[pathlib.Path]:
        root = pathlib.Path(__file__).resolve().parent.parent
        return sorted((root / ".github" / "workflows").glob("*.yml"))

    def test_all_workflow_actions_are_sha_pinned(self):
        """Half the repository already pinned by SHA; the rest floated.

        publish.yml built its artifact with unpinned actions and then attested
        it, which undercuts the point of the attestation: it records what was
        built, not that the thing which built it was the thing you reviewed.
        """
        self.assertTrue(self._workflows, "no workflows found")
        floating = []
        for path in self._workflows:
            for line_no, line in enumerate(path.read_text().splitlines(), start=1):
                match = re.search(r"uses:\s*(\S+)", line)
                if not match:
                    continue
                ref = match.group(1)
                if ref.startswith("./"):  # a local composite action, not third-party
                    continue
                if not re.search(r"@[0-9a-f]{40}$", ref):
                    floating.append(f"{path.name}:{line_no} {ref}")

        self.assertEqual(floating, [], "actions pinned by mutable tag: " + "; ".join(floating))

    def test_pins_carry_a_version_comment(self):
        """A bare SHA is unreadable; the comment is how anyone knows what
        version they are looking at, and what Dependabot rewrites."""
        missing = []
        for path in self._workflows:
            for line_no, line in enumerate(path.read_text().splitlines(), start=1):
                if re.search(r"uses:\s*[^.\s]+@[0-9a-f]{40}", line) and "#" not in line:
                    missing.append(f"{path.name}:{line_no}")
        self.assertEqual(missing, [], "SHA pins without a version comment: " + "; ".join(missing))
