"""Backend construction for `coding_agent`.

Splits filesystem responsibilities across three storage strategies via
``CompositeBackend`` (see https://docs.langchain.com/oss/python/deepagents/backends):

- **default** → ``LocalShellBackend`` rooted at the project cwd. Serves
  ``execute``/``read_file``/``write_file``/``edit_file``/``ls``/``glob``/
  ``grep`` against the real working tree, so the agent can run commands
  and modify project files in-place.
- ``/memories/`` → ``StoreBackend`` namespaced per-project. Anything the
  agent writes under ``/memories/...`` is persisted in a LangGraph
  ``BaseStore`` and survives across threads. ``AGENTS.md`` is still
  loaded into the prompt via ``memory=`` on the agent — these
  ``/memories/`` files are *additional* notes the agent can author.
- ``/skills/`` → ``FilesystemBackend`` rooted at ``coding_agent/skills/``.
  Read-mostly skill definitions live there; the agent reaches them via
  ``skills=['/skills/']`` on the deepagents factory.

``build_backend`` returns the composite plus the ``BaseStore`` that the
caller must hand to ``create_deep_agent(store=...)`` — ``StoreBackend``
pulls the store off the ``Runtime`` at tool-call time, so the same store
instance has to be wired into the graph.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
    StoreBackend,
)
from deepagents.backends.protocol import BackendProtocol
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

# Skills ship with the package so the agent always has them available,
# regardless of which project directory it was launched in.
SKILLS_DIR = Path(__file__).parent / "skills"


def _project_namespace(root: Path):
    """Namespace factory for ``StoreBackend``.

    Returns a callable that ignores the LangGraph ``Runtime`` and pins
    the namespace to a stable hash of the project root. This keeps
    ``/memories/`` partitioned per-project — running ``koda`` in a
    different cwd gets a separate slice — without requiring auth/user
    info on the runtime.
    """
    key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]

    def _ns(_runtime: object) -> tuple[str, ...]:
        return ("coding_agent", "memories", key)

    return _ns


def build_backend(
    root: Path,
    *,
    timeout: int = 180,
    inherit_env: bool = True,
    store: BaseStore | None = None,
) -> tuple[BackendProtocol, BaseStore]:
    """Construct the composite backend for the coding agent.

    Args:
        root: Project working directory. Used as the default backend's
            ``root_dir`` and as the per-project memory namespace.
        timeout: Shell-command timeout (seconds) for ``LocalShellBackend``.
        inherit_env: Whether subshells inherit the parent process env.
        store: Optional pre-built ``BaseStore``. Pass one when you want
            durable cross-process memory (e.g. a Postgres/Redis store);
            falls back to ``InMemoryStore`` for development.

    Returns:
        ``(backend, store)``. The caller passes ``backend`` to
        ``create_deep_agent(backend=...)`` and the same ``store`` to
        ``create_deep_agent(store=...)`` so ``/memories/`` writes land
        in the namespace defined here.
    """
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    default = LocalShellBackend(
        root_dir=root,
        virtual_mode=True,
        timeout=timeout,
        inherit_env=inherit_env,
    )
    memories = StoreBackend(namespace=_project_namespace(root))
    skills = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)

    composite = CompositeBackend(
        default=default,
        routes={
            "/memories/": memories,
            "/skills/": skills,
        },
    )
    return composite, store or InMemoryStore()
