"""Extra tools layered on top of the deepagents built-ins.

deepagents already provides: `execute` (shell), `read_file`, `write_file`,
`edit_file`, `ls`, `glob`, `grep`, `write_todos`, `task`. This module only
defines tools that have no deepagents equivalent and are needed for the
KODA coding workflow: `think`, `multi_edit`, web access, git read-only,
and `run_tests`.

All tools here use LangChain's `@tool` decorator so they slot directly
into `create_deep_agent(..., tools=EXTRA_TOOLS)`.
"""

import json
import os
import re
import subprocess
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel
from tavily import TavilyClient

class FindReplace(BaseModel):
    """One find/replace operation for `multi_edit`."""

    old: str
    new: str


def _enriched_env() -> dict[str, str]:
    """Return os.environ with user-local toolchain bins prepended to PATH.

    `subprocess.run(..., shell=True)` invokes /bin/sh (or cmd on Windows),
    which never sources ~/.bashrc, so version-manager-installed tools
    (nvm, pyenv, cargo, pipx) are invisible by default. Prepend their bin
    dirs so the agent can run things it just installed without restarting.
    """
    home = Path.home()
    candidates: list[str] = []
    nvm_versions = home / ".nvm" / "versions" / "node"
    if nvm_versions.is_dir():
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


# ── think ──────────────────────────────────────────────────────────────


@tool
def think(thought: str) -> str:
    """Scratchpad for reasoning. Use before actions or when stuck.

    Nothing is executed — the act of writing forces structured thinking
    and the thought stays in the conversation for later steps to reference.
    Use for: planning an approach, debugging hypotheses, weighing trade-offs.
    """
    return f"noted: {thought[:80]}{'...' if len(thought) > 80 else ''}"


# ── multi_edit ─────────────────────────────────────────────────────────


@tool
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


# ── web tools ──────────────────────────────────────────────────────────


@tool
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


@tool
def web_search(query: str, max_results: int = 10) -> str:
    """Search the web via Tavily and return results as a numbered list.

    Uses Tavily's ``advanced`` search depth with ``include_answer="advanced"``,
    so the response carries both a synthesised answer (rendered first when
    present) and per-source snippets. Requires ``TAVILY_API_KEY`` in the
    environment.

    Args:
        query: The search query string.
        max_results: Maximum number of result rows to return (default 10).

    Returns:
        A formatted string — an ``Answer:`` block when Tavily synthesised one,
        followed by numbered ``title / url / content`` rows. Errors come back
        prefixed with ``[error]``.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "[error] TAVILY_API_KEY is not set in the environment"



    try:
        client = TavilyClient(api_key)
        response = client.search(
            query=query,
            include_answer="advanced",
            search_depth="advanced",
            max_results=max_results,
        )
    except Exception as e:  # noqa: BLE001
        return f"[error] web_search failed: {e}"

    results = response.get("results") or []
    answer = (response.get("answer") or "").strip()
    if not results and not answer:
        return f"No results found for: {query}"

    blocks: list[str] = []
    if answer:
        blocks.append(f"Answer: {answer}")
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        blocks.append(f"{i}. {title}\n   {url}\n   {content}")
    return "\n\n".join(blocks)


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


@tool
def git_status(path: str = ".") -> str:
    """Show working-tree status with branch info (short format)."""
    return _git(["status", "--short", "--branch"], cwd=path)


@tool
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


@tool
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


@tool
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
        if suites:
            parts.append(f"suites: {suites.group(1)}")
        if tests:
            parts.append(f"tests: {tests.group(1)}")
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


@tool
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
            cmd,
            cwd=root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,
            env=_enriched_env(),
        )
    except subprocess.TimeoutExpired:
        return f"[error] {fw} timed out after 600s"

    output = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    summary = _summarize_tests(fw, output)
    tail = output[-4000:]
    return f"framework={fw} exit={r.returncode}\nsummary: {summary}\n--output (tail)--\n{tail}"


# ── Registry ───────────────────────────────────────────────────────────


EXTRA_TOOLS = [
    think,
    multi_edit,
    web_fetch,
    web_search,
    git_status,
    git_diff,
    git_log,
    git_blame,
    run_tests,
]


if __name__ == "__main__":
    # Direct-script entrypoint: load .env so TAVILY_API_KEY etc. are visible.
    # override=True lets the .env value win over any stale value already
    # exported in the current shell (e.g. LANGSMITH_TRACING from a prior run).
    from dotenv import load_dotenv

    load_dotenv(override=True)
    query = "What is the price of the tea in China?"
    print(web_search.invoke(query))