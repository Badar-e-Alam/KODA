"""
Filesystem + shell tools for the KODA default agent.

Replaces `deepagents.backends.FilesystemBackend` with plain LangChain `@tool`
functions. All operations are jailed to a workspace root (default:
`./agent_workspace`). Paths starting with `/` resolve relative to the
workspace root so model prompts that assume absolute paths keep working.

Tools:
  - ls, read_file, write_file, edit_file, glob, grep, execute
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from langchain.tools import tool

from koda.tools.permissions import check as _permission_check

# ── Workspace root ─────────────────────────────────────────────────────

_DEFAULT_ROOT = Path(os.environ.get(
    "KODA_WORKSPACE",
    Path.cwd() / "agent_workspace",
)).resolve()
_DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)

_ROOT = _DEFAULT_ROOT
# Tracks files the agent has read at least once — edits require a prior read.
_READ_CACHE: set[str] = set()


def set_workspace_root(root: str | Path) -> None:
    """Reset the workspace root. Called once at agent creation."""
    global _ROOT
    _ROOT = Path(root).resolve()
    _ROOT.mkdir(parents=True, exist_ok=True)
    _READ_CACHE.clear()


def _resolve(p: str) -> Path:
    """Resolve a model-supplied path to a real filesystem path inside the jail.

    '/foo/bar'  -> {root}/foo/bar
    'foo/bar'   -> {root}/foo/bar
    """
    raw = p.lstrip("/").replace("\\", "/") if p.startswith("/") else p
    candidate = _ROOT / raw
    # Reject symlinks along the path — resolve() would follow them out of the jail.
    probe = candidate
    while True:
        if probe.is_symlink():
            raise ValueError(f"Path traverses a symlink: {p!r}")
        if probe == probe.parent:
            break
        probe = probe.parent
        if not probe.exists():
            continue
        try:
            probe.relative_to(_ROOT)
        except ValueError:
            break
    full = candidate.resolve()
    try:
        full.relative_to(_ROOT)
    except ValueError as e:
        raise ValueError(f"Path escapes workspace root: {p!r}") from e
    return full


def _rel(p: Path) -> str:
    """Format a real path as a model-visible absolute path."""
    try:
        return "/" + str(p.relative_to(_ROOT)).replace(os.sep, "/")
    except ValueError:
        return str(p)


# ── ls ──────────────────────────────────────────────────────────────────

@tool
def ls(path: str = "/") -> str:
    """List entries in a directory. Use before read_file/edit_file to orient.

    Args:
        path: Absolute path (starting with '/'). '/' = workspace root.
    """
    target = _resolve(path)
    if not target.exists():
        return f"Error: path not found: {path}"
    if not target.is_dir():
        return f"Error: not a directory: {path}"
    entries = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name)):
        marker = "/" if child.is_dir() else ""
        entries.append(f"{_rel(child)}{marker}")
    if not entries:
        return f"(empty directory: {path})"
    return "\n".join(entries)


# ── read_file ───────────────────────────────────────────────────────────

@tool
def read_file(file_path: str, offset: int = 0, limit: int = 100) -> str:
    """Read a file with line numbers (cat -n format). Paginate large files.

    Args:
        file_path: Absolute path starting with '/'.
        offset: 0-indexed line number to start reading from.
        limit: Maximum number of lines to return.
    """
    target = _resolve(file_path)
    if not target.exists():
        return f"Error: file not found: {file_path}"
    if not target.is_file():
        return f"Error: not a regular file: {file_path}"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading file: {e}"

    _READ_CACHE.add(str(target))
    lines = text.splitlines()
    end = offset + limit
    chunk = lines[offset:end]
    numbered = [f"{i + 1:>6}\t{line}" for i, line in enumerate(chunk, start=offset)]
    header = ""
    if len(lines) > end:
        header = f"(showing lines {offset + 1}-{end} of {len(lines)})\n"
    elif offset > 0:
        header = f"(showing lines {offset + 1}-{len(lines)} of {len(lines)})\n"
    return header + "\n".join(numbered) if numbered else "(empty file)"


# ── write_file ──────────────────────────────────────────────────────────

@tool
def write_file(file_path: str, content: str) -> str:
    """Create or overwrite a file. Prefer edit_file for existing files.

    Args:
        file_path: Absolute path starting with '/'.
        content: Text content to write.
    """
    refusal = _permission_check("write_file", {"file_path": file_path, "content": content})
    if refusal:
        return refusal
    target = _resolve(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"Error writing file: {e}"
    _READ_CACHE.add(str(target))  # written implies "known"
    return f"Wrote {len(content)} chars to {file_path}"


# ── edit_file ──────────────────────────────────────────────────────────

@tool
def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Exact-string replacement in an existing file. Must read_file first.

    Args:
        file_path: Absolute path starting with '/'.
        old_string: Exact substring to replace.
        new_string: Replacement text.
        replace_all: If True, replace every occurrence. Else old_string must be unique.
    """
    refusal = _permission_check(
        "edit_file",
        {"file_path": file_path, "old_string": old_string, "new_string": new_string,
         "replace_all": replace_all},
    )
    if refusal:
        return refusal
    target = _resolve(file_path)
    if not target.exists():
        return f"Error: file not found: {file_path}"
    if str(target) not in _READ_CACHE:
        return (
            f"Error: read_file({file_path!r}) before edit_file. "
            "This prevents clobbering unknown content."
        )
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading file: {e}"

    if old_string == new_string:
        return "Error: old_string and new_string are identical."
    count = text.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {file_path}."
    if count > 1 and not replace_all:
        return (
            f"Error: old_string appears {count} times in {file_path}. "
            "Either provide more context to make it unique or set replace_all=True."
        )
    new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    try:
        target.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return f"Error writing file: {e}"
    n = count if replace_all else 1
    return f"Edited {file_path} ({n} replacement{'s' if n != 1 else ''})"


