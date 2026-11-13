"""The published image is multi-platform: linux/amd64 and linux/arm64 (#339).

Structural checks on ``docker-publish.yml``. The built images themselves are
checked in CI, one platform at a time, by ``.github/scripts/check-image.sh``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "docker-publish.yml"
PLATFORMS = ("linux/amd64", "linux/arm64")
JOBS = ("build-pr", "build-and-push")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _index(steps: list[dict], prefix: str) -> int:
    for i, step in enumerate(steps):
        if step.get("uses", "").startswith(prefix):
            return i
    raise AssertionError(f"no step uses {prefix}")


def _platforms(step: dict) -> set[str]:
    return {
        p.strip() for p in str(step.get("with", {}).get("platforms", "")).split(",") if p.strip()
    }


def test_the_image_is_built_for_more_than_one_platform(workflow):
    push = next(s for s in _steps(workflow, "build-and-push") if s.get("id") == "push")
    assert set(PLATFORMS) <= _platforms(push)
    assert push["with"]["push"] is True
    # A multi-platform result cannot be loaded into the local daemon.
    assert "load" not in push["with"]


@pytest.mark.parametrize("job", JOBS)
def test_qemu_and_buildx_are_set_up_before_any_build(job, workflow):
    steps = _steps(workflow, job)
    qemu = _index(steps, "docker/setup-qemu-action@")
    buildx = _index(steps, "docker/setup-buildx-action@")
    first_build = _index(steps, "docker/build-push-action@")
    assert qemu < buildx < first_build


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_platform_is_built_and_checked_before_the_push(job, platform, workflow):
    steps = _steps(workflow, job)
    builds = [
        i
        for i, s in enumerate(steps)
        if s.get("uses", "").startswith("docker/build-push-action@")
        and _platforms(s) == {platform}
        and s["with"].get("load") is True
        and s["with"].get("push") is False
    ]
    assert len(builds) == 1, f"{job}: no single loaded {platform} build"
    tag = steps[builds[0]]["with"]["tags"]
    checks = [
        i
        for i, s in enumerate(steps)
        if "check-image.sh" in str(s.get("run", "")) and tag in str(s.get("run", ""))
    ]
    assert len(checks) == 1, f"{job}: {tag} is never checked"
    assert checks[0] > builds[0]
    if platform != "linux/amd64":
        # The runner is amd64; without this the check would run the runner's
        # platform rather than the image that was just built.
        assert steps[checks[0]].get("env", {}).get("DOCKER_DEFAULT_PLATFORM") == platform
    pushes = [i for i, s in enumerate(steps) if s.get("id") == "push"]
    if pushes:
        assert checks[0] < pushes[0], "an image must be checked before it is pushed"


def test_the_attestation_names_the_pushed_index(workflow):
    steps = _steps(workflow, "build-and-push")
    attest_at = _index(steps, "actions/attest-build-provenance@")
    push_at = next(i for i, s in enumerate(steps) if s.get("id") == "push")
    attest = steps[attest_at]
    assert attest_at > push_at, "the digest only exists once the push has run"
    assert attest["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
    assert attest["with"]["push-to-registry"] is True
