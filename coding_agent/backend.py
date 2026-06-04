"""Backend construction for `coding_agent`.

Splits filesystem responsibilities across three storage strategies via
``CompositeBackend`` 
(see https://docs.langchain.com/oss/python/deepagents/backends):

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

Permission gating is **not** done here. Mutating tools (``write_file`` /
``edit_file`` / ``multi_edit`` / ``execute``) are gated by LangGraph's
human-in-the-loop ``interrupt()`` via ``create_deep_agent(interrupt_on=…)``
in ``coding_agent/agent.py``. That pauses the whole graph (checkpointing
its state) instead of blocking a worker thread, so the TUI never freezes
while the user decides. See ``koda/tools/permissions.py`` for the policy
and ``koda/adapters/langgraph.py`` for the pause/resume plumbing.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
)
from deepagents.backends.protocol import BackendProtocol, ExecuteResponse

# Skills ship with the package so the agent always has them available,
# regardless of which project directory it was launched in.
SKILLS_DIR = Path(__file__).parent / "skills"


class ReapingShellBackend(LocalShellBackend):
    """``LocalShellBackend`` that reaps the whole process tree on timeout.

    The stock backend runs commands via ``subprocess.run(shell=True,
    timeout=…)``. When the timeout fires, Python kills only the immediate
    ``/bin/sh`` wrapper — any grandchild it spawned (e.g. a ``python3 -c``
    that the shell didn't exec-replace) is **orphaned** and keeps running,
    reparented to init/launchd. We once had such an orphan walk the entire
    filesystem (``glob('/**/.env', recursive=True)``) for 75 minutes after
    its 180 s timeout had "expired", pegging a CPU core and flooding the
    terminal it had inherited.

    The fix: launch each command in its **own session** (``start_new_session=
    True`` → new process group) and, on timeout, ``killpg`` the entire group
    so children and grandchildren die together. Output formatting matches the
    parent so callers can't tell the difference on the happy path.
    """

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command or not isinstance(command, str):
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )

        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)

        try:
            proc = subprocess.Popen(  # noqa: S602
                command,
                shell=True,  # Intentional: LLM-controlled shell execution.
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._env,
                cwd=str(self.cwd),
                # New session → the shell and everything it spawns share a
                # process group we can signal as a unit.
                start_new_session=True,
            )
        except Exception as e:  # noqa: BLE001
            return ExecuteResponse(
                output=f"Error executing command ({type(e).__name__}): {e}",
                exit_code=1,
                truncated=False,
            )

        try:
            stdout, stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            self._kill_group(proc)
            # Drain whatever was buffered so file descriptors close cleanly.
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            if timeout is not None:
                msg = f"Error: Command timed out after {effective_timeout} seconds (custom timeout). The command may be stuck or require more time."
            else:
                msg = f"Error: Command timed out after {effective_timeout} seconds. For long-running commands, re-run using the timeout parameter."
            return ExecuteResponse(output=msg, exit_code=124, truncated=False)
        except Exception as e:  # noqa: BLE001
            self._kill_group(proc)
            return ExecuteResponse(
                output=f"Error executing command ({type(e).__name__}): {e}",
                exit_code=1,
                truncated=False,
            )

        output_parts = []
        if stdout:
            output_parts.append(stdout)
        if stderr:
            stderr_lines = stderr.strip().split("\n")
            output_parts.extend(f"[stderr] {line}" for line in stderr_lines)
        output = "\n".join(output_parts) if output_parts else "<no output>"

        truncated = False
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            truncated = True

        if proc.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {proc.returncode}"

        return ExecuteResponse(
            output=output,
            exit_code=proc.returncode or 0,
            truncated=truncated,
        )

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        """SIGTERM then SIGKILL the process group led by ``proc``."""
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                continue


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

    default = ReapingShellBackend(
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
