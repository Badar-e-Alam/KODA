"""Discover agent skills from the local workspace.

Any directory under ``<workspace>/skills/`` that contains a ``SKILL.md`` is
treated as a skill. No network, no downloads — whatever is on disk gets
used.

To add a skill: drop it into ``agent_workspace/skills/<name>/`` with a
``SKILL.md`` at its root. It will be picked up on the next agent build.

Public API
----------
    discover_skills(workspace) -> list[str]
        Returns workspace-relative POSIX paths (e.g. ``/skills/pdf/``)
        ready to pass to ``create_deep_agent(skills=[...])``.
"""

from __future__ import annotations

from pathlib import Path


def discover_skills(workspace: str | Path) -> list[str]:
    """Return workspace-relative paths for every skill under ``<workspace>/skills/``.

    A skill is any subdirectory of ``skills/`` that contains a ``SKILL.md`` at
    its root. Order is deterministic (alphabetical by skill name).
    """
    root = Path(workspace).resolve()
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return []

    paths: list[str] = []
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").is_file():
            continue
        paths.append(f"/skills/{entry.name}/")
    return paths


__all__ = ["discover_skills"]
