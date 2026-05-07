import json
import os
import subprocess
import re
from pathlib import Path

from pydantic import BaseModel
from agents import function_tool

from memory import (
    VALID_TYPES as _MEMORY_TYPES,
    get_memory_store as _get_memory_store,
)


class FindReplace(BaseModel):
    """One find/replace operation for `multi_edit`."""
    old: str
    new: str


# ── Approval system ────────────────────────────────────────────────────
#
# `_APPROVAL_MODE` controls what `run_shell` is allowed to execute.
#   yolo  — run anything (default; matches prior behavior).
#   auto  — run only commands matching the safe-command allowlist below.
#   ask   — block all commands; the model must request a mode change.
# The TUI adapter sets "yolo" at startup so it doesn't deadlock on prompts;
# CLI users can call `set_approval_mode("auto")` to enable the allowlist.
_APPROVAL_MODE = "yolo"

_DEFAULT_ALLOWLIST = [
    r"^\s*ls(\s|$)",
    r"^\s*pwd\s*$",
    r"^\s*echo\s+",
    r"^\s*cat\s+",
    r"^\s*head\s+",
    r"^\s*tail\s+",
    r"^\s*wc\s+",
    r"^\s*file\s+",
    r"^\s*git\s+(status|log|diff|blame|show|branch|remote|rev-parse|config\s+--get|describe)\b",
    r"^\s*rg\s+",
    r"^\s*grep\s+",
    r"^\s*find\s+\S+\s+-(name|type|maxdepth)\b",
    r"^\s*node\s+(-v|--version)\s*$",
    r"^\s*npm\s+(-v|--version|list|ls)(\s|$)",
    r"^\s*python3?\s+(-V|--version)\s*$",
    r"^\s*command\s+-v\s+",
    r"^\s*which\s+",
    r"^\s*test\s+-",
]


def set_approval_mode(mode: str) -> str:
    """Set the approval mode used by `run_shell`. Returns the new mode."""
    global _APPROVAL_MODE
    if mode not in {"yolo", "auto", "ask"}:
        return f"unknown mode: {mode}; use yolo|auto|ask"
    _APPROVAL_MODE = mode
    return f"approval mode = {mode}"


def get_approval_mode() -> str:
    return _APPROVAL_MODE


def _is_allowlisted(command: str) -> bool:
    return any(re.search(p, command) for p in _DEFAULT_ALLOWLIST)


def _enriched_env() -> dict[str, str]:
    """Return os.environ with user-local toolchain bins prepended to PATH.

    `subprocess.run(..., shell=True)` invokes /bin/sh, which never sources
    ~/.bashrc, so version-manager-installed tools (nvm, pyenv, cargo, pipx)
    are invisible by default. Prepend their bin dirs so the agent can run
    things it just installed without the user needing to restart koda.
    """
    home = Path.home()
    candidates: list[str] = []
    nvm_versions = home / ".nvm" / "versions" / "node"
    if nvm_versions.is_dir():
        # Pick the highest version dir; nvm names are like v24.15.0.
        versions = sorted(
            (p for p in nvm_versions.iterdir() if p.is_dir() and (p / "bin" / "node").exists()),
            key=lambda p: p.name,
        )
        if versions:
            candidates.append(str(versions[-1] / "bin"))
    for sub in (".local/bin", ".cargo/bin", ".pyenv/shims", ".pyenv/bin", ".bun/bin", ".deno/bin"):
        d = home / sub
        if d.is_dir():
            candidates.append(str(d))

    env = os.environ.copy()
    existing = env.get("PATH", "")
    parts = [c for c in candidates if c and c not in existing.split(os.pathsep)]
    if parts:
        env["PATH"] = os.pathsep.join(parts + ([existing] if existing else []))
    return env


