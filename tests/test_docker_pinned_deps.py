"""The image's Python dependencies are pinned by version and hash (#342).

The runtime stage used to ``pip install`` the wheel and let pip resolve
``aiohttp~=3.14`` and everything under it from the index on the day of the
build, so the same commit could produce a different image and the provenance
attestation covered none of it. These checks keep the pinned install in place
and the committed requirements file consistent with ``pyproject.toml``.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, requires
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
REQUIREMENTS = ROOT / "requirements-image.txt"
WORKFLOW = ROOT / ".github" / "workflows" / "docker-publish.yml"


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _logical_lines(text: str) -> list[str]:
    return [chunk.strip() for chunk in text.replace("\\\n", " ").splitlines() if chunk.strip()]


def _requirements() -> dict[str, str]:
    """name -> the requirement's whole logical line, comments dropped."""
    entries: dict[str, str] = {}
    for line in _logical_lines(REQUIREMENTS.read_text()):
        if line.startswith("#"):
            continue
        name = re.split(r"[=<>!~ \[;]", line, maxsplit=1)[0]
        entries[_normalize(name)] = line
    return entries


def _runtime_run_lines() -> list[str]:
    lines = _logical_lines(DOCKERFILE.read_text())
    last_from = max(i for i, line in enumerate(lines) if line.startswith("FROM "))
    return [line for line in lines[last_from:] if line.startswith("RUN ")]


def _pip_installs() -> list[str]:
    installs: list[str] = []
    for run in _runtime_run_lines():
        installs += [part.strip() for part in run[4:].split("&&") if "pip install" in part]
    return installs


def test_the_requirements_file_is_committed():
    assert REQUIREMENTS.is_file()
    assert _requirements(), "requirements-image.txt pins nothing"


def test_every_requirement_is_pinned_by_version_and_hash():
    for name, line in _requirements().items():
        assert re.search(r"==\S+", line), f"{name} is not pinned to a version"
        assert "--hash=sha256:" in line, f"{name} has no hash"


def test_the_runtime_stage_installs_with_require_hashes():
    from_file = [i for i in _pip_installs() if "-r /tmp/requirements-image.txt" in i]
    assert len(from_file) == 1, _pip_installs()
    for flag in ("--require-hashes", "--no-deps", "--only-binary :all:"):
        assert flag in from_file[0], flag


def test_nothing_is_resolved_from_the_index_outside_the_pinned_file():
    for install in _pip_installs():
        if "-r /tmp/requirements-image.txt" in install:
            continue
        # The locally built pywrkr wheel: installed without resolving anything.
        assert "/tmp/*.whl" in install and "--no-deps" in install, install
    assert any("pip check" in run for run in _runtime_run_lines())


def test_the_requirements_file_is_copied_into_the_image():
    assert re.search(r"^COPY requirements-image\.txt /tmp/$", DOCKERFILE.read_text(), re.M)


def test_the_file_is_compiled_for_the_image_interpreter():
    header = REQUIREMENTS.read_text().split("\n", 3)[1]
    # The last FROM is the runtime stage, the one the file is installed into;
    # the builder stage's interpreter does not matter here.
    runtime = [ln for ln in DOCKERFILE.read_text().splitlines() if ln.startswith("FROM ")][-1]
    base = re.match(r"FROM python:(\d+\.\d+)-alpine@sha256:", runtime)
    assert base, f"runtime base is not python:<x.y>-alpine pinned by digest: {runtime}"
    assert f"--python-version {base.group(1)}" in header
    assert "--generate-hashes" in header
    assert "-unknown-linux-musl" in header


def test_every_direct_dependency_of_the_image_is_pinned():
    """pywrkr's own requirements, base and the image extras, all appear pinned.

    A dependency added to pyproject.toml without regenerating the file would
    otherwise fail only at image build time, in `pip check`.
    """
    header = REQUIREMENTS.read_text().split("\n", 3)[1]
    extras = set(re.findall(r"--extra (\S+)", header))
    pinned = set(_requirements())
    missing = []
    for req in requires("pywrkr") or []:
        name, _, marker = req.partition(";")
        extra = re.search(r"extra\s*==\s*['\"]([^'\"]+)['\"]", marker)
        if extra and extra.group(1) not in extras:
            continue
        bare = _normalize(re.split(r"[=<>!~ \[(]", name.strip(), maxsplit=1)[0])
        if bare not in pinned:
            missing.append(bare)
        # And the extras of that dependency, e.g. httpx[http2] -> h2.
        for dep_extra in re.findall(r"\[([^\]]+)\]", name):
            for sub in _extra_requirements(bare, {e.strip() for e in dep_extra.split(",")}):
                if sub not in pinned:
                    missing.append(sub)
    assert not missing, f"not in requirements-image.txt (regenerate it): {sorted(set(missing))}"


def _extra_requirements(dist: str, extras: set[str]) -> set[str]:
    """Names a dependency adds for *extras*, read from its installed metadata."""
    try:
        reqs = requires(dist) or []
    except PackageNotFoundError:
        pytest.skip(f"{dist} is not installed here, so its extras cannot be read")
    names = set()
    for req in reqs:
        name, _, marker = req.partition(";")
        found = re.search(r"extra\s*==\s*['\"]([^'\"]+)['\"]", marker)
        if found and found.group(1) in extras:
            names.add(_normalize(re.split(r"[=<>!~ \[(]", name.strip(), maxsplit=1)[0]))
    return names


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_a_change_to_the_file_rebuilds_the_image(workflow):
    on = workflow.get("on", workflow.get(True))
    for event in ("push", "pull_request"):
        assert "requirements-image.txt" in on[event]["paths"], event


def test_the_published_image_carries_an_sbom(workflow):
    push = next(s for s in workflow["jobs"]["build-and-push"]["steps"] if s.get("id") == "push")
    assert push["with"].get("sbom") is True