# ── glob ───────────────────────────────────────────────────────────────

@tool
def glob(pattern: str, path: str = "/") -> str:
    """Find files matching a glob pattern, sorted by modification time (newest first).

    Args:
        pattern: e.g. '**/*.py', '*.txt', 'subdir/**/*.md'.
        path: Base directory (absolute, starts with '/'). Default '/'.
    """
    base = _resolve(path)
    if not base.exists() or not base.is_dir():
        return f"Error: base path not a directory: {path}"
    matches = [p for p in base.rglob(pattern.lstrip("/")) if p.is_file()]
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        return f"(no files match {pattern!r} under {path})"
    return "\n".join(_rel(p) for p in matches[:500])


# ── grep ───────────────────────────────────────────────────────────────

@tool
def grep(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    output_mode: str = "files_with_matches",
) -> str:
    """Search for literal text across files (not regex).

    Args:
        pattern: Literal string to search for.
        path: Directory to search (absolute, starts with '/'). Default = root.
        glob: Filename pattern to filter (e.g. '*.py').
        output_mode: 'files_with_matches' | 'content' | 'count'.
    """
    base = _resolve(path) if path else _ROOT
    if not base.exists():
        return f"Error: path not found: {path}"

    files_to_scan: list[Path]
    if base.is_file():
        files_to_scan = [base]
    else:
        files_to_scan = [p for p in base.rglob("*") if p.is_file()]
    if glob:
        files_to_scan = [p for p in files_to_scan if fnmatch.fnmatch(p.name, glob)]

    matches_by_file: dict[str, list[tuple[int, str]]] = {}
    for f in files_to_scan:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = [(i, line) for i, line in enumerate(text.splitlines(), start=1) if pattern in line]
        if hits:
            matches_by_file[_rel(f)] = hits

    if not matches_by_file:
        return f"(no matches for {pattern!r})"

    if output_mode == "files_with_matches":
        return "\n".join(sorted(matches_by_file.keys()))
    if output_mode == "count":
        return "\n".join(f"{path}: {len(hits)}" for path, hits in sorted(matches_by_file.items()))
    # content mode
    out: list[str] = []
    for path, hits in sorted(matches_by_file.items()):
        for lineno, line in hits[:50]:
            out.append(f"{path}:{lineno}:{line}")
    return "\n".join(out) if out else "(no content matches)"


# ── execute (shell) ────────────────────────────────────────────────────

@tool
def execute(command: str, timeout: int | None = 120) -> str:
    """Run a shell command. Returns combined stdout/stderr plus exit code.

    Args:
        command: Shell command to run. Use absolute paths.
        timeout: Seconds before the command is killed. None for no timeout.
    """
    refusal = _permission_check("execute", {"command": command, "timeout": timeout})
    if refusal:
        return refusal
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout if timeout and timeout > 0 else None,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except OSError as e:
        return f"Error: {e}"
    out = (result.stdout or "") + (result.stderr or "")
    return f"[exit {result.returncode}]\n{out[:8000]}"


ALL_TOOLS = [ls, read_file, write_file, edit_file, glob, grep, execute]
