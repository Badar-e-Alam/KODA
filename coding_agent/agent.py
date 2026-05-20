"""Agent factory and runner for `coding_agent`.

Builds a `deepagents` agent with the local shell backend so the model
can execute commands on the user's current directory in addition to the
built-in filesystem tools. Model resolution (including `kimi:` /
`ollama:` routing) lives in :mod:`coding_agent.model`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from deepagents import create_deep_agent
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from coding_agent.backend import build_backend
from coding_agent.model import resolve_model
from coding_agent.system_prompt_v2 import SYSTEM_PROMPT_V2
from coding_agent.tools import EXTRA_TOOLS
from coding_agent.tracing import langfuse_callbacks


def _needs_bootstrap(root: Path) -> bool:
    """True when ``<root>/AGENTS.md`` is missing or whitespace-only.

    Whitespace-only counts as empty so a stray ``\\n`` from ``touch
    AGENTS.md`` doesn't suppress the first-turn bootstrap. Anything
    with real content (frontmatter, prose, even a single non-whitespace
    char) is taken as "user-authored, don't overwrite." An unreadable
    file is *not* bootstrapped — we'd rather surface the error than
    clobber whatever's there.
    """
    agents_md = root / "AGENTS.md"
    if not agents_md.exists():
        return True
    try:
        return not agents_md.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False


def _render_system_prompt(root: Path) -> str:
    """Substitute the per-session template fields into ``SYSTEM_PROMPT_V2``.

    Session-start granularity (not per-turn): the date is captured once
    when ``build_agent`` runs and stays stable across the compiled
    graph's lifetime. A session crossing midnight gets a stale date
    until the next ``koda`` launch — accepted to keep the prompt cache
    hot across turns.
    """
    return SYSTEM_PROMPT_V2.format(
        current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        cwd=str(root.resolve()),
        bootstrap_required="true" if _needs_bootstrap(root) else "false",
    )


# ── Persistent memory (LangGraph checkpointer + thread scoping) ────────
#
# LangGraph's checkpointer persists graph state per ``thread_id`` after
# every super-step. With a SQLite saver pointed at a file, conversation
# history + tool state survives process restarts: invoke with the same
# ``thread_id`` later and the agent resumes from where it left off.
#
# We scope one thread per project cwd (hash of the resolved path), so
# running ``koda`` again in the same project picks up the prior history,
# while a different project gets a clean slate.


def _checkpoint_db_path(root: Path) -> Path:
    """Location of the SQLite checkpoint DB for a given project root."""
    return root / ".koda" / "checkpoints.db"


def _build_checkpointer(root: Path) -> AsyncSqliteSaver:
    """Construct the async SQLite checkpointer for ``<root>/.koda/checkpoints.db``.

    **Must be called from inside a running asyncio event loop.**
    ``AsyncSqliteSaver.__init__`` calls ``asyncio.get_running_loop()`` and
    binds to that loop — so constructing it on a worker thread raises
    ``RuntimeError: no running event loop``. The KODA adapter
    (``koda/adapters/coding_agent.py``) defers graph construction to the
    first async ``_native_stream`` call to satisfy this constraint.

    ``aiosqlite.connect(...)`` itself is called synchronously and returns
    a ``Connection`` proxy that hasn't started its worker thread yet;
    ``AsyncSqliteSaver`` opens it lazily on the first checkpoint write
    via its internal ``setup()``. ``check_same_thread=False`` is needed
    because LangGraph invokes from different threads under the TUI's
    asyncio loop. The connection is intentionally not closed — it lives
    for the process lifetime.
    """
    db_path = _checkpoint_db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = aiosqlite.connect(str(db_path), check_same_thread=False)
    return AsyncSqliteSaver(conn)


def _thread_id_for(root: Path) -> str:
    """Stable ``thread_id`` derived from the resolved project root.

    Same project = same conversation history across runs. Different
    projects don't collide. SHA256-truncated for a short, readable key.
    """
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]


async def build_agent(
    *,
    model: str | None = None,
    cwd: str | Path | None = None,
    timeout: int = 180,
    inherit_env: bool = True,
) -> CompiledStateGraph:
    """Construct the coding agent.

    **Async** because :func:`_build_checkpointer` returns an
    ``AsyncSqliteSaver`` which binds to the running event loop at
    construction. Callers in a worker-thread/sync context (the TUI's
    adapter factory) defer this call into their first async path; see
    ``koda/adapters/coding_agent.CodingAgentAdapter._ensure_graph``.

    Args:
        model: Provider-prefixed model spec (e.g. `anthropic:...`,
            `openai:...`, `ollama:...`, `kimi:...`). Defaults to
            `KODA_DEFAULT_MODEL` or sonnet-4-6.
        cwd: Working directory the agent reads/writes/executes against.
            Defaults to the process CWD.
        timeout: Shell command timeout in seconds.
        inherit_env: Pass through the parent process env to subshells.
            Needed for PATH/API keys to be visible to commands the agent
            runs.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    root = Path(cwd) if cwd else Path.cwd()
    backend = build_backend(root, timeout=timeout, inherit_env=inherit_env)
    checkpointer = _build_checkpointer(root)

    graph = create_deep_agent(
        model=resolve_model(model),
        backend=backend,
        # Extras layered on top of the deepagents defaults (`execute`,
        # `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`,
        # `write_todos`, `task`). See coding_agent/tools.py.
        tools=EXTRA_TOOLS,
        # Skill files live under the FilesystemBackend mounted at /skills/
        # inside the composite backend (see coding_agent/backend.py).
        skills=["/skills/"],
        # System prompt is rendered per-session with current_date / cwd /
        # bootstrap_required substituted in. See _render_system_prompt
        # and coding_agent/system_prompt_v2.py for the template.
        system_prompt=_render_system_prompt(root),
        # Load AGENTS.md from the project root as durable project context.
        # deepagents' MemoryMiddleware silently skips this if the file doesn't
        # exist, and injects the contents under <agent_memory> in the system
        # prompt. Path is relative to the backend root (cwd). When AGENTS.md
        # is missing or whitespace-only, _render_system_prompt sets
        # bootstrap_required=true so the agent writes one on its first turn.
        memory=["/AGENTS.md"],
        # Persist graph state to disk so conversations survive restarts.
        # Caller must pass ``configurable.thread_id`` on invoke/stream;
        # ``run()`` below derives one from cwd via ``_thread_id_for``.
        checkpointer=checkpointer,
        name="coding_agent",
    )
    # Stash the aiosqlite Connection on the graph so the adapter can
    # close it on shutdown. aiosqlite's worker thread is non-daemon, so
    # without an explicit ``await conn.close()`` it pins the process
    # open after Textual unmounts (terminal appears to hang).
    graph._koda_checkpointer_conn = checkpointer.conn  # type: ignore[attr-defined]
    return graph