@function_tool
def run_shell(command: str, timeout: int = 30) -> str:
    """Run a shell command. Returns exit code, stdout, and stderr.

    Behavior depends on the approval mode (see `set_approval_mode`):
      yolo — execute as-is.
      auto — execute only if the command matches the safe-command allowlist;
             otherwise return a [blocked] message so the model can rephrase.
      ask  — refuse all commands; the user must explicitly switch modes first.
    """
    if _APPROVAL_MODE == "ask":
        return (
            f"[blocked] approval mode is 'ask'; cannot run: {command}\n"
            f"Tell the user a shell command is required, then wait."
        )
    if _APPROVAL_MODE == "auto" and not _is_allowlisted(command):
        return (
            f"[blocked] command not on safe allowlist (mode=auto): {command}\n"
            f"Either narrow it to a read-only equivalent (ls/cat/git status/etc) "
            f"or ask the user to switch to 'yolo' mode."
        )
    try:
        r = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_enriched_env(),
        )
        return f"exit={r.returncode}\n--stdout--\n{r.stdout}\n--stderr--\n{r.stderr}"
    except subprocess.TimeoutExpired:
        return f"timeout after {timeout}s"

@function_tool
def read_file(path: str, start_line: int = 1, end_line: int = -1) -> str:
    """
    Read a text file with optional line range (1-indexed, inclusive).

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
    """Replace `old` with `new` in `path`. `old` must match exactly once.

    Fails loudly when `old` is not unique — DO NOT retry with the same string.
    Either: (a) widen `old` with more surrounding context to disambiguate, or
    (b) use `multi_edit` if you want to change every occurrence.
    """
    p = Path(path)
    if not p.exists():
        return f"[error] file not found: {path}"
    text = p.read_text()
    n = text.count(old)
    if n == 0:
        return (
            f"[error] `old` did not match any text in {path}. "
            f"Read the file first to confirm the exact characters (whitespace, line endings, casing)."
        )
    if n > 1:
        return (
            f"[error] `old` matched {n} places in {path}; need exactly 1. "
            f"Add more surrounding context to make `old` unique, or use `multi_edit`."
        )
    p.write_text(text.replace(old, new))
    return f"edited {path}"

@function_tool
def grep( pattern: str, path: str = ".", glob: str = "*", max_results: int = 50,) -> str:
    """
    Search for a regex pattern in files under `path`.

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

    Plan quality rules:
    - Do NOT submit shallow plans like "create html, create css, create js".
      Before calling this, use `think` to decide the design and approach.
    - Each todo should describe an outcome, not a file
      (good: "Build hero with animated gradient and typewriter intro";
       bad: "create index.html").
    - For UI work, the FIRST todo should commit to a concrete visual direction
      (palette, typography, layout, motion) rather than leaving it implicit.
    - The LAST todos MUST be verification: open the artifact, run it, exercise
      the API, screenshot at desktop+mobile, hit the endpoint, etc. Plans that
      end at "implementation done" are incomplete.
    - If verification fails, add a follow-up todo and re-verify; don't mark
      the final task done until the deliverable actually works.
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


# ── multi_edit ──────────────────────────────────────────────────────────


@function_tool
def multi_edit(path: str, edits: list[FindReplace]) -> str:
    """Apply multiple find/replace edits to one file atomically.

    All edits succeed or none are written — if any `old` fails to match
    uniquely (in the file state *after* prior edits in this batch), the
    file is left untouched and an error is returned.

    Args:
        path: target file.
        edits: list of {"old": str, "new": str} entries, applied in order.
    """
    p = Path(path)
    if not p.exists():
        return f"[error] file not found: {path}"
    text = p.read_text()
    original = text
    for i, e in enumerate(edits, 1):
        n = text.count(e.old)
        if n != 1:
            return (
                f"[error] edit {i}: `old` matched {n} times in current state, need exactly 1. "
                f"No changes written — widen `old` with surrounding context."
            )
        text = text.replace(e.old, e.new)
    if text == original:
        return "no changes (edits resolved to no-op)"
    p.write_text(text)
    return f"applied {len(edits)} edits to {path}"


# ── glob_files ─────────────────────────────────────────────────────────


@function_tool
def glob_files(pattern: str, path: str = ".", max_results: int = 200) -> str:
    """Find files by *name* using a glob pattern.

    Use this for filename search; use `grep` for content search.
    Pattern uses pathlib glob semantics: `**` matches any depth.
    Examples: `**/*.test.ts`, `src/**/*.py`, `*.md`, `koda/**/*.py`.
    """
    root = Path(path)
    if not root.exists():
        return f"path not found: {path}"
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".turbo"}
    hits: list[str] = []
    try:
        iterator = root.glob(pattern)
    except (ValueError, OSError) as e:
        return f"[error] bad pattern: {e}"
    for p in iterator:
        if any(part in skip for part in p.parts):
            continue
        if p.is_file():
            hits.append(str(p))
            if len(hits) >= max_results:
                return "\n".join(hits) + f"\n... (capped at {max_results})"
    return "\n".join(hits) if hits else "no matches"


