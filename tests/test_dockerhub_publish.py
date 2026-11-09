"""The Docker Hub publish path must stay wired up and must not leak a token.

The image is published to GitHub Container Registry and to Docker Hub
(``docker.io/ryabikov/pywrkr``). The Docker Hub credentials are repository
secrets, so these checks make sure the workflow keeps using them -- reads them
from the secrets, never inlines one, and skips the Docker Hub steps when the
repository has no token, so a fork still publishes to GitHub Container Registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "docker-publish.yml"
DOCKERHUB_IMAGE = "docker.io/ryabikov/pywrkr"
SECRET_NAMES = ("DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN")


@pytest.fixture(scope="module")
def workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text())
    # YAML 1.1 reads a bare "on:" key as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _step(workflow: dict, job: str, name: str) -> dict:
    for step in _steps(workflow, job):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in job {job!r}")


def test_the_workflow_publishes_to_both_registries(workflow):
    env = workflow["env"]
    assert env["REGISTRY"] == "ghcr.io"
    assert env["IMAGE_NAME"] == "${{ github.repository }}"
    assert env["DOCKERHUB_REGISTRY"] == "docker.io"
    assert env["DOCKERHUB_IMAGE"] == DOCKERHUB_IMAGE

    images = _step(workflow, "build-and-push", "Extract metadata (tags, labels)")["with"]["images"]
    assert "${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}" in images
    assert "${{ env.DOCKERHUB_IMAGE }}" in images


def test_the_docker_hub_login_uses_the_repository_secrets(workflow):
    step = _step(workflow, "build-and-push", "Log in to Docker Hub")
    assert step["with"]["registry"] == "${{ env.DOCKERHUB_REGISTRY }}"
    assert step["with"]["username"] == "${{ env.DOCKERHUB_USERNAME }}"
    assert step["with"]["password"] == "${{ env.DOCKERHUB_TOKEN }}"


def test_the_docker_hub_steps_are_skipped_without_a_token(workflow):
    """A fork (or a repository without the secrets) still publishes to GHCR."""
    step = _step(workflow, "build-and-push", "Log in to Docker Hub")
    assert "env.DOCKERHUB_TOKEN" in step["if"], (
        "the Docker Hub login must be guarded on the token being present"
    )
    # The guard must not depend on the secret itself: GitHub does not allow a
    # secret to be read in an ``if`` condition.
    assert "secrets." not in step["if"]


def test_no_token_is_written_into_the_workflow(workflow):
    text = WORKFLOW.read_text()
    for name in SECRET_NAMES:
        # The name may appear (it is how the secret is read); a value may not.
        assert f"{name}={{{{" not in text
    assert "dockerhub_token" not in text.lower() or "secrets.DOCKERHUB_TOKEN" in text


def test_the_token_reaches_the_job_as_an_environment_variable(workflow):
    env = workflow["jobs"]["build-and-push"].get("env") or {}
    assert env.get("DOCKERHUB_TOKEN") == "${{ secrets.DOCKERHUB_TOKEN }}"
    assert env.get("DOCKERHUB_USERNAME") == "${{ secrets.DOCKERHUB_USERNAME }}"


def test_a_pull_request_still_never_reaches_the_docker_hub_login(workflow):
    """The job that logs in to Docker Hub must not run for a pull request."""
    job = workflow["jobs"]["build-and-push"]
    assert "pull_request" in str(job.get("if", "")), (
        "the publishing job must be gated away from pull_request events"
    )


def test_the_workflow_can_be_run_by_hand_with_a_tag(workflow):
    on = workflow["on"]
    assert "workflow_dispatch" in on
    assert "tag" in on["workflow_dispatch"]["inputs"]
    # The extra tag is a step of its own, gated by a plain GitHub expression:
    # metadata-action evaluates ``enable=`` with its own parser, which does not
    # understand ``${{ }}``.
    step = _step(workflow, "build-and-push", "Extract metadata (the extra tag, when one was given)")
    assert step["if"] == "github.event_name == 'workflow_dispatch' && inputs.tag != ''"
    assert "inputs.tag" in step["with"]["tags"]
    # And its tags must reach the push.
    push = _step(workflow, "build-and-push", "Build and push Docker image")
    assert "steps.meta-extra.outputs.tags" in push["with"]["tags"]
