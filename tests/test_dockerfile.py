"""Structural checks on the published image's Dockerfile.

The built image itself is checked in CI by ``.github/scripts/check-image.sh``
(non-root, a working health check, a writable working directory). These read
the source so a regression fails the unit suite before an image is built.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
CHECK_IMAGE = ROOT / ".github" / "scripts" / "check-image.sh"


def _runtime_stage() -> list[str]:
    """Instruction lines of the last stage, continuations joined."""
    lines: list[str] = []
    pending = ""
    for raw in DOCKERFILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
    starts = [i for i, line in enumerate(lines) if line.upper().startswith("FROM ")]
    assert starts, "the Dockerfile has no FROM"
    return lines[starts[-1] :]


def _instructions(keyword: str) -> list[str]:
    prefix = keyword.upper() + " "
    return [
        line[len(prefix) :].strip() for line in _runtime_stage() if line.upper().startswith(prefix)
    ]


def test_the_image_runs_as_a_non_root_user():
    users = _instructions("USER")
    assert users, "the runtime stage has no USER"
    assert users[-1] not in ("root", "0", "0:0")


def test_the_image_has_a_working_directory_that_is_not_root():
    workdirs = _instructions("WORKDIR")
    assert workdirs, "the runtime stage has no WORKDIR, so it inherits '/'"
    assert workdirs[-1].rstrip("/") != ""


@pytest.mark.parametrize("keyword", ["USER", "WORKDIR"])
def test_user_and_workdir_come_after_the_user_is_created(keyword):
    stage = _runtime_stage()
    created = next(i for i, line in enumerate(stage) if "adduser" in line)
    last = max(i for i, line in enumerate(stage) if line.upper().startswith(keyword + " "))
    assert last > created


def test_the_working_directory_is_the_runtime_users_home():
    # adduser -D creates /home/<user> owned by that user; any other directory
    # would need a mkdir and chown to be writable.
    user = _instructions("USER")[-1]
    assert _instructions("WORKDIR")[-1].rstrip("/") == f"/home/{user}"


def test_the_image_check_writes_a_relative_report():
    script = CHECK_IMAGE.read_text()
    assert "--json results.json" in script
    assert "test -s results.json" in script
