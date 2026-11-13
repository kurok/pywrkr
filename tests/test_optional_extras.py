"""A missing optional extra is reported before the run, not after it (#340).

``--otel-endpoint`` and ``--live`` used to let the benchmark run and only then
say the package was missing, so the report looked complete when the export or
the dashboard never happened. They are now startup errors, like ``--http2``.

The container image ships the extras those flags need; the last tests keep the
Dockerfile, the package metadata and the README from drifting apart.
"""

from __future__ import annotations

import re
from importlib.metadata import metadata
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from pywrkr.main import _build_parser, _parse_and_validate_args, main

ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXTRAS = {"http2", "otel", "tui", "yaml"}


def _parse(argv: list[str]):
    parser = _build_parser()
    return _parse_and_validate_args(parser, parser.parse_args(argv))


@pytest.mark.parametrize(
    ("flag_args", "flag", "attr", "hint"),
    [
        (["--otel-endpoint", "http://collector:4318"], "--otel-endpoint", "OTEL_AVAILABLE", "otel"),
        (["--live"], "--live", "RICH_AVAILABLE", "tui"),
    ],
)
def test_a_missing_extra_is_reported_before_the_run(flag_args, flag, attr, hint):
    # Through the real entry point, with the run itself replaced: the error has
    # to come out of argument validation, before any benchmark starts.
    argv = ["pywrkr", *flag_args, "-n", "1", "http://example.com"]
    with (
        patch(f"pywrkr.reporting.{attr}", False),
        patch("sys.argv", argv),
        patch("pywrkr.main._determine_and_run_mode") as run,
        patch("sys.stderr", new_callable=StringIO) as err,
        pytest.raises(SystemExit) as exc,
    ):
        main()
    assert exc.value.code == 2
    message = err.getvalue()
    assert flag in message
    assert f"pywrkr[{hint}]" in message
    run.assert_not_called()


@pytest.mark.parametrize(
    ("flag_args", "attr", "field", "expected"),
    [
        (
            ["--otel-endpoint", "http://collector:4318"],
            "OTEL_AVAILABLE",
            "otel_endpoint",
            "http://collector:4318",
        ),
        (["--live"], "RICH_AVAILABLE", "live_dashboard", True),
    ],
)
def test_an_installed_extra_passes_the_check(flag_args, attr, field, expected):
    # The other half of the check: with the extra present the flag reaches the
    # config, so a validator that rejected unconditionally would fail here.
    with patch(f"pywrkr.reporting.{attr}", True):
        config, _ = _parse([*flag_args, "-n", "1", "http://example.com"])
    assert getattr(config, field) == expected


def test_no_extra_is_checked_when_no_flag_asks_for_one():
    with (
        patch("pywrkr.reporting.OTEL_AVAILABLE", False),
        patch("pywrkr.reporting.RICH_AVAILABLE", False),
    ):
        config, _ = _parse(["-n", "1", "http://example.com"])
    assert config is not None


def _image_extras() -> set[str]:
    # The image installs from requirements-image.txt (#342), compiled from
    # pyproject.toml with one --extra per extra; the header records them.
    header = (ROOT / "requirements-image.txt").read_text().split("\n", 3)[1]
    return set(re.findall(r"--extra (\S+)", header))


def test_the_image_installs_the_extras_the_cli_flags_need():
    assert _image_extras() == IMAGE_EXTRAS


def test_every_image_extra_is_declared_by_the_package():
    declared = set(metadata("pywrkr").get_all("Provides-Extra") or [])
    assert IMAGE_EXTRAS <= declared


def test_the_readme_says_which_extras_the_image_has():
    readme = (ROOT / "README.md").read_text()
    section = readme[readme.index("## Container image") :]
    end = section.find("\n## ", 1)
    section = section if end == -1 else section[:end]
    for extra in IMAGE_EXTRAS:
        assert f"`{extra}`" in section, extra


def test_the_image_check_probes_the_extras():
    script = (ROOT / ".github" / "scripts" / "check-image.sh").read_text()
    # The probe itself, not a mention in a comment: the module list of the
    # `python -c 'import ...'` the script runs in the image.
    match = re.search(r"--entrypoint python \"\$image\" -c \\\n\s*'import ([^']+)'", script)
    assert match, "check-image.sh does not import the extras in the image"
    imported = {m.strip() for m in match.group(1).split(",")}
    assert {"httpx", "h2", "opentelemetry.sdk", "rich", "yaml"} <= imported