# ── web_fetch ──────────────────────────────────────────────────────────


@function_tool
def web_fetch(url: str, max_chars: int = 20_000) -> str:
    """Fetch a URL and return its text content (truncated to `max_chars`).

    Use when you need to read external docs, an API reference, a Stack
    Overflow answer, or any page to inform your work. HTML is stripped to
    body text. Set `max_chars` higher for long pages, lower to save tokens.
    """
    try:
        import httpx
    except ImportError:
        return "[error] httpx not installed; cannot fetch URLs"
    try:
        r = httpx.get(
            url,
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "koda-coding-agent/1.0"},
        )
    except Exception as e:
        return f"[error] fetch failed: {e}"
    if r.status_code >= 400:
        return f"[error] HTTP {r.status_code} for {url}"
    text = r.text
    ct = (r.headers.get("content-type") or "").lower()
    if "html" in ct:
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n[ \t]*\n+", "\n\n", text).strip()
    overflow = max(0, len(text) - max_chars)
    if overflow:
        text = text[:max_chars] + f"\n... [truncated, {overflow} chars omitted]"
    return f"# {url} ({r.status_code}, {ct or 'unknown content-type'})\n{text}"


# ── git tools ──────────────────────────────────────────────────────────


def _git(args: list[str], cwd: str = ".") -> str:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return "[error] git is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return "[error] git timed out"
    if r.returncode != 0 and not r.stdout:
        return f"[error] git {' '.join(args)} exit={r.returncode}\n{r.stderr.strip()}"
    out = r.stdout
    if r.stderr.strip():
        out += f"\n[stderr]\n{r.stderr.strip()}"
    return out or "(no output)"


@function_tool
def git_status(path: str = ".") -> str:
    """Show working-tree status with branch info (short format)."""
    return _git(["status", "--short", "--branch"], cwd=path)


@function_tool
def git_diff(path: str = ".", staged: bool = False, file: str = "") -> str:
    """Show unified diff of unstaged (default) or staged changes.

    Args:
        path: repo root.
        staged: True for staged diff, False for unstaged.
        file: optional path to limit the diff to one file.
    """
    args = ["diff"] + (["--cached"] if staged else [])
    if file:
        args += ["--", file]
    return _git(args, cwd=path)


@function_tool
def git_log(n: int = 10, path: str = ".", file: str = "") -> str:
    """Show the last N commits as `<short-sha> <author> <date> <subject>`.

    Args:
        n: how many commits.
        path: repo root.
        file: optional path to limit history to one file.
    """
    args = ["log", f"-{max(1, n)}", "--pretty=format:%h %an %ad %s", "--date=short"]
    if file:
        args += ["--", file]
    return _git(args, cwd=path)


@function_tool
def git_blame(file: str, line_start: int = 1, line_end: int = 0, path: str = ".") -> str:
    """Show git blame for a file, optionally limited to a line range.

    Args:
        file: file path to blame.
        line_start: first line (1-indexed). Default 1.
        line_end: last line; 0 means line_start + 200.
        path: repo root.
    """
    end = line_end if line_end and line_end >= line_start else line_start + 200
    return _git(["blame", "--date=short", "-L", f"{line_start},{end}", file], cwd=path)


# ── run_tests ──────────────────────────────────────────────────────────


def _detect_test_framework(root: Path) -> str:
    if (root / "pytest.ini").exists() or (root / "tests").is_dir():
        return "pytest"
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            txt = pyproject.read_text()
            if "[tool.pytest" in txt or "pytest" in txt:
                return "pytest"
        except OSError:
            pass
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            deps = {**data.get("devDependencies", {}), **data.get("dependencies", {})}
            if "jest" in deps:
                return "jest"
            if data.get("scripts", {}).get("test"):
                return "npm-test"
        except (OSError, json.JSONDecodeError):
            pass
    if (root / "Cargo.toml").exists():
        return "cargo"
    if (root / "go.mod").exists():
        return "go"
    return ""


