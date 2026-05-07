import asyncio
import json
import logging
import os
import platform
import re
import sys
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Langfuse v4 reads `LANGFUSE_HOST`; the project's .env uses `LANGFUSE_BASE_URL`.
# Map it before importing langfuse so the SDK picks up our self-hosted/cloud URL.
if not os.getenv("LANGFUSE_HOST") and os.getenv("LANGFUSE_BASE_URL"):
    os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]

from langfuse import get_client, observe, propagate_attributes
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from agents.tool_context import ToolContext

# LangChain primitives — Phase 3 wiring. ``build_chat_model`` returns a
# provider-agnostic ``BaseChatModel`` from a ``"provider:model"`` spec
# (see coding_agent/clients.py). ``convert_to_messages`` turns the
# OpenAI-shape dict history we already keep into LangChain ``BaseMessage``s
# right before each ``astream`` call, so we don't have to change any of the
# message-building / tool-result-appending code below.
from clients import build_chat_model
from langchain_core.messages import AIMessageChunk, convert_to_messages

_log = logging.getLogger("coding_agent")

from system_prompt import AGENTS_INIT_PROMPT, SYSTEM_PROMPT
import subprocess

from tools import (
    run_shell,
    read_file,
    write_file,
    edit_file,
    grep,
    todo_write,
    todo_update,
    think,
    multi_edit,
    glob_files,
    web_fetch,
    git_status,
    git_diff,
    git_log,
    git_blame,
    run_tests,
    set_approval_mode,
    save_memory,
    update_memory,
    delete_memory,
    explore,
)
from memory import set_memory_root, get_memory_store, INDEX_FILENAME, MEMORY_DIRNAME
from project_state import (
    BootstrapAction,
    decide_bootstrap_action,
    save_snapshot,
)


_TOOLS = [
    run_shell, read_file, write_file, edit_file, grep,
    todo_write, todo_update, think,
    multi_edit, glob_files, web_fetch,
    git_status, git_diff, git_log, git_blame,
    run_tests,
    save_memory, update_memory, delete_memory,
    explore,
]

AGENTS_MD_NAME = "AGENTS.md"

# Default coding model. Connection settings (base URL, API key) come from .env.
MINIMAX_MODEL_NAME = "MiniMax-M2.7-UD-Q8_K_XL"

# ── Context compaction tunables (env-overridable) ───────────────────────
#
# Compaction folds older history into a single summary system message when
# the running message list crosses ``COMPACT_THRESHOLD_CHARS``. We keep the
# last ``COMPACT_KEEP_RECENT_TURNS`` user turns intact (a "turn" being a
# user message and everything that follows it until the next user message,
# i.e. assistant + any tool replies). Cuts only happen on user-message
# boundaries so we never split an assistant ``tool_calls`` message from
# its matching ``tool`` replies.
#
# Threshold ~50k chars ≈ 12k tokens — fires well before typical 16k–32k
# context limits but not so eagerly it kicks in for normal multi-step
# tasks. Set ``KODA_DISABLE_COMPACT=1`` to skip entirely.
_COMPACT_THRESHOLD_CHARS = int(os.getenv("KODA_COMPACT_THRESHOLD_CHARS", "50000"))
# An "assistant block" = one think→act exchange (one assistant message and
# any tool replies that follow). Single-turn tasks only have ONE user
# message but can have many assistant blocks, so cutting on user-msg
# boundaries leaves nothing to compact. Default keeps the last 2 blocks
# verbatim and folds everything before.
_COMPACT_KEEP_RECENT_BLOCKS = int(
    os.getenv(
        "KODA_COMPACT_KEEP_RECENT_BLOCKS",
        os.getenv("KODA_COMPACT_KEEP_RECENT_TURNS", "2"),
    )
)
_COMPACT_DISABLED = os.getenv("KODA_DISABLE_COMPACT", "0") == "1"
_SUMMARY_MAX_CHARS = int(os.getenv("KODA_COMPACT_SUMMARY_CHARS", "2000"))

# ── Subagent / explore plumbing ─────────────────────────────────────────
#
# Tools are registered as bare module-level functions, so they can't see
# ``self`` on the running CodingAgent. We expose the active agent as a
# module-level singleton (set in ``__init__``) so the ``explore`` tool can
# call back into it. Same pattern as ``memory.set_memory_root``.
#
# Depth counter prevents an explorer from spawning explorers. The agent
# loop is synchronous, so a plain int is enough — no locking needed.
_active_agent: "CodingAgent | None" = None
_subagent_depth: int = 0
_MAX_SUBAGENT_DEPTH = 1


def _set_active_agent(a: "CodingAgent") -> None:
    global _active_agent
    _active_agent = a


def get_active_agent() -> "CodingAgent | None":
    return _active_agent


def _message_chars(msg: dict) -> int:
    """Best-effort char count for a message dict.

    Includes ``content`` plus any tool-call ``arguments`` strings since
    those can be large (a single ``write_file`` arguments blob may be
    several kilobytes of source code).
    """
    n = len(msg.get("content") or "")
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        n += len(fn.get("arguments") or "")
    return n


def _find_safe_compact_cut(
    messages: list[dict], keep_recent_blocks: int
) -> tuple[int, int] | None:
    """Identify the slice ``messages[start:cut]`` that compaction may fold.

    A safe cut respects two invariants:

    1. ``messages[0]`` (system prompt) and ``messages[1]`` (the original
       user prompt) are always preserved verbatim — they're the agent's
       mission. Compaction starts at index 2.
    2. The cut must land on an *assistant-block boundary*: the first
       message of a step (an ``assistant`` message immediately after a
       non-assistant message). Cutting elsewhere would strand a ``tool``
       message away from its parent assistant, breaking the next
       provider call.

    Returns ``(start, cut)`` such that ``messages[start:cut]`` is the
    slice to compact and ``messages[cut:]`` is the recent tail to keep.
    Returns ``None`` when there aren't enough blocks to compact safely.
    """
    if len(messages) < 3:
        return None
    block_starts: list[int] = []
    for i in range(1, len(messages)):
        m = messages[i]
        if m.get("role") != "assistant":
            continue
        prev_role = messages[i - 1].get("role")
        if prev_role != "assistant":  # block start = first assistant after user/tool
            block_starts.append(i)
    if len(block_starts) <= keep_recent_blocks:
        return None
    cut = block_starts[-keep_recent_blocks]
    # Preserve system prompt + initial user prompt.
    start = 2 if messages[1].get("role") == "user" else 1
    if start >= cut:
        return None
    return (start, cut)


