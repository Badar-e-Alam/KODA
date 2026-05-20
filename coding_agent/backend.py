"""Backend construction for `coding_agent`.

Splits filesystem responsibilities across three storage strategies via
``CompositeBackend`` (see https://docs.langchain.com/oss/python/deepagents/backends):

- **default** → ``LocalShellBackend`` rooted at the project cwd. Serves
  ``execute``/``read_file``/``write_file``/``edit_file``/``ls``/``glob``/
  ``grep`` against the real working tree, so the agent can run commands
  and modify project files in-place.
- ``/memories/`` → ``FilesystemBackend`` rooted at ``<cwd>/.koda/memories/``.
  Everything the agent writes under ``/memories/...`` lands on disk
  *inside the project being worked on*, so memories travel with the
  project and survive process restarts without needing an external
  store. Different projects get isolated `.koda/memories/` trees.
- ``/skills/`` → ``FilesystemBackend`` rooted at ``coding_agent/skills/``
  (package-bundled). Read-mostly skill definitions ship with the agent
  and are available in every project; the agent reaches them via
  ``skills=['/skills/']`` on the deepagents factory.

All three routes are project-scoped *except* ``/skills/``, which is
intentionally global so skills are reusable across projects.
"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
)
from deepagents.backends.protocol import BackendProtocol

# Skills ship with the package so the agent always has them available,
# regardless of which project directory it was launched in.
SKILLS_DIR = Path(__file__).parent / "skills"


def project_memories_dir(root: Path) -> Path:
    """Where ``/memories/*`` writes land on disk for the given project."""
    return root / ".koda" / "memories"


def build_backend(
    root: Path,
    *,
    timeout: int = 180,
    inherit_env: bool = True,
) -> BackendProtocol:
    """Construct the composite backend for the coding agent.

    Args:
        root: Project working directory. The default backend's
            ``root_dir`` and the ``<root>/.koda/memories/`` mount point
            both anchor here.
        timeout: Shell-command timeout (seconds) for ``LocalShellBackend``.
        inherit_env: Whether subshells inherit the parent process env.

    Returns:
        A ``CompositeBackend`` that the caller passes to
        ``create_deep_agent(backend=...)``. Memory + skill directories
        are auto-created if missing.
    """
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    memories_dir = project_memories_dir(root)
    memories_dir.mkdir(parents=True, exist_ok=True)

    default = LocalShellBackend(
        root_dir=root,
        virtual_mode=True,
        timeout=timeout,
        inherit_env=inherit_env,
    )
    memories = FilesystemBackend(root_dir=memories_dir, virtual_mode=True)
    skills = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)

    return CompositeBackend(
        default=default,
        routes={
            "/memories/": memories,
            "/skills/": skills,
        },
    )
