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
WORKFLOW_FILES = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
WRITE_SCOPES = {"packages", "id-token", "pull-requests", "contents", "attestations", "actions"}
# Guards that keep a job off pull_request events entirely.
NOT_ON_PULL_REQUESTS = {
    "github.event_name != 'pull_request'",
    "${{ github.event_name != 'pull_request' }}",
}
SAME_REPO_ONLY = "github.event.pull_request.head.repo.full_name == github.repository"
# Write scopes a pull-request job may hold, by workflow, with the reason.
ALLOWED_PR_WRITES = {
    # CodeQL uploads its SARIF results; the token cannot touch code or packages.
    "codeql.yml": {"security-events"},
}


def _load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    if not isinstance(data, dict):
        return {}
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
        # An exact match: a guard such as "!= 'pull_request' || <label>"
        # still lets pull requests through.
        if str(job.get("if", "")).strip() in NOT_ON_PULL_REQUESTS:
            continue
        jobs[name] = job
    return jobs


def _triggers(data: dict) -> set[str]:
    on = data.get("on") or {}
    if isinstance(on, str):
        return {on}
    return set(on)


def test_docker_publish_does_not_grant_write_permissions_to_pr_runs():
    data = _load("docker-publish.yml")
    assert "pull_request" in data["on"]
    assert not _writes(data.get("permissions")), "workflow-level permissions must be read-only"
    pr_jobs = _pull_request_jobs(data)
    assert pr_jobs, "the image should still be built on pull requests"
    for name, job in pr_jobs.items():
        assert not _writes(job.get("permissions")), name


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_pull_request_jobs_hold_no_write_scope(path):
    """Every workflow a pull request triggers, not only the two this was found in."""
    data = _load(path.name)
    if "pull_request" not in _triggers(data):
        pytest.skip("not triggered by pull_request")
    allowed = ALLOWED_PR_WRITES.get(path.name, set())
    default = data.get("permissions")
    for name, job in _pull_request_jobs(data).items():
        if str(job.get("if", "")).strip() == SAME_REPO_ONLY:
            continue
        # A job without its own block runs with the workflow's.
        writes = _writes(job.get("permissions", default))
        assert writes <= allowed, (path.name, name, sorted(writes - allowed))


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
            assert guard.strip() == SAME_REPO_ONLY, name


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_no_pull_request_target_trigger(path):
    """pull_request_target runs with a write token in the base context; none
    of these workflows needs it, so its appearance should be a deliberate
    review decision."""
    assert "pull_request_target" not in _triggers(_load(path.name))