def _summarize_tests(framework: str, output: str) -> str:
    if framework == "pytest":
        m = re.search(r"^=+ (.+?) =+\s*$", output, flags=re.M)
        return m.group(1) if m else "(no pytest summary line)"
    if framework == "jest":
        tests = re.search(r"^Tests:\s+(.+)$", output, flags=re.M)
        suites = re.search(r"^Test Suites:\s+(.+)$", output, flags=re.M)
        parts = []
        if suites: parts.append(f"suites: {suites.group(1)}")
        if tests: parts.append(f"tests: {tests.group(1)}")
        return ", ".join(parts) if parts else "(no jest summary)"
    if framework == "cargo":
        m = re.search(r"test result: (.+)", output)
        return m.group(1) if m else "(no cargo summary)"
    if framework == "go":
        if "FAIL" in output:
            fails = re.findall(r"--- FAIL: (\S+)", output)
            return f"FAIL ({len(fails)} failing): {', '.join(fails[:5])}"
        return "ok" if "ok" in output else "(no go summary)"
    return "(parser not implemented)"


@function_tool
def run_tests(framework: str = "auto", args: str = "", path: str = ".") -> str:
    """Run the project's test suite and return a structured summary.

    `framework='auto'` (default) detects pytest / jest / cargo / go / npm-test
    by inspecting the project root. Override to force a specific runner.
    The result includes: framework, exit code, summary line, and the tail
    of stdout/stderr (last ~4 KB) so the model can read failure details
    without ballooning the context.
    """
    root = Path(path)
    fw = framework if framework != "auto" else _detect_test_framework(root)
    if not fw:
        return "[error] could not auto-detect a test framework; pass framework= explicitly"

    if fw == "pytest":
        cmd = f"pytest --tb=short -q {args}".strip()
    elif fw == "jest":
        cmd = f"npx --yes jest --silent {args}".strip()
    elif fw == "npm-test":
        cmd = f"npm test --silent {args}".strip()
    elif fw == "cargo":
        cmd = f"cargo test {args}".strip()
    elif fw == "go":
        cmd = f"go test ./... {args}".strip()
    else:
        return f"[error] unsupported framework: {fw}"

    try:
        r = subprocess.run(
            cmd, cwd=root, shell=True, capture_output=True, text=True,
            timeout=600, env=_enriched_env(),
        )
    except subprocess.TimeoutExpired:
        return f"[error] {fw} timed out after 600s"

    output = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    summary = _summarize_tests(fw, output)
    tail = output[-4000:]
    return f"framework={fw} exit={r.returncode}\nsummary: {summary}\n--output (tail)--\n{tail}"


# ── Persistent memory ──────────────────────────────────────────────────
#
# These tools write to ``<project_root>/.koda/memory/`` so the agent can
# carry non-obvious facts (user preferences, project decisions, gotchas)
# across sessions. The store is anchored by ``CodingAgent.__init__`` via
# ``memory.set_memory_root``.

_MEMORY_TYPE_HELP = (
    "Type must be one of: "
    "user (who the user is, role, expertise), "
    "feedback (corrections or validated approaches — include WHY), "
    "project (current initiatives, deadlines, decisions), "
    "reference (pointers to external systems or files)."
)


@function_tool
def save_memory(name: str, type: str, description: str, content: str) -> str:
    """Persist a durable fact to .koda/memory/ so future sessions inherit it.

    Use this when the user tells you something non-obvious that should
    survive context compaction or a session restart: a preference
    ("we always use ruff with line-length 100"), a correction ("don't
    mock the database"), a project fact ("merge freeze on 2026-03-05"),
    or an external pointer ("oncall dashboard at grafana.internal/...").

    Do NOT save: code patterns derivable from the repo, ephemeral task
    state, or anything already in CLAUDE.md / AGENTS.md.

    Args:
      name: Short, unique title (becomes the filename slug).
      type: One of user / feedback / project / reference. See type help below.
      description: One-line summary shown in the index.
      content: The full memory body (markdown). Lead with the rule/fact,
               then a "Why:" line and a "How to apply:" line for feedback
               or project memories.
    """
    store = _get_memory_store()
    if store is None:
        return "[error] memory store not initialized (no project root anchored)"
    try:
        path = store.save(name, type, description, content)
    except ValueError as e:
        return f"[error] {e}\n{_MEMORY_TYPE_HELP}"
    return f"saved memory {name!r} → {path.relative_to(store.project_root)}"