def _render_for_summary(messages: list[dict]) -> str:
    """Flatten a slice of message dicts into one prompt-ready string."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content") or ""
        line = f"[{role}] {content}".rstrip()
        # Include tool-call info inline so the summarizer sees what tools ran.
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            line += f"\n  ↳ tool_call {fn.get('name', '?')}({fn.get('arguments', '') or ''})"
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id", "?")
            line = f"[tool result {tcid}] {content}".rstrip()
        parts.append(line)
    return "\n\n".join(parts)


# ── AGENTS.md cascade ───────────────────────────────────────────────────
#
# Mirrors the Codex-style hierarchical AGENTS.md walk: from cwd, walk up to
# the git work-tree root collecting AGENTS.md at each level, plus a
# user-level ~/.koda/AGENTS.md. Closer files are more specific and appended
# *last* so the LLM treats them as the most recent (winning) instructions.
USER_AGENTS_MD = Path.home() / ".koda" / AGENTS_MD_NAME
_MAX_CASCADE_LEVELS = 8


def _git_toplevel(start: Path) -> Path | None:
    """Return the git work-tree root containing ``start``, or None."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start, capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, NotADirectoryError):
        return None
    if r.returncode != 0:
        return None
    top = r.stdout.strip()
    return Path(top).resolve() if top else None


def _agents_md_chain(project_root: Path) -> list[Path]:
    """Return AGENTS.md paths to consider, ordered general → specific.

    User-level first (most general fallback), then outermost git ancestor
    down to ``project_root`` last. Used by both the loader and the cache
    so both consider the same set of files.
    """
    chain: list[Path] = [USER_AGENTS_MD]
    stop_at = _git_toplevel(project_root)
    walk: list[Path] = []
    cur = project_root.resolve()
    for _ in range(_MAX_CASCADE_LEVELS):
        walk.append(cur / AGENTS_MD_NAME)
        if stop_at is not None and cur == stop_at:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    chain.extend(reversed(walk))
    return chain


def _collect_agents_md_files(project_root: Path) -> list[tuple[Path, str]]:
    """Read each AGENTS.md in the cascade. Returns ``[(path, content), …]``."""
    out: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for path in _agents_md_chain(project_root):
        try:
            rp = path.resolve()
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        if not rp.exists():
            continue
        try:
            content = rp.read_text().strip()
        except OSError:
            continue
        if content:
            out.append((rp, content))
    return out


def _read_agents_md(project_root: Path) -> str:
    """Return the project-root AGENTS.md (stripped) or '' if absent/empty.

    Kept as a thin shim for callers that only care about the file the
    agent itself would *write* to (e.g. bootstrap target).
    """
    path = project_root / AGENTS_MD_NAME
    if not path.exists():
        return ""
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _compose_env_block(project_root: Path, *, model_name: str | None = None) -> str:
    """Return the ``<env>`` block prepended to the system prompt.

    Gives the model a stable header it can rely on for "where am I, when
    is it, what model am I" instead of having to ask via tools. Cheap —
    one short ``git rev-parse`` if we're in a repo, otherwise no I/O.
    """
    is_git_repo = (project_root / ".git").exists() or _git_toplevel(project_root) is not None
    branch = ""
    if is_git_repo:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=project_root, capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0:
                branch = r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, NotADirectoryError):
            pass

    parts = [
        "<env>",
        f"cwd: {project_root}",
        f"platform: {sys.platform}",
        f"os: {platform.platform(terse=True)}",
        f"python: {platform.python_version()}",
        f"shell: {os.getenv('SHELL', 'unknown')}",
        f"date: {date.today().isoformat()}",
    ]
    if model_name:
        parts.append(f"model: {model_name}")
    parts.append(f"is_git_repo: {str(is_git_repo).lower()}")
    if branch:
        parts.append(f"git_branch: {branch}")
    parts.append("</env>")
    return "\n".join(parts)


def _compose_git_context(project_root: Path) -> str:
    """Return a one-shot snapshot of git state to seed the system prompt.

    Empty string if not a git repo or git is unavailable. Captures branch,
    short status, and the last 5 commits — enough for the agent to know
    what's in flight without reaching for git tools on every session.
    """
    if not (project_root / ".git").exists():
        return ""
    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                ["git", *args], cwd=project_root,
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    status = _run(["status", "--short"])
    log = _run(["log", "-5", "--pretty=format:%h %s"])
    if not branch and not status and not log:
        return ""
    parts = []
    if branch: parts.append(f"Branch: {branch}")
    if status: parts.append(f"Status:\n{status}")
    else: parts.append("Status: clean")
    if log: parts.append(f"Recent commits:\n{log}")
    return "# Git context (snapshot at session start)\n\n" + "\n\n".join(parts) + "\n"


