"""Extra tools layered on top of the deepagents built-ins.

deepagents already provides: `execute` (shell), `read_file`, `write_file`,
`edit_file`, `ls`, `glob`, `grep`, `write_todos`, `task`. This module only
defines tools that have no deepagents equivalent and are needed for the
KODA coding workflow: `think`, `multi_edit`, web access, read-only git,
`run_tests`, `run_type_check`, `run_lint`.

The runner tools (``run_tests`` / ``run_type_check`` / ``run_lint``) follow
the same shape on purpose: auto-detect the framework, run with a hard
timeout, return a small structured header + the *tail* of the output so
failures land in the model's context without bloating it.

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
            # Hard upper bound so a slow Tavily response can't silently
            # wedge an agent turn. 20 s is comfortably above the p99 of
            # ``advanced`` queries and well below the model's per-turn
            # patience. Override via ``KODA_WEB_SEARCH_TIMEOUT`` if a
            # specific deployment needs longer.
            timeout=int(os.environ.get("KODA_WEB_SEARCH_TIMEOUT", "20")),
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


_GIT_READ_ONLY_SUBCOMMANDS = frozenset({
    "status", "log", "blame", "show", "branch", "tag",
    "ls-files", "rev-parse", "rev-list", "describe", "remote",
    "shortlog", "reflog", "config",  # config is read-only when invoked with --get*
})


@tool
def git(subcommand: str, extra_args: str = "", path: str = ".") -> str:
    """Run a **read-only** git subcommand.

    Replaces the prior ``git_status`` / ``git_log`` / ``git_blame`` trio with
    one slot. The model gets full git semantics for inspection while a
    whitelist keeps it from mutating state — anything that could change
    the repo (``commit``, ``push``, ``reset``, ``checkout``, ``rebase``,
    ``merge``, ``add``, ``rm``, ``stash`` save, ``tag -d``, ``branch -d``)
    is rejected. For diffs see ``git_diff`` (kept separate because its
    flag shape is distinct).

    Args:
        subcommand: a single git subcommand from the read-only set
            (``status``, ``log``, ``blame``, ``show``, ``branch``,
            ``tag``, ``ls-files``, ``rev-parse``, ``rev-list``,
            ``describe``, ``remote``, ``shortlog``, ``reflog``,
            ``config``).
        extra_args: flags / paths to append, e.g. ``"-10 --oneline"`` for
            log, ``"-L 40,80 path/to/file.py"`` for blame, ``"--short
            --branch"`` for status.
        path: repo root. Defaults to the cwd.

    Examples:
        ``git("status", "--short --branch")``
        ``git("log", "-10 --pretty=format:'%h %an %s'")``
        ``git("blame", "-L 40,80 koda/tui/app.py")``
    """
    sc = (subcommand or "").strip().split()[0] if subcommand and subcommand.strip() else ""
    if sc not in _GIT_READ_ONLY_SUBCOMMANDS:
        return (
            f"[error] subcommand {sc!r} is not in the read-only whitelist. "
            f"Allowed: {sorted(_GIT_READ_ONLY_SUBCOMMANDS)}. "
            "For mutating commands, use the `execute` tool with explicit intent."
        )
    # ``shlex.split`` mirrors how a shell would tokenize the extra flags,
    # so quoted values like ``--pretty=format:'%h %s'`` survive intact.
    import shlex
    parts = [sc, *shlex.split(extra_args)] if extra_args else [sc]
    return _git(parts, cwd=path)


@tool
def git_diff(path: str = ".", staged: bool = False, file: str = "") -> str:
    """Show unified diff of unstaged (default) or staged changes.

    Kept separate from the generic ``git`` tool because its flag shape
    (``--cached``, ``-- <path>``) is the one the model gets wrong most
    often when forced to spell out diffs through a generic interface.

    Args:
        path: repo root.
        staged: True for staged diff, False for unstaged.
        file: optional path to limit the diff to one file.
    """
    args = ["diff"] + (["--cached"] if staged else [])
    if file:
        args += ["--", file]
    return _git(args, cwd=path)


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
def run_tests(framework: str = "auto", extra_args: str = "", path: str = ".") -> str:
    """Run the project's test suite and return a structured summary.

    `framework='auto'` (default) detects pytest / jest / cargo / go / npm-test
    by inspecting the project root. Override to force a specific runner.
    Pass extra runner flags / target patterns via ``extra_args``
    (e.g. ``"-k login"``, ``"--collect-only"``, ``"tests/api"``).
    The result includes: framework, exit code, summary line, and the tail
    of stdout/stderr (last ~4 KB) so the model can read failure details
    without ballooning the context.

    Note: the second parameter is intentionally NOT named ``args`` —
    LangChain's tool wrapper rewrites a kwarg literally named ``args``
    to ``v__args`` when binding the call, which then surfaces as
    ``TypeError: run_tests() got an unexpected keyword argument
    'v__args'`` at invocation time. Keep it as ``extra_args``.
    """
    root = Path(path)
    fw = framework if framework != "auto" else _detect_test_framework(root)
    if not fw:
        return "[error] could not auto-detect a test framework; pass framework= explicitly"

    if fw == "pytest":
        cmd = f"pytest --tb=short -q {extra_args}".strip()
    elif fw == "jest":
        cmd = f"npx --yes jest --silent {extra_args}".strip()
    elif fw == "npm-test":
        cmd = f"npm test --silent {extra_args}".strip()
    elif fw == "cargo":
        cmd = f"cargo test {extra_args}".strip()
    elif fw == "go":
        cmd = f"go test ./... {extra_args}".strip()
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


# ── run_type_check ─────────────────────────────────────────────────────


def _detect_type_checker(root: Path) -> str:
    """Pick a type checker based on what the project ships configuration for.

    Order matters: a project may have both ``pyproject.toml`` (with mypy
    settings) and ``tsconfig.json`` (for a JS sub-tree). Python config
    wins for the root call; the model can pass an explicit ``checker=``
    to target the JS side.
    """
    if (root / "mypy.ini").exists() or (root / ".mypy.ini").exists():
        return "mypy"
    if (root / "pyrightconfig.json").exists():
        return "pyright"
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            txt = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            txt = ""
        if "[tool.mypy" in txt:
            return "mypy"
        if "[tool.pyright" in txt:
            return "pyright"
        # Python project, no explicit config → prefer mypy if any *.py exists.
        if any(root.glob("**/*.py")):
            return "mypy"
    if (root / "tsconfig.json").exists():
        return "tsc"
    return ""


def _summarize_type_check(checker: str, output: str) -> str:
    if checker == "mypy":
        m = re.search(r"^(Found \d+ error.+)$", output, flags=re.M)
        if m:
            return m.group(1)
        if "Success: no issues" in output:
            return "Success: no issues found"
        return "(no mypy summary line)"
    if checker == "pyright":
        m = re.search(r"^(\d+ errors?,\s*\d+ warnings?,.*)$", output, flags=re.M)
        return m.group(1) if m else "(no pyright summary line)"
    if checker == "tsc":
        errors = re.findall(r"error TS\d+", output)
        return f"{len(errors)} TypeScript error(s)" if errors else "no TypeScript errors"
    return "(parser not implemented)"


@tool
def run_type_check(checker: str = "auto", extra_args: str = "", path: str = ".") -> str:
    """Run a static type checker and return a structured summary.

    ``checker='auto'`` (default) detects mypy / pyright / tsc by inspecting
    the project root. Static analysis catches a large fraction of bugs
    before tests even run — call this first on a bug-fix turn.

    Args:
        checker: ``auto``, ``mypy``, ``pyright``, or ``tsc``.
        extra_args: flags / target paths to append (e.g. ``"--strict
            koda/tui/"``, ``"--project tsconfig.build.json"``).
        path: project root. Defaults to the cwd.

    Returns:
        ``checker=<name> exit=<code>`` followed by a one-line summary and
        the tail (~4 KB) of stdout/stderr. The tail keeps failure detail
        in context without ballooning prompt size.
    """
    root = Path(path)
    ck = checker if checker != "auto" else _detect_type_checker(root)
    if not ck:
        return "[error] could not auto-detect a type checker; pass checker= explicitly"

    if ck == "mypy":
        cmd = f"mypy {extra_args or '.'}".strip()
    elif ck == "pyright":
        cmd = f"pyright {extra_args}".strip()
    elif ck == "tsc":
        # ``--noEmit`` keeps tsc as a checker only — without it the call
        # would also write .js files into the tree, which is surprising.
        cmd = f"npx --yes tsc --noEmit {extra_args}".strip()
    else:
        return f"[error] unsupported checker: {ck}"

    try:
        r = subprocess.run(
            cmd,
            cwd=root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            env=_enriched_env(),
        )
    except subprocess.TimeoutExpired:
        return f"[error] {ck} timed out after 300s"

    output = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    summary = _summarize_type_check(ck, output)
    tail = output[-4000:]
    return f"checker={ck} exit={r.returncode}\nsummary: {summary}\n--output (tail)--\n{tail}"


# ── run_lint ───────────────────────────────────────────────────────────


def _detect_linter(root: Path) -> str:
    """Pick a linter by config-file presence, falling back to language."""
    if (root / "ruff.toml").exists() or (root / ".ruff.toml").exists():
        return "ruff"
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            txt = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            txt = ""
        if "[tool.ruff" in txt:
            return "ruff"
    # ESLint configs come in many flavors — flat (eslint.config.*) and
    # legacy (.eslintrc.*). Cover both.
    for cand in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
                 ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs",
                 ".eslintrc.yml", ".eslintrc.yaml"):
        if (root / cand).exists():
            return "eslint"
    # Fall back by language: prefer ruff for Python, eslint for JS/TS.
    if any(root.glob("*.py")) or pyproject.exists():
        return "ruff"
    if (root / "package.json").exists():
        return "eslint"
    return ""


def _summarize_lint(linter: str, output: str, exit_code: int) -> str:
    if linter == "ruff":
        m = re.search(r"^Found (\d+) error", output, flags=re.M)
        if m:
            return f"{m.group(1)} ruff finding(s)"
        if exit_code == 0:
            return "no ruff findings"
        return "(no ruff summary)"
    if linter == "eslint":
        m = re.search(r"(\d+ problems?\s*\(\d+ errors?,\s*\d+ warnings?\))", output)
        if m:
            return m.group(1)
        if exit_code == 0:
            return "no eslint findings"
        return "(no eslint summary)"
    return "(parser not implemented)"


@tool
def run_lint(linter: str = "auto", extra_args: str = "", path: str = ".") -> str:
    """Run a linter and return a structured summary.

    ``linter='auto'`` (default) detects ruff / eslint by config-file
    presence, then by language. Run this for cheap, fast feedback —
    linters surface dead code, unused imports, wrong arity, and other
    obvious-but-easy-to-miss issues without the cost of executing tests.

    Args:
        linter: ``auto``, ``ruff``, or ``eslint``.
        extra_args: flags / paths to append (e.g. ``"--fix"``,
            ``"--select E,F koda/"``, ``"--max-warnings 0 src/"``).
        path: project root. Defaults to the cwd.

    Returns:
        ``linter=<name> exit=<code>`` followed by a summary and the tail
        (~4 KB) of stdout/stderr.
    """
    root = Path(path)
    ln = linter if linter != "auto" else _detect_linter(root)
    if not ln:
        return "[error] could not auto-detect a linter; pass linter= explicitly"

    if ln == "ruff":
        cmd = f"ruff check {extra_args or '.'}".strip()
    elif ln == "eslint":
        cmd = f"npx --yes eslint {extra_args or '.'}".strip()
    else:
        return f"[error] unsupported linter: {ln}"

    try:
        r = subprocess.run(
            cmd,
            cwd=root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=180,
            env=_enriched_env(),
        )
    except subprocess.TimeoutExpired:
        return f"[error] {ln} timed out after 180s"

    output = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    summary = _summarize_lint(ln, output, r.returncode)
    tail = output[-4000:]
    return f"linter={ln} exit={r.returncode}\nsummary: {summary}\n--output (tail)--\n{tail}"


# ── Registry ───────────────────────────────────────────────────────────


@tool
def ask_user(question: str, options: list[str] | None = None) -> str:
    """Ask the user a clarifying question and wait for their answer.

    Encouraged in **PLAN mode** before drafting — when the user's
    request is ambiguous, the agent should ask one focused question
    instead of guessing at a requirement. Also useful at decision
    points anywhere (which approach, which library, where to put X)
    when an arbitrary pick would be costly to revisit.

    Args:
        question: A focused, single-sentence question. Don't stack
            multiple questions — call ``ask_user`` again if you need
            another answer.
        options: Optional list of 2-9 short choices. The user can
            navigate with arrow keys or jump with number keys 1-9.
            If omitted, the user acknowledges with Enter or cancels
            with Esc; phrase the question so an "okay/cancel"
            response is meaningful.

    Returns:
        The user's chosen option (verbatim text) when they pick one,
        ``"(acknowledged)"`` when they hit Enter on an options-less
        prompt, or an empty string when they cancel with Esc.

    Examples:
        ``ask_user("Should this use SQLite or Postgres for storage?", ["SQLite", "Postgres"])``
        ``ask_user("This change touches public API. Add a deprecation period?", ["Yes, deprecate first", "No, break now"])``
    """
    from koda.tools import ask_user as _ask

    return _ask.ask(question, options or [])


EXTRA_TOOLS = [
    think,
    multi_edit,
    web_fetch,
    web_search,
    git,
    git_diff,
    run_tests,
    run_type_check,
    run_lint,
    ask_user,
]


if __name__ == "__main__":
    # Direct-script entrypoint: load .env so TAVILY_API_KEY etc. are visible.
    # override=True lets the .env value win over any stale value already
    # exported in the current shell (e.g. LANGSMITH_TRACING from a prior run).
    from dotenv import load_dotenv

    load_dotenv(override=True)
    query = "What is the price of the tea in China?"
    print(web_search.invoke(query))