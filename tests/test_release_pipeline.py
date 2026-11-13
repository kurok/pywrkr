"""Structural checks on the release pipeline's Python build inputs (#307).

The build job's output is published, unmodified, by a job holding PyPI's
trusted-publishing identity, so whatever resolves the build tool and backend
controls what gets published. These checks keep every such input pinned by
version and hash rather than resolved from the index at release time.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOWS = ["publish.yml", "release.yml"]
# Commands that build the package; each resolves an isolated backend from the
# index unless told otherwise.
BUILD_COMMAND = re.compile(
    r"(-m\s+build|\bpyproject-build|\buv\s+build|\bpipx\s+run\s+build)(\s|$)"
)
REQUIREMENTS = REPO_ROOT / "requirements-build.txt"


def _run_lines(workflow: str) -> list[str]:
    data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / workflow).read_text())
    lines = []
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            # Join shell continuations, so a flag on the next line counts.
            script = str(step.get("run", "")).replace("\\\n", " ")
            for line in script.splitlines():
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
    installs = [line for line in _run_lines(workflow) if re.search(r"\bpip3?\s+install\b", line)]
    assert installs, f"{workflow}: expected a pip install step"
    for line in installs:
        hashed = "--require-hashes" in line and "-r requirements-build.txt" in line
        # Only the checkout itself may be installed this way.
        local_only = (
            "--no-build-isolation" in line and "--no-deps" in line and line.split()[-1] == "."
        )
        assert hashed or local_only, f"{workflow}: unpinned install: {line!r}"
        if hashed:
            assert "--no-deps" in line, f"{workflow}: hashed install must not resolve: {line!r}"


@pytest.mark.parametrize("workflow", RELEASE_WORKFLOWS)
def test_no_build_runs_in_an_isolated_environment(workflow):
    """An isolated build resolves the build backend from the index."""
    for line in _run_lines(workflow):
        if BUILD_COMMAND.search(line):
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


@pytest.mark.parametrize("workflow", RELEASE_WORKFLOWS)
def test_a_local_install_follows_the_pinned_build_inputs(workflow):
    """--no-build-isolation uses whatever backend is installed, so the hashed
    install must come first."""
    lines = _run_lines(workflow)
    for i, line in enumerate(lines):
        if "--no-build-isolation" in line or "--no-isolation" in line:
            assert any("-r requirements-build.txt" in prior for prior in lines[:i]), (
                f"{workflow}: {line!r} runs before the pinned build inputs are installed"
            )


@pytest.mark.parametrize("workflow", [*RELEASE_WORKFLOWS, "python-package.yml"])
def test_release_python_matches_the_lockfile(workflow):
    """requirements-build.txt is compiled for one Python; --no-deps would hide
    a dependency another version needs until the tag is pushed."""
    header = re.search(r"--python-version (\S+)", REQUIREMENTS.read_text())
    assert header, "requirements-build.txt does not record its Python version"
    data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / workflow).read_text())
    versions = {
        str(step.get("with", {}).get("python-version"))
        for job in data["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    }
    assert versions == {header.group(1)}, (workflow, versions)