@function_tool
def update_memory(name: str, content: str) -> str:
    """Replace the body of an existing memory; frontmatter (type, description) is preserved.

    Use to refine a memory whose facts have evolved. To change the
    description or type, delete and re-save.
    """
    store = _get_memory_store()
    if store is None:
        return "[error] memory store not initialized"
    try:
        path = store.update(name, content)
    except (ValueError, FileNotFoundError) as e:
        return f"[error] {e}"
    return f"updated memory {name!r} → {path.relative_to(store.project_root)}"


@function_tool
def delete_memory(name: str) -> str:
    """Remove a memory whose facts are stale or wrong.

    Prefer ``update_memory`` when only the body is outdated. Delete only
    when the memory should no longer influence the agent at all.
    """
    store = _get_memory_store()
    if store is None:
        return "[error] memory store not initialized"
    if store.delete(name):
        return f"deleted memory {name!r}"
    return f"[warn] no memory named {name!r}"


# ── Explore (read-only subagent) ───────────────────────────────────────
#
# Spawns a child agent loop limited to read-only tools and returns just
# its final natural-language summary. Keeps the parent's context small
# when answering "where is X?" / "what calls Y?" / "give me a quick map
# of Z" questions — the child's tool calls and intermediate reads stay
# private. Trade-off: latency (extra LLM round-trips) vs. context.

EXPLORER_PROMPT = """You are an EXPLORER subagent. Your only job is to investigate the user's question and return a concise natural-language summary.

You have READ-ONLY tools: read_file, grep, glob_files, git_status, git_diff, git_log, git_blame. You CANNOT write, edit, run shell commands, or modify memory.

Process:
1. Plan briefly which files/symbols to look at. Don't restate the question.
2. Use grep / glob_files to locate; use read_file (sliced) to confirm. Batch tool calls when independent.
3. Stop as soon as you can answer. Don't read every file in the repo.

Answer format:
- Lead with the answer in 1-2 sentences.
- Cite specific files with `path:line` when applicable.
- If the question can't be answered from the repo, say so explicitly — don't speculate.
- Keep the whole reply under ~300 words. The parent agent only sees this final reply, not your tool calls."""


def _explore_tools() -> list:
    """Return the read-only tool subset the explorer is allowed to use."""
    return [read_file, grep, glob_files, git_status, git_diff, git_log, git_blame]


@function_tool
def explore(query: str, focus_paths: list[str] | None = None) -> str:
    """Spawn a read-only subagent to investigate, returning ONLY its summary.

    Use for "where is X defined?", "what calls Y?", "give me a quick map
    of Z" style questions. The subagent's tool calls and intermediate
    file reads stay out of your context — only the final summary returns.

    Trade-off: an extra LLM round-trip vs. keeping your context small.
    Worth it when you'd otherwise grep + read 5+ files just to answer one
    orientation question.

    Args:
      query: The investigation question, phrased naturally.
      focus_paths: Optional list of paths/globs to direct the explorer at
                   first (e.g. ``["coding_agent/", "tests/test_*.py"]``).
                   The explorer can still read elsewhere if needed.

    Returns:
      The explorer's final summary string. Errors are returned with an
      ``[error]`` prefix so the calling loop can recover.
    """
    # Lazy import dodges the agent <-> tools circular at module load time.
    from agent import get_active_agent

    a = get_active_agent()
    if a is None:
        return "[error] explore unavailable (no active agent registered)"

    extra = ""
    if focus_paths:
        bullets = "\n".join(f"  - {p}" for p in focus_paths)
        extra = f"\n\nFocus on these paths first (but you may read others if needed):\n{bullets}"
    full_query = (
        f"{query.strip()}{extra}\n\n"
        "Reply with a concise summary only — no preamble, no recap of these instructions."
    )
    return a._run_subagent(EXPLORER_PROMPT, full_query, _explore_tools(), max_steps=12)
