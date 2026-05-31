"""Backend construction for `coding_agent`.

Splits filesystem responsibilities across three storage strategies via
``CompositeBackend`` 
(see https://docs.langchain.com/oss/python/deepagents/backends):

- **default** → ``LocalShellBackend`` rooted at the project cwd. Serves
  ``execute``/``read_file``/``write_file``/``edit_file``/``ls``/``glob``/
  ``grep`` against the real working tree, so the agent can run commands
  and modify project files in-place.
  
  This handles all default file operations and shell command execution
  within the project's working directory.

- ``/memories/`` → ``FilesystemBackend`` rooted at ``<cwd>/.koda/memories/``.
  Everything the agent writes under ``/memories/...`` lands on disk
  *inside the project being worked on*, so memories travel with the
  project and survive process restarts without needing an external
  store. Different projects get isolated `.koda/memories/` trees.
  
  This stores persistent project-specific memories that survive across
  agent sessions and process restarts.

- ``/skills/`` → ``FilesystemBackend`` rooted at ``coding_agent/skills/``
  (package-bundled). Read-mostly skill definitions ship with the agent
  and are available in every project; the agent reaches them via
  ``skills=['/skills/']`` on the deepagents factory.
  
  Skills are reusable functionality bundles (PDF, Word, Excel, etc.)
  bundled with the agent package and available across all projects.

All three routes are project-scoped *except* ``/skills/``, which is
intentionally global so skills are reusable across projects.

Permission gating is **not** done here. Mutating tools (``write_file`` /
``edit_file`` / ``multi_edit`` / ``execute``) are gated by LangGraph's
human-in-the-loop ``interrupt()`` via ``create_deep_agent(interrupt_on=…)``
in ``coding_agent/agent.py``. That pauses the whole graph (checkpointing
its state) instead of blocking a worker thread, so the TUI never freezes
while the user decides. See ``koda/tools/permissions.py`` for the policy
and ``koda/adapters/langgraph.py`` for the pause/resume plumbing.
"""

from __future__ import annotations  # Enable future Python type annotation features

from pathlib import Path  # Provides object-oriented filesystem paths

from deepagents.backends import (  # Import the backend ABSTRACT CLASSES from deepagents
    CompositeBackend,  # Backend that routes paths to different underlying backends
    FilesystemBackend,  # Backend that maps virtual paths to a real filesystem directory
    LocalShellBackend,  # Backend that executes shell commands on the local system
)
from deepagents.backends.protocol import BackendProtocol  # Import the backend interface protocol

# Skills ship with the package so the agent always has them available,
# regardless of which project directory it was launched in.
SKILLS_DIR = Path(__file__).parent / "skills"  # Path to the skills directory bundled with the package


def project_memories_dir(root: Path) -> Path:  # Function to compute the memories directory path for a project
    """Where ``/memories/*`` writes land on disk for the given project."""
    return root / ".koda" / "memories"  # Returns the path: <project_root>/.koda/memories


def build_backend(  # Factory function that constructs and configures the composite backend
    root: Path,  # The project's working directory path
    *,  # All following parameters are keyword-only
    timeout: int = 180,  # Maximum seconds to wait for shell commands before timing out (default: 3 minutes)
    inherit_env: bool = True,  # Whether spawned shell processes inherit the parent process's environment variables
) -> BackendProtocol:  # Returns a backend that the agent can use for file operations and command execution
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
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)  # Ensure skills directory exists (create if missing, including parents)
    memories_dir = project_memories_dir(root)  # Compute the memories directory path for this project
    memories_dir.mkdir(parents=True, exist_ok=True)  # Ensure memories directory exists (create if missing, including parents)

    default = LocalShellBackend(  # Create the default backend for file ops and shell commands
        root_dir=root,  # Set the project's working directory as the root for file operations
        virtual_mode=True,  # Use virtual filesystem mode (paths are virtualized, not raw OS paths)
        timeout=timeout,  # Set the command execution timeout
        inherit_env=inherit_env,  # Configure whether subshells inherit parent environment variables
    )
    memories = FilesystemBackend(  # Create the memories backend
        root_dir=memories_dir,  # Point to the project's memories directory
        virtual_mode=True,  # Use virtual filesystem mode
    )
    skills = FilesystemBackend(  # Create the skills backend
        root_dir=SKILLS_DIR,  # Point to the bundled skills directory
        virtual_mode=True,  # Use virtual filesystem mode
    )

    return CompositeBackend(  # Return a composite backend that routes paths to the appropriate backend
        default=default,  # Use the local shell backend as the default for unrouted paths
        routes={  # Map virtual path prefixes to their respective backends
            "/memories/": memories,  # Paths starting with /memories/ go to the memories backend
            "/skills/": skills,  # Paths starting with /skills/ go to the skills backend
        },
    )
