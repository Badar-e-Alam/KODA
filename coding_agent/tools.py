"""Tools for the coding agent.

Highlights vs. the v0 version:
- Persistent shell: cwd and env survive across run_shell calls (cd src/ then pytest works).
- Output truncation: large outputs are capped at the head + tail with a notice in between.
- File pagination: read_file shows the head of large files and supports start_line/end_line.
- File-state awareness: edit_file/multi_edit refuse if the file hasn't been read this
  session, or was modified after the last read.
- multi_edit: apply N edits to one file atomically.
- Approval modes: yolo (auto), default (prompt on dangerous), safe (prompt on all writes).
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from agents import function_tool
from pydantic import BaseModel, Field

# ── Configuration ─────────────────────────────────────────────────────────

_SHELL_TIMEOUT = 60
_GREP_MAX_MATCHES = 200
_GREP_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build",
}
_MAX_OUTPUT_CHARS = 30_000
_FILE_HEAD_LINES = 200

WORKDIR = Path.cwd().resolve()

ApprovalMode = Literal["safe", "default", "yolo"]
_approval_mode: ApprovalMode = "default"


def set_approval_mode(mode: ApprovalMode) -> None:
    global _approval_mode
    _approval_mode = mode


_DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf?\b"),
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\b"),
    re.compile(r"\bcurl\b.*\|\s*sh\b"),
    re.compile(r"\bwget\b.*\|\s*sh\b"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bchmod\s+777\b"),
]


def _is_dangerous_shell(cmd: str) -> bool:
    return any(p.search(cmd) for p in _DANGEROUS_PATTERNS)


async def _approve(action_label: str, *, kind: Literal["shell", "write"]) -> bool:
    """Return True if the action is approved under the current mode."""
    if _approval_mode == "yolo":
        return True
    if _approval_mode == "default":
        if kind == "shell" and not _is_dangerous_shell(action_label):
            return True
        if kind == "write":
            # default mode does not prompt on writes
            return True
    # safe mode: prompt on everything; default mode: prompt on dangerous shell
    prompt = f"\n  approve {kind}: {action_label} ? [y/N] "
    try:
        ans = await asyncio.to_thread(input, prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return ans.strip().lower() in {"y", "yes"}


def _truncate(text: str, label: str = "output") -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    half = _MAX_OUTPUT_CHARS // 2
    omitted = len(text) - _MAX_OUTPUT_CHARS
    return (
        f"{text[:half]}"
        f"\n\n[... {omitted} chars truncated from {label} ...]\n\n"
        f"{text[-half:]}"
    )


# ── Persistent shell ──────────────────────────────────────────────────────


class _Shell:
    """A persistent shell session: cwd and env survive across run() calls."""

    _CWD_MARK = "__KODA_CWD__"
    _RC_MARK = "__KODA_RC__"

    def __init__(self) -> None:
        self.cwd: Path = WORKDIR
        self.env: dict[str, str] = os.environ.copy()

    def run(self, command: str, timeout: int = _SHELL_TIMEOUT) -> tuple[int, str]:
        # Sentinel-wrapped: bash runs the user command, then prints PWD and RC on
        # their own marker lines so we can recover them out of stdout.
        wrapped = (
            f"{command}\n"
            f"__rc__=$?\n"
            f"printf '\\n{self._CWD_MARK}%s\\n{self._RC_MARK}%s\\n' \"$PWD\" \"$__rc__\""
        )
        try:
            proc = subprocess.run(
                ["bash", "-c", wrapped],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.cwd),
                env=self.env,
            )
        except subprocess.TimeoutExpired:
            return 124, f"[timeout after {timeout}s]"

        rc = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")

        m_cwd = re.search(rf"\n{self._CWD_MARK}(.*)", out)
        if m_cwd:
            new_cwd = Path(m_cwd.group(1).strip())
            if new_cwd.is_dir():
                self.cwd = new_cwd
        m_rc = re.search(rf"\n{self._RC_MARK}(\d+)", out)
        if m_rc:
            try:
                rc = int(m_rc.group(1))
            except ValueError:
                pass

        # Strip everything from the first marker onward.
        out = re.split(rf"\n{self._CWD_MARK}", out, maxsplit=1)[0]
        return rc, out


_shell = _Shell()


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else _shell.cwd / p


# ── File-state tracking ──────────────────────────────────────────────────

_read_state: dict[str, float] = {}  # absolute path str -> mtime when last seen


def _record_seen(p: Path) -> None:
    try:
        _read_state[str(p.resolve())] = p.stat().st_mtime
    except OSError:
        pass


def _check_fresh(p: Path) -> str | None:
    """Return None if p was read this session and has not been modified since.
    Otherwise return an error message suitable to send back to the model."""
    key = str(p.resolve())
    if key not in _read_state:
        return f"[error] read {p} before editing it"
    try:
        current = p.stat().st_mtime
    except OSError:
        return f"[error] cannot stat {p}"
    if current > _read_state[key] + 1e-3:
        return f"[error] {p} changed since you read it. Re-read before editing."
    return None


# ── Tools ────────────────────────────────────────────────────────────────


@function_tool
async def run_shell(command: str) -> str:
    """Run a shell command in a persistent bash session.

    The working directory and environment **persist across calls** — `cd src/`
    followed by `pytest` works as you would expect. Combined stdout/stderr is
    returned along with the new cwd and exit code.

    Use this for: tests, builds, git, package managers, file system inspection,
    one-shot scripts.
    Don't use this for: reading files (use read_file) or editing files
    (use edit_file/multi_edit).

    Examples:
      run_shell("cd src && pytest -k test_login -x")
      run_shell("git status -s")
      run_shell("uv pip install httpx")
    """
    if not await _approve(command, kind="shell"):
        return "[denied] user declined to run this command"
    rc, out = _shell.run(command)
    out = _truncate(out, "shell output")
    return f"cwd={_shell.cwd}\nexit={rc}\n{out}"


@function_tool
def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a text file, returning lines in [start_line, end_line] (1-indexed,
    inclusive). If end_line is 0, read to EOF; if the file is large, only the
    first 200 lines are returned and a note instructs you to call read_file
    again with start_line/end_line to read more.

    Use this for: inspecting code before editing, checking config values.
    Don't use this for: searching across files (use grep) or binary files.
    """
    p = _resolve(path)
    if not p.exists():
        return f"[error] no such file: {path}"
    if not p.is_file():
        return f"[error] not a file: {path}"
    try:
        text = p.read_text()
    except UnicodeDecodeError:
        return f"[error] {path} is not UTF-8 text"

    _record_seen(p)

    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return "[empty file]"

    if end_line == 0:
        if start_line == 1 and total > _FILE_HEAD_LINES:
            shown = "\n".join(lines[:_FILE_HEAD_LINES])
            return (
                f"{shown}\n\n[... {total - _FILE_HEAD_LINES} more lines. "
                f"Call read_file with start_line/end_line to read more. "
                f"Total: {total} lines ...]"
            )
        end = total
    else:
        end = end_line

    start = max(1, start_line) - 1
    end = min(total, end)
    if start >= end:
        return "[empty range]"
    return _truncate("\n".join(lines[start:end]), f"{path}:{start_line}-{end}")


