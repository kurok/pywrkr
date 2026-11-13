"""Every pinned dependency source in the repository has a Dependabot updater.

A pin nothing watches goes stale silently, and the hash-pinned requirements
files are exactly the ones a human never touches. Dependabot's uv updater
reads uv.lock and every requirements *.in / *.txt in its directory, and
re-compiles a .txt from the .in of the same name; a .txt without one would
only get single-pin bumps that leave its other pins unresolved.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".github" / "dependabot.yml"


def _ecosystems() -> dict[str, set[str]]:
    data = yaml.safe_load(CONFIG.read_text())
    found: dict[str, set[str]] = {}
    for update in data["updates"]:
        dirs = update.get("directories") or [update["directory"]]
        found.setdefault(update["package-ecosystem"], set()).update(dirs)
    return found


def test_the_requirements_files_sit_where_the_uv_updater_looks():
    compiled = sorted(ROOT.glob("requirements*.txt"))
    assert compiled, "no requirements files at the root"
    assert "/" in _ecosystems().get("uv", set())


def test_every_compiled_requirements_file_has_its_source():
    for txt in ROOT.glob("requirements*.txt"):
        source = txt.with_suffix(".in")
        assert source.is_file(), f"{txt.name} has no {source.name} to re-compile from"
        header = txt.read_text().split("\n", 3)[1]
        assert f"uv pip compile {source.name} " in header, txt.name


def test_no_second_python_updater_competes_for_the_same_files():
    assert "pip" not in _ecosystems()


def test_the_actions_and_images_are_still_watched():
    ecosystems = _ecosystems()
    for name in ("github-actions", "docker"):
        assert "/" in ecosystems.get(name, set()), name