async def run(
    prompt: str,
    *,
    model: str | None = None,
    cwd: str | Path | None = None,
    thread_id: str | None = None,
) -> dict:
    """One-shot async invocation. Returns the final agent state dict.

    Async because :func:`build_agent` is — the underlying async SQLite
    checkpointer requires a running event loop. For a sync-friendly
    caller, wrap the call: ``asyncio.run(run(prompt, ...))``.

    ``thread_id`` selects which persisted conversation to resume. When
    omitted, defaults to a stable hash of the project root so repeated
    runs in the same directory continue the same thread.
    """
    agent = await build_agent(model=model, cwd=cwd)
    root = Path(cwd) if cwd else Path.cwd()
    tid = thread_id or _thread_id_for(root)
    return await agent.ainvoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config=invocation_config(thread_id=tid),
    )


def invocation_config(
    extra: dict[str, Any] | None = None,
    recursion_limit: int = 5000,
    *,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Build the per-call config dict for `graph.invoke` / `graph.stream`.

    Merges Langfuse callbacks into anything the caller passes via `extra`.
    ``thread_id`` is required for checkpointed graphs — without it LangGraph
    raises on invoke. ``coding_agent`` doesn't override LangGraph's
    recursion limit here; deepagents pins ``recursion_limit=9_999`` on the
    compiled graph and callers can still override via the config dict.
    """
    config: dict[str, Any] = {"callbacks": langfuse_callbacks()}
    if thread_id is not None:
        config["configurable"] = {"thread_id": thread_id}
    if extra:
        if "callbacks" in extra:
            extra_cbs = extra["callbacks"] or []
            config["callbacks"] = config["callbacks"] + list(extra_cbs)
        for k, v in extra.items():
            if k == "callbacks":
                continue
            if k == "configurable":
                # Caller's configurable wins on conflict but we keep
                # any keys we set above that they didn't override.
                merged = {**config.get("configurable", {}), **v}
                config["configurable"] = merged
                continue
            config[k] = v
    return config