def _compose_instructions(
    base: str,
    project_root: Path,
    *,
    model_name: str | None = None,
) -> str:
    """Wrap the base system prompt with env block + AGENTS.md cascade + git.

    Layout of the composed prompt::

        <env>...</env>          ← always present, gives orientation
        <base system prompt>
        # Project context (AGENTS.md cascade — closer files override)
          ### AGENTS.md (~/.koda/AGENTS.md)        ← user-level
          ### AGENTS.md (../AGENTS.md)             ← parent
          ### AGENTS.md (./AGENTS.md)              ← project root (winning)
        # Git context (snapshot at session start)

    Closer files appear *last* in the cascade so the LLM treats them as
    the most recent (and authoritative) instructions.
    """
    env = _compose_env_block(project_root, model_name=model_name)
    out = f"{env}\n\n{base}" if env else base

    md_files = _collect_agents_md_files(project_root)
    if md_files:
        sections: list[str] = []
        for path, content in md_files:
            try:
                label = str(path.relative_to(project_root))
                if not label.startswith("."):
                    label = f"./{label}"
            except ValueError:
                try:
                    label = f"~/{path.relative_to(Path.home())}"
                except ValueError:
                    label = str(path)
            sections.append(f"### AGENTS.md ({label})\n\n{content}")
        out = (
            f"{out}\n\n# Project context (AGENTS.md cascade — closer files override)"
            f"\n\n" + "\n\n---\n\n".join(sections) + "\n"
        )

    mem = _read_memory_index(project_root)
    if mem:
        out = (
            f"{out}\n\n# Persistent memory (.koda/memory/MEMORY.md — read individual "
            f"entries with read_file when relevant)\n\n{mem}\n"
        )

    git_ctx = _compose_git_context(project_root)
    if git_ctx:
        out = f"{out}\n\n{git_ctx}"
    return out


def _memory_index_path(project_root: Path) -> Path:
    return project_root / MEMORY_DIRNAME / INDEX_FILENAME


def _read_memory_index(project_root: Path) -> str:
    """Return ``.koda/memory/MEMORY.md`` contents (stripped) or '' if absent."""
    path = _memory_index_path(project_root)
    try:
        return path.read_text().strip()
    except OSError:
        return ""


# ── Composed-prompt cache ───────────────────────────────────────────────
#
# The composed prompt costs several git subprocess calls + a few file reads
# every turn — significant TTFT on long sessions and the dominant local
# overhead before the first token goes out. We memoize per
# (base, project_root, model_name) with a 30s TTL plus a per-file mtime
# tuple covering every AGENTS.md in the cascade, so any edit anywhere in
# the cascade invalidates within the TTL.
_COMPOSED_TTL = float(os.getenv("KODA_CODING_AGENT_PROMPT_TTL", "30"))
_COMPOSED_CACHE: dict[
    tuple[int, str, str | None],
    tuple[float, tuple[tuple[str, float], ...], str],
] = {}


def _agents_md_mtimes(project_root: Path) -> tuple[tuple[str, float], ...]:
    """Snapshot mtimes of every prompt-affecting file (AGENTS.md cascade + memory index).

    Missing files are recorded as ``0.0`` so creating one later still
    invalidates the cache.
    """
    out: list[tuple[str, float]] = []
    paths = list(_agents_md_chain(project_root))
    paths.append(_memory_index_path(project_root))
    for path in paths:
        try:
            mt = path.stat().st_mtime
        except OSError:
            mt = 0.0
        out.append((str(path), mt))
    return tuple(out)


def _compose_instructions_cached(
    base: str,
    project_root: Path,
    *,
    model_name: str | None = None,
) -> str:
    """Memoized variant of :func:`_compose_instructions`.

    Cache key is ``(id(base), str(project_root), model_name)`` so distinct
    base prompts / projects / models don't collide. Invalidated when:
      * the entry is older than ``_COMPOSED_TTL`` seconds, OR
      * any AGENTS.md mtime in the cascade changed since the entry was built.
    """
    key = (id(base), str(project_root), model_name)
    now = time.time()
    mtimes = _agents_md_mtimes(project_root)

    cached = _COMPOSED_CACHE.get(key)
    if cached is not None:
        ts, cached_mtimes, value = cached
        if now - ts < _COMPOSED_TTL and cached_mtimes == mtimes:
            return value

    value = _compose_instructions(base, project_root, model_name=model_name)
    _COMPOSED_CACHE[key] = (now, mtimes, value)
    return value