@function_tool
async def write_file(path: str, content: str) -> str:
    """Create a new file or overwrite an existing one. Creates parent dirs as
    needed.

    Use this for: brand-new files, complete rewrites.
    Don't use this for: small edits to existing files (use edit_file or
    multi_edit so you don't blow away surrounding content).
    """
    p = _resolve(path)
    if not await _approve(f"write {len(content)} chars to {path}", kind="write"):
        return "[denied] user declined to write this file"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    _record_seen(p)
    return f"wrote {len(content)} chars to {path}"


@function_tool
async def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    expected_matches: int = 1,
) -> str:
    """Replace `old_string` with `new_string` in `path`. Include 2-3 lines of
    context above/below the change so `old_string` is uniquely identifiable.

    By default the match must be unique; pass `expected_matches=N` to replace N
    identical occurrences when that is intentional.

    Refuses to edit a file that hasn't been read this session, or was modified
    since the last read — re-read first.

    Common failure: old_string not found. Causes: tabs vs. spaces, trailing
    whitespace, the line drifted. Re-read the file.
    """
    p = _resolve(path)
    if not p.is_file():
        return f"[error] no such file: {path}"
    err = _check_fresh(p)
    if err:
        return err
    text = p.read_text()
    n = text.count(old_string)
    if n == 0:
        return (
            f"[error] old_string not found in {path}. "
            "Common causes: tabs vs. spaces, trailing whitespace, or the line "
            "drifted. Re-read the file."
        )
    if n != expected_matches:
        return (
            f"[error] old_string matched {n} times in {path}, "
            f"expected {expected_matches}. Add more context or pass "
            f"expected_matches={n} if intentional."
        )
    label = f"edit {path} ({expected_matches} replacement{'s' if expected_matches != 1 else ''})"
    if not await _approve(label, kind="write"):
        return "[denied] user declined to apply this edit"
    p.write_text(text.replace(old_string, new_string, expected_matches))
    _record_seen(p)
    return f"edited {path} ({expected_matches} replacement{'s' if expected_matches != 1 else ''})"


class EditOp(BaseModel):
    """One edit operation in a multi_edit call."""
    old_string: str = Field(description="The exact text to replace.")
    new_string: str = Field(description="The replacement text.")
    expected_matches: int = Field(
        default=1,
        description="How many occurrences of old_string to replace. Default 1.",
    )


