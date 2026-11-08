"""Structural checks on the release pipeline's Python build inputs (#307).

The build job's output is published, unmodified, by a job holding PyPI's
trusted-publishing identity, so whatever resolves the build tool and backend
controls what gets published. These checks keep every such input pinned by
version and hash rather than resolved from the index at release time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOWS = ["publish.yml", "release.yml"]
REQUIREMENTS = REPO_ROOT / "requirements-build.txt"


def _run_lines(workflow: str) -> list[str]:
    data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / workflow).read_text())
    lines = []
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            for line in str(step.get("run", "")).splitlines():
                if line.strip():
                    lines.append(line.strip())
    return lines


def _pinned_requirements() -> dict[str, str]:
    """name -> exact version, asserting every requirement is hash-pinned."""
    text = REQUIREMENTS.read_text()
    pinned = {}
    for block in re.split(r"\n(?=[A-Za-z])", text):
        first = block.strip().splitlines()[0] if block.strip() else ""
        if not first or first.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s\;]+)", first)
        assert match, f"requirement not pinned with ==: {first!r}"
        assert "--hash=sha256:" in block, f"requirement has no hash: {first!r}"
        pinned[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pinned


@pytest.mark.parametrize("workflow", RELEASE_WORKFLOWS)
def test_every_pip_install_in_the_release_path_is_pinned(workflow):
    installs = [line for line in _run_lines(workflow) if re.search(r"\bpip install\b", line)]
    assert installs, f"{workflow}: expected a pip install step"
    for line in installs:
        hashed = "--require-hashes" in line and "-r requirements-build.txt" in line
        local_only = "--no-build-isolation" in line and "--no-deps" in line
        assert hashed or local_only, f"{workflow}: unpinned install: {line!r}"
        if hashed:
            assert "--no-deps" in line, f"{workflow}: hashed install must not resolve: {line!r}"


@pytest.mark.parametrize("workflow", RELEASE_WORKFLOWS)
def test_no_build_runs_in_an_isolated_environment(workflow):
    """An isolated build resolves the build backend from the index."""
    for line in _run_lines(workflow):
        if re.search(r"\b-m build\b", line):
            assert "--no-isolation" in line, f"{workflow}: isolated build: {line!r}"


def test_build_requirements_are_exact_and_hashed():
    pinned = _pinned_requirements()
    assert {"build", "setuptools", "wheel"} <= set(pinned)


def test_build_requirements_cover_the_build_backend():
    """Every build-system requirement must be among the pinned inputs, or a
    --no-isolation build would fail (or worse, use whatever is installed)."""
    from packaging.requirements import Requirement
    from packaging.version import Version

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    pinned = _pinned_requirements()
    for raw in pyproject["build-system"]["requires"]:
        requirement = Requirement(raw)
        name = requirement.name.lower().replace("_", "-")
        assert name in pinned, f"build requirement {raw!r} is not pinned"
        assert requirement.specifier.contains(Version(pinned[name])), (
            f"pinned {name}=={pinned[name]} does not satisfy {raw!r}"
        )