class CodingAgent:
    def __init__(
        self,
        model: str,
        tools: list | None = None,
        system_prompt: str = "",
        summarizer=None,
        summarize_threshold: int = 4_000,
        project_root: str | os.PathLike | None = None,
        auto_create_agents_md: bool = True,
        temperature: float = 0.7,
        # Optional overrides for self-hosted / OpenAI-compatible endpoints.
        # When ``None``, ``build_chat_model`` reads the right env var per
        # provider (e.g. ``OPENAI_API_KEY``, ``OLLAMA_BASE_URL``).
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        """Main Coding Agent class.

        Owns a LangChain ``BaseChatModel`` (provider chosen by the
        ``provider:model[:tag]`` ``model`` spec — see
        :func:`clients.build_chat_model`), the bound tool list, the system
        prompt, an optional summarizer, and a project-scoped AGENTS.md that
        gets loaded into the system prompt for project-specific context.

        If ``auto_create_agents_md=True`` and the project's AGENTS.md is
        missing, the agent bootstraps the file by running itself once with
        AGENTS_INIT_PROMPT, then re-loads it into the system prompt.
        """
        self.model = model
        self.tools = list(tools or [])

        # Build the chat model from the provider spec. Tools are bound on
        # the model (not passed per-call) so providers that need
        # provider-specific schema massaging (Anthropic, Gemini) get one
        # consistent surface.
        chat_kwargs: dict[str, Any] = {"temperature": temperature}
        if base_url is not None:
            chat_kwargs["base_url"] = base_url
        if api_key is not None:
            chat_kwargs["api_key"] = api_key
        base_model = build_chat_model(model, **chat_kwargs)
        # Keep the un-bound model around for compaction summaries — we
        # don't want the summarizer call to itself try to emit tool calls.
        self._summarizer_model = base_model
        self._chat_model = (
            base_model.bind_tools(self._tool_schemas()) if self.tools else base_model
        )

        # Keep raw + composed prompts separate so the async path can recompose
        # AGENTS.md + git context fresh on every turn (state changes between turns).
        self._raw_system_prompt = system_prompt or SYSTEM_PROMPT
        self.system_prompt = self._raw_system_prompt
        self.summarizer = summarizer
        self.summarize_threshold = summarize_threshold
        self.temperature = temperature
        self.last_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._lf = get_client()
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.agents_md_path = self.project_root / AGENTS_MD_NAME
        # Anchor the persistent-memory store before composing the prompt;
        # the composer reads .koda/memory/MEMORY.md if it exists.
        set_memory_root(self.project_root)
        # Register this agent so the ``explore`` tool can call back into
        # it. The TUI runs one CodingAgent at a time, so last-set wins.
        _set_active_agent(self)
        self._load_or_create_agents_md(auto_create_agents_md)

    def _load_or_create_agents_md(self, auto_create: bool) -> None:
        """Route between SKIP / DELTA / FULL bootstrap based on signal state.

        - FULL: nothing in the AGENTS.md cascade (project, parents, user)
          has any content → run the original full-explore bootstrap.
        - DELTA: AGENTS.md exists but tracked signal files (manifests,
          top-level dirs) have changed since the last snapshot → run a
          *focused* update that only re-reads what changed.
        - SKIP: AGENTS.md exists and signals are unchanged → use as-is.

        After a successful FULL or DELTA bootstrap the new project-state
        snapshot is persisted to ``.koda/state.json`` so the next session
        has a fresh baseline to diff against.
        """
        cascade = _collect_agents_md_files(self.project_root)
        agents_md_present = bool(cascade)

        if auto_create:
            action, changed, snap = decide_bootstrap_action(
                self.project_root, agents_md_present=agents_md_present,
            )
            if action is BootstrapAction.FULL:
                _log.info(
                    "No AGENTS.md found in cascade — full bootstrap at %s",
                    self.agents_md_path,
                )
                self._bootstrap_agents_md()
                # Re-snapshot after the file was written.
                from project_state import collect_snapshot
                save_snapshot(self.project_root, collect_snapshot(self.project_root))
            elif action is BootstrapAction.DELTA:
                _log.info(
                    "AGENTS.md may be stale — delta update; %d signal(s) changed: %s",
                    len(changed), ", ".join(changed[:5]),
                )
                self._delta_bootstrap_agents_md(changed)
                from project_state import collect_snapshot
                save_snapshot(self.project_root, collect_snapshot(self.project_root))
            else:  # SKIP
                # Still persist the snapshot if we don't have one yet, so the
                # *next* run can detect changes against this baseline.
                save_snapshot(self.project_root, snap)

        self.system_prompt = _compose_instructions(
            self._raw_system_prompt or SYSTEM_PROMPT,
            self.project_root,
            model_name=self.model,
        )

    def _bootstrap_agents_md(self) -> None:
        """Run a one-shot agent loop with AGENTS_INIT_PROMPT to create AGENTS.md."""
        saved_prompt = self.system_prompt
        try:
            self.system_prompt = AGENTS_INIT_PROMPT
            query = (
                f"Explore the project rooted at `{self.project_root}` and write a "
                f"concise AGENTS.md describing it. Save the file using `write_file` "
                f"to `{self.agents_md_path}`. Reply with the single word `done` "
                f"after the file is saved."
            )
            self.run(query, max_steps=20, verbose=True)
        finally:
            self.system_prompt = saved_prompt

    def _delta_bootstrap_agents_md(self, changed_signals: list[str]) -> None:
        """Update an existing AGENTS.md based on what changed.

        Cheaper than a full bootstrap because the model is told *exactly*
        which files moved — it can read just those plus the current
        AGENTS.md, then write back a focused revision instead of
        re-exploring the entire repo. Caps at 12 steps (full bootstrap is
        20).
        """
        if not self.agents_md_path.exists():
            # Cascade had a parent AGENTS.md but project root doesn't —
            # treat as full to actually create the project file.
            self._bootstrap_agents_md()
            return
        saved_prompt = self.system_prompt
        try:
            self.system_prompt = AGENTS_INIT_PROMPT
            change_list = "\n".join(f"  - {c}" for c in changed_signals)
            query = (
                f"AGENTS.md at `{self.agents_md_path}` already exists. The "
                f"following project signals changed since it was last "
                f"updated:\n{change_list}\n\n"
                f"Read the current AGENTS.md and the changed files (focus on "
                f"these — do NOT re-explore the whole repo). Produce an "
                f"UPDATED AGENTS.md that reflects the new state: bumped "
                f"dependency versions, new commands, new top-level packages, "
                f"removed sections, etc. Preserve sections unrelated to the "
                f"changed signals. Save with `write_file` to "
                f"`{self.agents_md_path}`. Reply `done` when saved."
            )
            self.run(query, max_steps=12, verbose=True)
        finally:
            self.system_prompt = saved_prompt

    def _run_subagent(
        self,
        prompt: str,
        query: str,
        tools: list,
        max_steps: int = 12,
        verbose: bool = False,
    ) -> str:
        """Run a focused sub-loop with a different prompt + tool subset.

        Saves and restores ``system_prompt`` / ``tools`` / ``_chat_model``
        around the call. Tools are rebound on the unbound ``base_model``
        (``self._summarizer_model`` is the same reference) so the parent
        keeps its own bound model intact.

        Returns the sub-loop's final assistant content. Errors bubble up
        as a string starting with ``[error]`` so the parent's tool-result
        handling stays uniform.
        """
        global _subagent_depth
        if _subagent_depth >= _MAX_SUBAGENT_DEPTH:
            return "[error] subagent max depth reached (no nested explore)"
        saved_prompt = self.system_prompt
        saved_tools = self.tools
        saved_chat_model = self._chat_model
        _subagent_depth += 1
        try:
            self.system_prompt = prompt
            self.tools = list(tools)
            self._chat_model = (
                self._summarizer_model.bind_tools(self._tool_schemas())
                if self.tools else self._summarizer_model
            )
            return self.run(query, max_steps=max_steps, verbose=verbose)
        except Exception as e:  # noqa: BLE001 — never let a subagent crash the parent
            _log.warning("subagent failed (%s: %s)", type(e).__name__, e)
            return f"[error] subagent failed: {type(e).__name__}: {e}"
        finally:
            self.system_prompt = saved_prompt
            self.tools = saved_tools
            self._chat_model = saved_chat_model
            _subagent_depth -= 1

    _RETRYABLE_LLM_ERRORS = (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        InternalServerError,
    )

    def _extract_durable_facts(self, slice_to_compact: list[dict]) -> list[dict]:
        """Ask the model for durable facts in the slice worth saving as memory.

        Runs *before* the lossy summarisation step in :meth:`_compact_if_needed`,
        so anything worth remembering migrates from the conversation into
        ``.koda/memory/`` before the originals are dropped. Returns the
        list of facts actually persisted (for logging / telemetry); empty
        list on any failure — compaction proceeds either way.
        """
        store = get_memory_store()
        if store is None:
            return []
        rendered = _render_for_summary(slice_to_compact)
        prompt = (
            "Extract DURABLE facts from the following coding-agent transcript "
            "that should survive into future sessions as persistent memory.\n\n"
            "Save ONLY:\n"
            "  - user: who the user is, role, expertise (when newly revealed)\n"
            "  - feedback: explicit corrections OR validated approaches the user "
            "confirmed (include WHY in the content)\n"
            "  - project: ongoing initiatives, deadlines, decisions, incidents "
            "(use ABSOLUTE dates, not 'tomorrow')\n"
            "  - reference: pointers to external systems (Linear projects, "
            "dashboards, channels)\n\n"
            "Do NOT save: code patterns, file paths, debugging recipes, "
            "ephemeral task state, or anything obvious from reading the repo.\n\n"
            "Return a JSON array (and NOTHING else) of objects with keys: "
            "name, type, description, content. Empty array [] if nothing "
            "qualifies. Limit to 5 facts max.\n\n"
            f"--- TRANSCRIPT ---\n{rendered}"
        )
        from langchain_core.messages import HumanMessage

        try:
            resp = self._summarizer_model.invoke([HumanMessage(content=prompt)])
        except Exception as e:  # noqa: BLE001 — extraction must never crash a turn
            _log.warning("memory extraction failed (%s: %s); skipping", type(e).__name__, e)
            return []
        raw = (getattr(resp, "content", None) or "").strip()
        # Strip code fences if the model wrapped JSON in ``` blocks.
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", raw, flags=re.DOTALL).strip()
        try:
            facts = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _log.info("memory extraction returned non-JSON; skipping (head=%r)", raw[:160])
            return []
        if not isinstance(facts, list):
            return []
        saved: list[dict] = []
        for f in facts[:5]:
            if not isinstance(f, dict):
                continue
            try:
                store.save(
                    name=str(f.get("name", "")).strip(),
                    type_=str(f.get("type", "project")).strip(),
                    description=str(f.get("description", "")).strip(),
                    content=str(f.get("content", "")).strip(),
                )
                saved.append(f)
            except (ValueError, OSError) as e:
                _log.info("memory save skipped (%s: %s)", type(e).__name__, e)
        if saved:
            _log.info(
                "extracted %d durable fact(s) to .koda/memory/ pre-compaction",
                len(saved),
            )
        return saved

    def _compact_if_needed(self, messages: list[dict]) -> bool:
        """Fold older history into a single summary system message in place.

        Triggers when total content+args char count crosses
        ``KODA_COMPACT_THRESHOLD_CHARS`` (default 50k). Cuts only on user-
        message boundaries so the assistant ``tool_calls`` ↔ ``tool``
        pairing the next ``astream`` call requires stays intact. Preserves
        ``messages[0]`` (the system prompt) verbatim.

        Before the lossy summary, runs :meth:`_extract_durable_facts` so
        memory-worthy facts in the slice are persisted to
        ``.koda/memory/`` before being dropped. Returns ``True`` iff a
        compaction actually fired.
        """
        if _COMPACT_DISABLED:
            return False
        total = sum(_message_chars(m) for m in messages)
        if total < _COMPACT_THRESHOLD_CHARS:
            return False
        cut_info = _find_safe_compact_cut(messages, _COMPACT_KEEP_RECENT_BLOCKS)
        if cut_info is None:
            return False
        start, cut = cut_info

        slice_to_compact = messages[start:cut]
        # Persist durable facts first so they survive the upcoming drop.
        saved_facts = self._extract_durable_facts(slice_to_compact)

        rendered = _render_for_summary(slice_to_compact)
        prompt = (
            f"You are a memory compactor. Summarise the following coding-agent "
            f"transcript in at most {_SUMMARY_MAX_CHARS} characters. Preserve: "
            f"file paths read or written, errors encountered, tool results that "
            f"informed later decisions, key architectural decisions, and any "
            f"outstanding TODOs. Drop chat fluff and verbose tool output. Write "
            f"in compact bullet form.\n\n"
            f"--- TRANSCRIPT ---\n{rendered}"
        )
        from langchain_core.messages import HumanMessage

        try:
            resp = self._summarizer_model.invoke([HumanMessage(content=prompt)])
            summary = (getattr(resp, "content", None) or "")[: _SUMMARY_MAX_CHARS]
        except Exception as e:  # noqa: BLE001 — compaction must never crash a turn
            _log.warning("compaction summariser failed (%s: %s); skipping", type(e).__name__, e)
            return False

        if not summary.strip():
            return False
        memory_note = (
            f" + {len(saved_facts)} fact(s) saved to .koda/memory/"
            if saved_facts else ""
        )
        summary_msg = {
            "role": "system",
            "content": (
                f"[compacted {len(slice_to_compact)} prior message(s); "
                f"~{total} chars folded{memory_note}]\n\n{summary}"
            ),
        }
        messages[start:cut] = [summary_msg]
        _log.info(
            "compacted %d msgs (~%d chars) into 1 summary msg (+%d memory)",
            len(slice_to_compact), total, len(saved_facts),
        )
        return True

    async def _astream_with_retry(self, lc_messages):
        """Yield chunks from ``self._chat_model.astream()`` with retry.

        Retries on transient provider failures (connection drops, timeouts,
        rate limits, 500s) up to 3 attempts with exponential backoff, but
        **only when no chunks have been yielded yet from this attempt**.
        Once we've started streaming, a mid-stream failure is fatal — we
        can't re-do the call without risking double-emit of text deltas.
        Same semantic the old sync ``_stream_with_retry`` had, just lifted
        to the async/LangChain path.
        """
        last_err: Exception | None = None
        for attempt in range(3):
            yielded_any = False
            try:
                async for chunk in self._chat_model.astream(lc_messages):
                    yielded_any = True
                    yield chunk
                return  # stream completed
            except self._RETRYABLE_LLM_ERRORS as e:
                last_err = e
                if yielded_any:
                    _log.warning(
                        "LLM stream failed mid-stream (%s: %s); cannot retry",
                        type(e).__name__, e,
                    )
                    raise
                if attempt == 2:
                    raise
                wait = 2 ** attempt
                _log.warning(
                    "LLM stream failed (%s: %s); retry %d/3 in %ds",
                    type(e).__name__, e, attempt + 1, wait,
                )
                await asyncio.sleep(wait)
        if last_err is not None:
            raise last_err

    def _tool_schemas(self) -> list[dict]:
        """
            Define the tools schemas inspired by openai funciton calling a, langchain tools call. 
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.params_json_schema,
                },
            }
            for t in self.tools
        ]

    def _invoke_tool(self, name: str, args_json: str) -> str:
        """
            Manually runnig the tools selected by the model, take in the tools name and the args_json to select the tool. 
        """

        tool = next((t for t in self.tools if t.name == name), None)
        if tool is None:
            return f"unknown tool: {name}"
        ctx = ToolContext(
            context=None,
            tool_name=name,
            tool_call_id="loop",
            tool_arguments=args_json,
        )
        return str(asyncio.run(tool.on_invoke_tool(ctx, args_json)))

    def _maybe_summarize(self, name: str, result: str) -> str:
        if self.summarizer is None or len(result) <= self.summarize_threshold:
            return result
        return f"[summarized output of {name}]\n{self.summarizer.summarize(result)}"

    # ── async / TUI surface ─────────────────────────────────────────────
    #
    # `stream_events` is the canonical streaming entry point used by the
    # KODA adapter. It yields plain dict events (text_delta / tool_start /
    # tool_result / usage / done) so the adapter is a pure shape mapper.
    # The sync `_run_traced` above and `stream_events` below share no code
    # today; if drift becomes a problem, fold one into the other.

    def _build_messages(self, history: list[dict], user_message: str) -> list[dict]:
        """Compose the message list for one streamed turn.

        Recomposes AGENTS.md + git context fresh each turn so the system
        prompt reflects the *current* repo state — branch, dirty paths,
        recent commits — rather than a snapshot taken at construction.
        """
        composed_sp = _compose_instructions_cached(
            self._raw_system_prompt, self.project_root, model_name=self.model
        )
        msgs: list[dict] = [{"role": "system", "content": composed_sp}]
        for h in history:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant", "system") and isinstance(content, str):
                msgs.append({"role": role, "content": content})
        msgs.append({"role": "user", "content": user_message})
        return msgs

    async def _invoke_tool_async(self, name: str, args_json: str) -> tuple[str, bool]:
        """Run a tool via its FunctionTool ABI; convert exceptions to errors.

        Returns ``(output, is_error)``. A missing tool or an unhandled
        exception inside the tool maps to ``is_error=True`` so the loop
        can keep going and the model can recover, rather than crashing
        the entire stream.
        """
        tool = next((t for t in self.tools if t.name == name), None)
        if tool is None:
            valid = ", ".join(t.name for t in self.tools) or "(none registered)"
            return (
                f"unknown tool: {name!r}. Stop and reflect — which valid tool "
                f"did you actually mean? Available tools: {valid}",
                True,
            )
        ctx = ToolContext(
            context=None,
            tool_name=name,
            tool_call_id="loop",
            tool_arguments=args_json,
        )
        try:
            result = await tool.on_invoke_tool(ctx, args_json)
            return str(result), False
        except Exception as e:  # noqa: BLE001 — surface ALL tool failures as events
            return f"[error] tool {name} raised: {type(e).__name__}: {e}", True

    def _correct_bad_args(self, name: str, args_json: str, parse_err: str) -> str:
        """Build a self-correction tool_result for a tool call whose JSON
        arguments wouldn't parse.

        The model sees this as the tool's output on the next think step and
        can re-emit a clean call instead of crashing the turn.
        """
        snippet = (args_json or "")[:200]
        valid = ", ".join(t.name for t in self.tools) or "(none registered)"
        known = any(t.name == name for t in self.tools)
        hint = (
            f"The tool {name!r} exists but its arguments must be valid JSON."
            if known
            else f"And the tool name {name!r} is not registered. Available tools: {valid}."
        )
        return (
            f"[error] tool call to {name!r} had malformed JSON arguments "
            f"({parse_err}). Raw arguments: {snippet}\n\n"
            f"{hint}\n\n"
            "Stop and reflect: re-read the tool schema, then emit ONE "
            "well-formed tool call with valid, fully-closed JSON."
        )

    async def stream_events(
        self,
        message: str,
        history: list[dict] | None = None,
        max_steps: int = 200,
        cancel_event: "asyncio.Event | None" = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ):
        """Async generator emitting typed events for the KODA TUI.

        Event shapes (all dicts, ``type`` keys):
          ``{"type": "text_delta", "content": str}``
          ``{"type": "tool_start", "tool_id": str, "name": str, "arguments": dict}``
          ``{"type": "tool_result", "tool_id": str, "output": str, "is_error": bool}``
          ``{"type": "usage", "step": int, "step_usage": {...}, "run_total": {...}}``
          ``{"type": "done", "content": str, "max_steps_reached": bool}``

        Cancellation: pass an ``asyncio.Event``; when set, the loop exits
        cleanly between chunks, between tool calls, and between steps.
        Langfuse spans wrap the run and each tool call.
        """
        history = history or []
        session_id = session_id or os.getenv("KODA_SESSION_ID") or uuid.uuid4().hex

        with propagate_attributes(session_id=session_id, user_id=user_id):
            with self._lf.start_as_current_observation(
                name="coding_agent.stream",
                as_type="agent",
                input={"message": message, "history_len": len(history)},
            ) as run_span:
                messages = self._build_messages(history, message)
                usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                final_text = ""

                try:
                    for step in range(1, max_steps + 1):
                        if cancel_event is not None and cancel_event.is_set():
                            return

                        # ── Drive one provider round through LangChain ──
                        # ``self._chat_model`` is a ``BaseChatModel`` already
                        # ``.bind_tools()``ed in __init__, so the schemas
                        # never travel on the wire — only the messages do.
                        # ``convert_to_messages`` accepts the OpenAI-shape
                        # dicts we already keep (system / user / assistant
                        # +tool_calls / tool) and produces ``BaseMessage``s
                        # with the right per-role envelope.
                        lc_messages = convert_to_messages(messages)
                        content_parts: list[str] = []
                        tool_calls_acc: dict[int, dict] = {}
                        step_usage: dict | None = None

                        async for chunk in self._astream_with_retry(lc_messages):
                            if cancel_event is not None and cancel_event.is_set():
                                return
                            # Usage typically arrives on the final chunk for
                            # OpenAI/Anthropic-shape providers. Treat as
                            # cumulative for the current step.
                            usage_meta = getattr(chunk, "usage_metadata", None)
                            if usage_meta:
                                step_usage = {
                                    "prompt_tokens": int(usage_meta.get("input_tokens") or 0),
                                    "completion_tokens": int(usage_meta.get("output_tokens") or 0),
                                    "total_tokens": int(usage_meta.get("total_tokens") or 0),
                                }
                            # ``chunk.text`` is the public accessor that
                            # flattens both string ``content`` and
                            # content-block lists (Anthropic) into a single
                            # string. Yield it as a text_delta event — same
                            # shape the existing TUI extractor consumes.
                            text = getattr(chunk, "text", "") or ""
                            if text:
                                content_parts.append(text)
                                yield {"type": "text_delta", "content": text}
                            # Tool-call streaming — LangChain normalizes
                            # OpenAI deltas / Anthropic tool_use blocks into
                            # ``tool_call_chunks`` keyed by ``index``. Each
                            # chunk may carry partial ``args`` JSON; we
                            # accumulate into the same dict shape the rest
                            # of the loop already speaks (OpenAI-style
                            # ``{"id":..., "function":{"name":..., "arguments":...}}``).
                            for tc in getattr(chunk, "tool_call_chunks", None) or []:
                                idx = tc.get("index") or 0
                                slot = tool_calls_acc.setdefault(idx, {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                })
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                if tc.get("name"):
                                    slot["function"]["name"] += tc["name"] or ""
                                if tc.get("args"):
                                    slot["function"]["arguments"] += tc["args"] or ""

                        content = "".join(content_parts)
                        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]

                        if step_usage:
                            for k in usage_total:
                                usage_total[k] += step_usage.get(k, 0)
                            yield {
                                "type": "usage",
                                "step": step,
                                "step_usage": step_usage,
                                "run_total": dict(usage_total),
                            }

                        # Pre-validate tool calls so we can (a) scrub
                        # unparseable JSON args before sending the assistant
                        # message back to the provider — otherwise the next
                        # chat.completions.create() round-trips bad JSON and
                        # the server returns 400 — and (b) feed a corrective
                        # tool_result to the model so it can reflect and
                        # recover within the same turn.
                        bad_args: dict[int, tuple[dict, str]] = {}
                        for i, tc in enumerate(tool_calls):
                            raw = tc["function"].get("arguments") or "{}"
                            try:
                                parsed = json.loads(raw)
                                args_obj = parsed if isinstance(parsed, dict) else {"_value": parsed}
                                bad_args[i] = (args_obj, "")
                            except json.JSONDecodeError as je:
                                # Rewrite the persisted args so the API accepts
                                # the assistant message; remember the original
                                # so we can give the model a useful error.
                                tc["function"]["arguments"] = "{}"
                                bad_args[i] = ({"_raw": raw}, f"{type(je).__name__}: {je}")

                        assistant_msg: dict = {"role": "assistant", "content": content}
                        if tool_calls:
                            assistant_msg["tool_calls"] = tool_calls
                        messages.append(assistant_msg)

                        if not tool_calls:
                            final_text = content
                            yield {"type": "done", "content": content, "max_steps_reached": False}
                            return

                        # Phase 1: emit every tool_start up front so the UI
                        # shows the full fan-out immediately.
                        assigned_ids: list[str] = []
                        for i, tc in enumerate(tool_calls):
                            tool_id = tc["id"] or uuid.uuid4().hex
                            assigned_ids.append(tool_id)
                            yield {
                                "type": "tool_start",
                                "tool_id": tool_id,
                                "name": tc["function"]["name"],
                                "arguments": bad_args[i][0],
                            }

                        if cancel_event is not None and cancel_event.is_set():
                            return

                        # Phase 2: dispatch concurrently. Independent tool
                        # calls in the same step run in parallel — read_file
                        # x4 is no longer 4x sequential subprocess waits.
                        # Asyncio task contexts are copied per task, so the
                        # langfuse "current observation" stays scoped to
                        # each coroutine.
                        async def _dispatch(i: int, tc: dict) -> tuple[str, bool]:
                            name = tc["function"]["name"]
                            args_json = tc["function"]["arguments"] or "{}"
                            args_obj, parse_err = bad_args[i]
                            if parse_err:
                                return (
                                    self._correct_bad_args(
                                        name, args_obj.get("_raw", ""), parse_err
                                    ),
                                    True,
                                )
                            with self._lf.start_as_current_observation(
                                name=name,
                                as_type="tool",
                                input={"arguments": args_json},
                            ) as span:
                                output, is_error = await self._invoke_tool_async(name, args_json)
                                output = self._maybe_summarize(name, output)
                                try:
                                    span.update(
                                        output=output,
                                        level="ERROR" if is_error else None,
                                    )
                                except Exception:
                                    pass
                            return output, is_error

                        results = await asyncio.gather(
                            *(_dispatch(i, tc) for i, tc in enumerate(tool_calls))
                        )

                        # Phase 3: emit results and feed them back to the
                        # model in the original tool_calls order so the
                        # assistant message ↔ tool message pairing stays
                        # well-formed for the next chat.completions.create.
                        for i, tc in enumerate(tool_calls):
                            if cancel_event is not None and cancel_event.is_set():
                                return
                            tool_id = assigned_ids[i]
                            output, is_error = results[i]
                            yield {
                                "type": "tool_result",
                                "tool_id": tool_id,
                                "output": output,
                                "is_error": is_error,
                            }
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"] or tool_id,
                                "content": output,
                            })

                        # Compact in place at the end of the step so the
                        # next chat.astream sees the trimmed history. The
                        # compactor only fires when total chars cross the
                        # threshold and there's a safe boundary to cut on,
                        # so for short tasks this is a free no-op.
                        self._compact_if_needed(messages)

                    yield {
                        "type": "done",
                        "content": "[max steps reached]",
                        "max_steps_reached": True,
                    }
                finally:
                    self.last_usage = usage_total
                    try:
                        run_span.update(output={"usage": usage_total, "final_text_len": len(final_text)})
                    except Exception:
                        pass

    def run( self, user_query: str, max_steps: int = 200, verbose: bool = True, session_id: str | None = None, user_id: str | None = None, ) -> str:
        """
            Main runner class orhiestraction which call the LLM then tools , then give the response back , with doing the context ofload and stuff if needed it also have a sub class run_traced which basically put the trACES INTO THE LANGFUS 
        """
        session_id = session_id or os.getenv("KODA_SESSION_ID") or uuid.uuid4().hex
        with propagate_attributes(session_id=session_id, user_id=user_id):
            return self._run_traced(user_query, max_steps, verbose)

    @observe(name="coding_agent.run", as_type="agent")
    def _run_traced(self, user_query: str, max_steps: int, verbose: bool) -> str:

        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": user_query},
        ]

        # Cumulative usage across all steps in this run.
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for step in range(1, max_steps + 1):
            if verbose:
                _log.info("step %d: think", step)

            # Sync streaming via LangChain — same chunk shape we consume in
            # ``stream_events`` (see comments there). The retry path that
            # used to wrap this call lived only in the OpenAI-specific path;
            # callers that need retries should wrap ``run()``.
            lc_messages = convert_to_messages(messages)
            content_parts: list[str] = []
            tool_calls_acc: dict[int, dict] = {}
            step_usage: dict | None = None

            for chunk in self._chat_model.stream(lc_messages):
                usage_meta = getattr(chunk, "usage_metadata", None)
                if usage_meta:
                    step_usage = {
                        "prompt_tokens": int(usage_meta.get("input_tokens") or 0),
                        "completion_tokens": int(usage_meta.get("output_tokens") or 0),
                        "total_tokens": int(usage_meta.get("total_tokens") or 0),
                    }
                text = getattr(chunk, "text", "") or ""
                if text:
                    content_parts.append(text)
                    if verbose:
                        print(text, end="", flush=True)
                for tc in getattr(chunk, "tool_call_chunks", None) or []:
                    idx = tc.get("index") or 0
                    slot = tool_calls_acc.setdefault(idx, {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    if tc.get("name"):
                        slot["function"]["name"] += tc["name"] or ""
                    if tc.get("args"):
                        slot["function"]["arguments"] += tc["args"] or ""

            content = "".join(content_parts)
            tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
            if verbose and content:
                print()  # newline after streamed text

            if step_usage:
                for k in usage_total:
                    usage_total[k] += step_usage.get(k, 0)
                if verbose:
                    _log.info(
                        "step %d: usage in=%d out=%d total=%d (run total=%d)",
                        step,
                        step_usage["prompt_tokens"],
                        step_usage["completion_tokens"],
                        step_usage["total_tokens"],
                        usage_total["total_tokens"],
                    )

            assistant_msg: dict = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if not tool_calls:
                if verbose:
                    _log.info(
                        "step %d: final answer (run total tokens in=%d out=%d total=%d)",
                        step,
                        usage_total["prompt_tokens"],
                        usage_total["completion_tokens"],
                        usage_total["total_tokens"],
                    )
                self.last_usage = usage_total
                return content

            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["function"]["arguments"]
                if verbose:
                    _log.info("step %d: act -> %s(%s)", step, name, args)
                with self._lf.start_as_current_observation(name=name, as_type="tool", input={"arguments": args}) as span:
                    result = self._invoke_tool(name, args)
                    result = self._maybe_summarize(name, result)
                    span.update(output=result)
                if verbose:
                    preview = result[:200].replace("\n", " ")
                    _log.info(
                        "step %d: observe -> %s%s",
                        step, preview, "..." if len(result) > 200 else "",
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        self.last_usage = usage_total
        return "[max steps reached]"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Standalone CLI default: route the original MiniMax setup through the
    # OpenAI-compatible provider path. Override with KODA_DEFAULT_MODEL or
    # by passing a different ``model`` spec.
    model_spec = os.getenv("KODA_DEFAULT_MODEL", f"openai:{MINIMAX_MODEL_NAME}")
    agent = CodingAgent(
        model=model_spec,
        tools=_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        base_url=os.getenv("MINIMAX_BASE_URL"),
        api_key=os.getenv("MINIMAX_API_KEY"),
    )

    query = sys.argv[1] if len(sys.argv) > 1 else (
        "Summarize what the code in coding_agent/agent.py does in 3 sentences."
    )

    print(f"USER: {query}")
    answer = agent.run(query, max_steps=6, session_id=os.getenv("KODA_SESSION_ID"))
    print("\n=== FINAL ANSWER ===")
    print(answer)

    # Flush so spans land in Langfuse before the process exits.
    try:
        get_client().flush()
    except Exception:
        pass
