"""Pull-request-triggered workflows must not hold write-scoped tokens (#308).

On a public repository GitHub already downgrades a fork's ``pull_request``
token to read-only, but that is a platform default, not something this
repository states. These checks make the workflows state it: no job that can
run for a pull request may carry a write scope, except where the job is
gated to this repository's own branches.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
WRITE_SCOPES = {"packages", "id-token", "pull-requests", "contents", "attestations", "actions"}


def _load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    # YAML 1.1 reads a bare `on:` key as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _writes(permissions: object) -> set[str]:
    if permissions == "write-all":
        return set(WRITE_SCOPES)
    if not isinstance(permissions, dict):
        return set()
    return {scope for scope, level in permissions.items() if level == "write"}


def _pull_request_jobs(data: dict) -> dict[str, dict]:
    """Jobs that can run for a pull_request event, by their ``if`` guard."""
    jobs = {}
    for name, job in data["jobs"].items():
        guard = str(job.get("if", ""))
        if "github.event_name != 'pull_request'" in guard:
            continue
        jobs[name] = job
    return jobs


def test_docker_publish_does_not_grant_write_permissions_to_pr_runs():
    data = _load("docker-publish.yml")
    assert "pull_request" in data["on"]
    assert not _writes(data.get("permissions")), "workflow-level permissions must be read-only"
    pr_jobs = _pull_request_jobs(data)
    assert pr_jobs, "the image should still be built on pull requests"
    for name, job in pr_jobs.items():
        writes = _writes(job.get("permissions"))
        assert not writes & {"packages", "id-token", "attestations"}, (name, writes)


def test_docker_publish_still_pushes_and_attests_outside_pull_requests():
    data = _load("docker-publish.yml")
    pushers = [
        job
        for job in data["jobs"].values()
        if "github.event_name != 'pull_request'" in str(job.get("if", ""))
    ]
    assert len(pushers) == 1
    writes = _writes(pushers[0].get("permissions"))
    assert {"packages", "id-token", "attestations"} <= writes
    uses = [step.get("uses", "") for step in pushers[0]["steps"]]
    assert any(u.startswith("actions/attest-build-provenance@") for u in uses)


def test_sanitize_pr_description_does_not_write_on_fork_pull_requests():
    data = _load("sanitize-pr-description.yml")
    assert not _writes(data.get("permissions")), "workflow-level permissions must be read-only"
    for name, job in data["jobs"].items():
        if _writes(job.get("permissions")):
            guard = str(job.get("if", ""))
            assert "github.event.pull_request.head.repo.full_name == github.repository" in guard, (
                name
            )


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_no_pull_request_target_trigger(path):
    """pull_request_target runs with a write token in the base context; none
    of these workflows needs it, so its appearance should be a deliberate
    review decision."""
    assert "pull_request_target" not in (_load(path.name).get("on") or {})
