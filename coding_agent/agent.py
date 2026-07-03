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
from coding_agent.compaction import (
    build_context_editing_middleware,
    build_manual_compaction_engine,
)
from coding_agent.model import resolve_model
from coding_agent.subagents import SUBAGENTS
from coding_agent.system_prompt_v2 import SYSTEM_PROMPT_V2
from coding_agent.mcp import load_mcp_tools
from coding_agent.tools import EXTRA_TOOLS
from coding_agent.tracing import langfuse_callbacks

from koda.tools import permissions as _perms


def _render_system_prompt(root: Path) -> str:
    """Substitute the per-session template fields into ``SYSTEM_PROMPT_V2``.

    Session-start granularity (not per-turn): the date is captured once
    when ``build_agent`` runs and stays stable across the compiled
    graph's lifetime. A session crossing midnight gets a stale date
    until the next ``koda`` launch — accepted to keep the prompt cache
    hot across turns.

    There is no longer a "bootstrap_required" field: KODA does not
    auto-create ``AGENTS.md`` at startup. The agent builds its knowledge
    base organically (see the ``<Project knowledge base>`` block in the
    prompt) only when it has something durable to record.
    """
    return SYSTEM_PROMPT_V2.format(
        current_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        cwd=str(root.resolve()),
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


def _load_async_subagents(root: Path) -> list[dict]:
    """Optional REMOTE async subagents from ``<root>/.koda/async_subagents.json``.

    Entries follow deepagents' official ``AsyncSubAgent`` spec
    (https://docs.langchain.com/oss/python/deepagents/async-subagents):
    ``[{"name", "description", "graph_id", "url"?, "headers"?}]``. When
    present they're passed straight to ``create_deep_agent``, whose
    ``AsyncSubAgentMiddleware`` provides the official
    ``start_async_task`` / ``check_async_task`` / … tools backed by an Agent
    Protocol server (LangGraph Platform or self-hosted). KODA's in-process
    equivalents (``koda/subagent_tools.py``) are removed in that case so the
    tool names don't collide. Without the file, the in-process variants serve
    the same five-tool API locally.
    """
    cfg = root / ".koda" / "async_subagents.json"
    if not cfg.is_file():
        return []
    try:
        import json

        specs = json.loads(cfg.read_text())
    except Exception:
        return []
    if not isinstance(specs, list):
        return []
    return [
        s for s in specs
        if isinstance(s, dict) and s.get("name") and s.get("description") and s.get("graph_id")
    ]


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
    resolved_model = resolve_model(model)

    # Context management (see coding_agent/compaction.py):
    #   • ContextEditingMiddleware — always-on, Claude-Code-style clearing of
    #     stale tool *results* once the transcript crosses a token threshold.
    #     Layered on top of the auto-summarization middleware deepagents wires
    #     in by default.
    #   • A standalone summarization engine drives the manual ``/compact``
    #     command (see ``CodingAgentAdapter.compact``); it's stashed on the
    #     graph below rather than added to the chain.
    extra_middleware = []
    context_editing = build_context_editing_middleware()
    if context_editing is not None:
        extra_middleware.append(context_editing)

    # MCP tools — loaded from .mcp.json (Context7 for up-to-date library
    # docs, etc.) via langchain-mcp-adapters. Non-fatal: returns [] when
    # MCP is unconfigured or langchain-mcp-adapters isn't installed, so the
    # agent always starts with at least its built-in tools. See
    # coding_agent/mcp.py.
    mcp_tools = await load_mcp_tools(root)
    all_tools = list(EXTRA_TOOLS) + mcp_tools

    # Remote async subagents (official deepagents transport) — when configured,
    # the AsyncSubAgentMiddleware owns start_async_task/…, so drop KODA's
    # in-process versions of those tool names.
    async_specs = _load_async_subagents(root)
    if async_specs:
        try:
            from koda.subagent_tools import ASYNC_TASK_TOOL_NAMES

            all_tools = [
                t for t in all_tools
                if getattr(t, "name", "") not in ASYNC_TASK_TOOL_NAMES
            ]
        except Exception:
            pass

    graph = create_deep_agent(
        model=resolved_model,
        backend=backend,
        # Additional middleware layered on top of deepagents' defaults.
        middleware=extra_middleware,
        # Extras layered on top of the deepagents defaults (`execute`,
        # `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`,
        # `write_todos`, `task`) plus any MCP tools loaded above.
        # See coding_agent/tools.py and coding_agent/mcp.py.
        tools=all_tools,
        # Skill files live under the FilesystemBackend mounted at /skills/
        # inside the composite backend (see coding_agent/backend.py).
        skills=["/skills/"],
        # System prompt is rendered per-session with current_date / cwd /
        # bootstrap_required substituted in. See _render_system_prompt
        # and coding_agent/system_prompt_v2.py for the template.
        system_prompt=_render_system_prompt(root),
        # Durable context auto-injected under <agent_memory> every turn:
        # AGENTS.md (the knowledge-base hub/index) and user_preferences.md
        # (small, globally relevant). deepagents' MemoryMiddleware silently
        # skips a file that doesn't exist yet, so listing them before they're
        # created is safe and there is no startup bootstrap. Topic sub-pages
        # (architecture.md, frontend.md, api.md, project_history.md, …) are
        # intentionally NOT auto-loaded — they can grow large and are read on
        # demand via the links in AGENTS.md. The agent creates any of these
        # files only when it has durable info to record; see the
        # <Project knowledge base> block in coding_agent/system_prompt_v2.py.
        memory=["/AGENTS.md", "/user_preferences.md"],
        # Persist graph state to disk so conversations survive restarts.
        # Caller must pass ``configurable.thread_id`` on invoke/stream;
        # ``run()`` below derives one from cwd via ``_thread_id_for``.
        checkpointer=checkpointer,
        # Human-in-the-loop gate for mutating tools. deepagents wires
        # ``HumanInTheLoopMiddleware`` so that before any of these tools
        # runs, the graph hits ``interrupt()`` — it pauses and checkpoints
        # its state instead of mutating. KODA's adapter
        # (``koda/adapters/langgraph.py``) surfaces that as a
        # ``PermissionRequest`` to the TUI and resumes via
        # ``Command(resume=…)`` once the user decides. The gated-tool set
        # and the approve/reject/ask policy both live in
        # ``koda/tools/permissions.py`` so they stay in one place.
        interrupt_on=_perms.INTERRUPT_ON,
        # Specialist subagents (explore / plan / edit) the main agent
        # dispatches via the built-in ``task`` tool, plus any configured
        # REMOTE async subagents (identified by their ``graph_id`` field and
        # routed to AsyncSubAgentMiddleware). See ``coding_agent/subagents.py``
        # and ``_load_async_subagents`` above.
        subagents=list(SUBAGENTS) + async_specs,
        name="coding_agent",
    )
    # Stash the aiosqlite Connection on the graph so the adapter can
    # close it on shutdown. aiosqlite's worker thread is non-daemon, so
    # without an explicit ``await conn.close()`` it pins the process
    # open after Textual unmounts (terminal appears to hang).
    graph._koda_checkpointer_conn = checkpointer.conn  # type: ignore[attr-defined]
    # Stash the summarization engine the ``/compact`` command drives on
    # demand (see coding_agent/compaction.compact_thread).
    graph._koda_compact_engine = build_manual_compaction_engine(  # type: ignore[attr-defined]
        resolved_model, backend
    )
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
