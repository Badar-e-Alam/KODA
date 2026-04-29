import subprocess
import re
from pathlib import Path
from agents import function_tool



@function_tool
def run_shell(command: str, timeout: int = 30) -> str:
    """Run a shell command. Returns exit code, stdout, and stderr."""
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return f"exit={r.returncode}\n--stdout--\n{r.stdout}\n--stderr--\n{r.stderr}"
    except subprocess.TimeoutExpired:
        return f"timeout after {timeout}s"



@function_tool
def read_file(path: str, start_line: int = 1, end_line: int = -1) -> str:
    """Read a text file with optional line range (1-indexed, inclusive).

    Use start_line and end_line to read just a slice of large files instead
    of the whole thing. end_line=-1 means read to the end.
    Returns lines prefixed with their line numbers.
    """
    lines = Path(path).read_text().splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = total if end_line == -1 else min(total, end_line)
    if start > total:
        return f"file has {total} lines, start_line={start} is out of range"
    chunk = lines[start - 1:end]
    width = len(str(end))
    numbered = "\n".join(f"{i + start:>{width}}  {line}" for i, line in enumerate(chunk))
    return f"# {path} lines {start}-{end} of {total}\n{numbered}"


@function_tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with the given content."""
    Path(path).write_text(content)
    return f"wrote {len(content)} chars to {path}"


@function_tool
def edit_file(path: str, old: str, new: str) -> str:
    """Replace `old` with `new` in `path`. `old` must match exactly once."""
    p = Path(path)
    text = p.read_text()
    n = text.count(old)
    if n != 1:
        return f"error: `old` matched {n} times, need exactly 1"
    p.write_text(text.replace(old, new))
    return f"edited {path}"



@function_tool
def grep(
    pattern: str,
    path: str = ".",
    glob: str = "*",
    max_results: int = 50,
) -> str:
    """Search for a regex pattern in files under `path`.

    Returns matching lines as `filepath:lineno: line`. Use this to find
    where things are defined or used before reading whole files.

    Args:
        pattern: Regex to search for.
        path: Directory to search in (recursive). Defaults to cwd.
        glob: Filename glob like '*.py' or '*.{js,ts}'. Defaults to '*'.
        max_results: Cap on hits returned.
    """
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"bad regex: {e}"

    root = Path(path)
    if not root.exists():
        return f"path not found: {path}"

    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    hits = []

    for f in root.rglob(glob):
        if not f.is_file() or any(part in skip_dirs for part in f.parts):
            continue
        try:
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{f}:{i}: {line.rstrip()}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + f"\n... (capped at {max_results})"
        except (OSError, UnicodeDecodeError):
            continue

    return "\n".join(hits) if hits else "no matches"


_TODOS: list[dict] = []


@function_tool
def todo_write(items: list[str]) -> str:
    """Replace the todo list with a fresh set of tasks.

    Call this at the start of a multi-step task to plan, and again whenever
    the plan changes. Each item starts as 'pending'.
    """
    global _TODOS
    _TODOS = [{"id": i + 1, "task": t, "status": "pending"} for i, t in enumerate(items)]
    return _render_todos()


@function_tool
def todo_update(task_id: int, status: str) -> str:
    """Update one todo's status.

    Args:
        task_id: The id of the todo (1-indexed).
        status: One of 'pending', 'in_progress', 'done'.
    """
    if status not in {"pending", "in_progress", "done"}:
        return "status must be pending, in_progress, or done"
    for t in _TODOS:
        if t["id"] == task_id:
            t["status"] = status
            return _render_todos()
    return f"no todo with id {task_id}"


def _render_todos() -> str:
    if not _TODOS:
        return "(no todos)"
    marks = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
    return "\n".join(f"{marks[t['status']]} {t['id']}. {t['task']}" for t in _TODOS)



@function_tool
def think(thought: str) -> str:
    """Scratchpad for reasoning. Use before actions or when stuck.

    Nothing is executed — the act of writing forces structured thinking
    and the thought stays in the conversation for later steps to reference.
    Use for: planning an approach, debugging hypotheses, weighing trade-offs.
    """
    return f"noted: {thought[:80]}{'...' if len(thought) > 80 else ''}"

