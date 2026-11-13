"""Every pinned dependency source in the repository has a Dependabot updater.

A pin nothing watches goes stale silently, and the hash-pinned requirements
files are exactly the ones a human never touches.
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


def test_the_hash_pinned_requirements_files_are_watched():
    assert sorted(ROOT.glob("requirements*.txt")), "no requirements files at the root"
    assert "/" in _ecosystems().get("pip", set())


def test_the_lockfile_actions_and_images_are_still_watched():
    ecosystems = _ecosystems()
    for name in ("uv", "github-actions", "docker"):
        assert "/" in ecosystems.get(name, set()), name
