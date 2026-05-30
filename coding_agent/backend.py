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

import asyncio
from pathlib import Path

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
)
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

from koda.tools import permissions as _perms

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

    composite = CompositeBackend(
        default=default,
        routes={
            "/memories/": memories,
            "/skills/": skills,
        },
    )
    return _GatedBackend(composite)


class _GatedBackend(SandboxBackendProtocol):
    """Permission-gated proxy around a ``CompositeBackend``.

    Routes every mutating filesystem op (``write``/``edit``) and every
    shell execution (``execute``) through ``koda.tools.permissions.check``
    using the canonical KODA tool names from ``MUTATING_TOOLS``. When
    the gate refuses the call, the wrapper returns the refusal string in
    the appropriate result type so deepagents surfaces it to the model
    exactly like a normal tool failure — no need to override the tools
    themselves.

    Read paths (``ls`` / ``read`` / ``grep`` / ``glob``) bypass the gate.

    Subagents (``coding_agent/subagents.py``) share the parent backend
    by default, so the same gate applies inside the ``edit`` subagent
    too — no separate wiring needed.
    """

    def __init__(self, inner: CompositeBackend) -> None:
        self._inner = inner

    # Composite exposes a few non-protocol attributes (artifacts_root,
    # default, routes) some middleware reads directly. Fall through to
    # the inner backend for anything we don't explicitly override.
    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    # ── Reads (passthrough) ────────────────────────────────────────────

    def ls(self, path: str) -> LsResult:
        return self._inner.ls(path)

    async def als(self, path: str) -> LsResult:
        return await self._inner.als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._inner.read(file_path, offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._inner.aread(file_path, offset=offset, limit=limit)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return self._inner.grep(pattern, path, glob)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return await self._inner.agrep(pattern, path, glob)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return self._inner.glob(pattern, path)

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return await self._inner.aglob(pattern, path)

    # ── Mutations (gated) ──────────────────────────────────────────────

    def write(self, file_path: str, content: str) -> WriteResult:
        refusal = _perms.check("write_file", {"file_path": file_path})
        if refusal is not None:
            return WriteResult(error=refusal)
        return self._inner.write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        # Soft pause: if another tool call is currently holding a
        # permission prompt up, wait until the user resolves it before
        # we even ask the gate. Without this, parallel tool calls race
        # the prompt and stack multiple cards in the message stream.
        await _perms.wait_until_unpaused()
        # ``_perms.check`` may invoke the TUI permission modal via
        # ``call_from_thread`` (see ``KodaApp._prompt_from_tool_thread``),
        # which blocks the calling thread until the modal completes.
        # That's only safe from a *worker* thread — calling it on the
        # main asyncio loop would deadlock — so we push the gate check
        # to a thread via ``asyncio.to_thread``.
        refusal = await asyncio.to_thread(
            _perms.check, "write_file", {"file_path": file_path}
        )
        if refusal is not None:
            return WriteResult(error=refusal)
        return await self._inner.awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        refusal = _perms.check("edit_file", {"file_path": file_path})
        if refusal is not None:
            return EditResult(error=refusal)
        return self._inner.edit(file_path, old_string, new_string, replace_all=replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        await _perms.wait_until_unpaused()
        refusal = await asyncio.to_thread(
            _perms.check, "edit_file", {"file_path": file_path}
        )
        if refusal is not None:
            return EditResult(error=refusal)
        return await self._inner.aedit(file_path, old_string, new_string, replace_all=replace_all)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        refusal = _perms.check("execute", {"command": command})
        if refusal is not None:
            return ExecuteResponse(output=refusal, exit_code=1)
        if timeout is not None:
            return self._inner.execute(command, timeout=timeout)
        return self._inner.execute(command)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        await _perms.wait_until_unpaused()
        refusal = await asyncio.to_thread(
            _perms.check, "execute", {"command": command}
        )
        if refusal is not None:
            return ExecuteResponse(output=refusal, exit_code=1)
        if timeout is not None:
            return await self._inner.aexecute(command, timeout=timeout)
        return await self._inner.aexecute(command)

    # ── Bulk file transfer (passthrough; deepagents' MemoryMiddleware
    # calls ``adownload_files`` to load ``/AGENTS.md`` at the start of
    # every turn). These would otherwise inherit ``NotImplementedError``
    # from BackendProtocol since the inherited default raises and
    # ``__getattr__`` only fires for missing attributes.

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._inner.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await self._inner.aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._inner.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._inner.adownload_files(paths)