@function_tool
async def multi_edit(path: str, edits: list[EditOp]) -> str:
    """Apply several edits to one file atomically. Each edit specifies
    `old_string`, `new_string`, and an optional `expected_matches` (default 1).

    All edits must succeed or none are applied. Edits are applied in order to
    the running buffer — later edits see the result of earlier ones. Use this
    when you need to fix the same file in N places as one logical operation.
    """
    p = _resolve(path)
    if not p.is_file():
        return f"[error] no such file: {path}"
    err = _check_fresh(p)
    if err:
        return err
    text = p.read_text()
    applied: list[str] = []
    for i, e in enumerate(edits, start=1):
        old = e.old_string
        new = e.new_string
        expected = e.expected_matches
        if not old:
            return f"[error] edit #{i}: missing old_string"
        n = text.count(old)
        if n != expected:
            return (
                f"[error] edit #{i}: old_string matched {n} times, "
                f"expected {expected}. No edits applied."
            )
        text = text.replace(old, new, expected)
        applied.append(f"#{i}: {expected}")

    label = f"multi_edit {path} ({len(edits)} edits)"
    if not await _approve(label, kind="write"):
        return "[denied] user declined to apply these edits"
    p.write_text(text)
    _record_seen(p)
    return f"edited {path}: " + ", ".join(applied) + " replacement(s)"


@function_tool
def find_files(name_pattern: str, path: str = ".") -> str:
    """Find files by **name pattern** (shell glob, e.g. `*.py`, `client*`,
    `*config*`). Walks the tree under `path` and returns matching file paths,
    one per line. Skips .git, venv, node_modules, dist, etc.

    Use this when:
      - The user gave a filename and you need to confirm where it lives or
        whether it exists at all.
      - You suspect a typo or singular/plural drift (`clients.py` vs
        `client.py`) — widen the pattern: `find_files("*client*")`.
      - You don't yet know the project layout and need to locate something
        before reading it.

    Don't use this when:
      - You already know the exact path (just read_file directly).
      - You want to search file *contents* (use grep).

    Examples:
      find_files("*.py", "src/")
      find_files("*client*")
      find_files("Dockerfile*")
    """
    root = _resolve(path)
    if not root.exists():
        return f"[error] no such path: {path}"
    if root.is_file():
        return str(root)
    matches: list[str] = []
    try:
        for f in root.rglob(name_pattern):
            if not f.is_file():
                continue
            if any(part in _GREP_SKIP_DIRS for part in f.parts):
                continue
            matches.append(str(f))
            if len(matches) >= _GREP_MAX_MATCHES:
                matches.append(f"[truncated at {_GREP_MAX_MATCHES} matches]")
                break
    except OSError as e:
        return f"[error] {e}"
    return "\n".join(matches) if matches else "[no matches]"


@function_tool
def grep(pattern: str, path: str = ".", glob: str = "*") -> str:
    """Search for a regex pattern across files. Returns matching lines as
    `file:line:text`. Skips .git, venv, node_modules, etc.

    Use this for:
      - Finding where a function/class/symbol is defined or used.
      - Locating config values, error messages, TODO comments.
    Don't use this for:
      - Reading a known file (use read_file).
      - Open-ended semantic questions like "where is auth handled" — those
        are dispatch_subagent territory.

    Examples:
      grep("def login", "src/")
      grep("TODO|FIXME", ".", "*.py")
    """
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"[error] invalid regex: {e}"
    root = _resolve(path)
    if not root.exists():
        return f"[error] no such path: {path}"

    matches: list[str] = []
    if root.is_file():
        files: Any = [root]
    else:
        files = (
            f for f in root.rglob(glob)
            if f.is_file()
            and not any(part in _GREP_SKIP_DIRS for part in f.parts)
        )

    for f in files:
        try:
            for i, line in enumerate(f.read_text().splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{f}:{i}:{line}")
                    if len(matches) >= _GREP_MAX_MATCHES:
                        matches.append(f"[truncated at {_GREP_MAX_MATCHES} matches]")
                        return _truncate("\n".join(matches), "grep")
        except (UnicodeDecodeError, OSError):
            continue
    return _truncate("\n".join(matches), "grep") if matches else "[no matches]"


# ── Plan / scratchpad ────────────────────────────────────────────────────

_todos: list[dict[str, Any]] = []


@function_tool
def todo_write(tasks: list[str]) -> str:
    """Replace the current plan with these tasks (all marked pending).

    Call this at the start of any non-trivial task. Re-call it whenever the
    situation has changed — plans drift, and explicit re-evaluation prevents
    tunnel vision. Have at most one item in_progress at a time.
    """
    global _todos
    _todos = [
        {"id": i, "task": t, "status": "pending"}
        for i, t in enumerate(tasks, start=1)
    ]
    return _render_todos()


@function_tool
def todo_update(task_id: int, status: str) -> str:
    """Update one todo's status. Status: pending | in_progress | completed."""
    if status not in {"pending", "in_progress", "completed"}:
        return f"[error] invalid status: {status}"
    for t in _todos:
        if t["id"] == task_id:
            t["status"] = status
            return _render_todos()
    return f"[error] no task with id {task_id}"


@function_tool
def think(thought: str) -> str:
    """Scratchpad for reasoning. Use to plan steps before acting. No side effects."""
    return "noted"


def _render_todos() -> str:
    if not _todos:
        return "[empty]"
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    return "\n".join(
        f"{marks[t['status']]} {t['id']}. {t['task']}" for t in _todos
    )
