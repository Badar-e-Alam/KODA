"""KODA adapter for the local coding agent (``coding_agent/agent.py``).

``coding_agent.agent.build_agent`` is an *async* factory because the
graph it returns is wired to an ``AsyncSqliteSaver`` checkpointer, which
binds to the running asyncio event loop at construction. KODA builds
adapters from a worker thread (``asyncio.to_thread`` in
``koda/tui/app.py:_bootstrap_adapter``) with no loop running, so the
graph can't be built in ``__init__``.

This adapter therefore stores the config at construction time and
defers graph construction to the first ``_native_stream`` call, which
runs inside the TUI's event loop. ``describe()`` (sync) reports an
empty tool list pre-build and a populated one after the first turn —
the status-bar badges refresh on the next describe trigger
(``/model`` swap).

Usage::

    koda --agent coding_agent
    koda --agent coding_agent --model anthropic:claude-sonnet-4-6
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from coding_agent.agent import build_agent
from coding_agent.compaction import CompactionResult, compact_thread
from coding_agent.tracing import langfuse_callbacks

from koda.adapters.langgraph import LangGraphAdapter

_log = logging.getLogger("koda.adapters.coding_agent")


class CodingAgentAdapter(LangGraphAdapter):
    """Wraps the compiled ``coding_agent`` LangGraph for the KODA TUI.

    Graph construction is lazy — see module docstring for the why.
    """

    _backend = "coding-agent"

    def __init__(self, model: str, thread_id: str | None = None) -> None:
        # Initialise the LangGraph adapter with ``graph=None``; the
        # parent's introspection helpers tolerate that and report an
        # empty tool list until the graph is built.
        super().__init__(graph=None, model=model, thread_id=thread_id)
        # Concurrent first-streams could race on graph construction
        # (unlikely in the TUI, possible in eval harnesses) — guard with
        # a lock so only one ``build_agent`` call lands.
        self._graph_build_lock = asyncio.Lock()

    async def _ensure_graph(self) -> None:
        """Build the graph on first async use; cheap no-op afterwards."""
        if self._graph is not None:
            return
        async with self._graph_build_lock:
            if self._graph is not None:
                return
            _log.debug("building coding_agent graph for model=%s", self._model)
            self._graph = await build_agent(model=self._model)

    async def compact(self) -> CompactionResult:
        """Summarize older turns for this thread (the ``/compact`` command).

        Drives the summarization engine stashed on the graph at build time
        (``coding_agent.agent.build_agent``). Builds the graph first if the
        first turn hasn't run yet, so ``/compact`` works even on a freshly
        resumed session. Uses the same ``thread_id`` the streaming path does so
        the compaction lands on the active conversation.
        """
        await self._ensure_graph()
        engine = getattr(self._graph, "_koda_compact_engine", None)
        if engine is None:
            raise RuntimeError("This agent build does not support compaction.")
        config = {"configurable": {"thread_id": self._thread_id}}
        return await compact_thread(self._graph, engine, config)

    def _extra_callbacks(self) -> list[Any]:
        """Attach Langfuse to every turn (no-op when LANGFUSE_PUBLIC_KEY unset).

        ``langfuse_callbacks()`` is cached for the process lifetime — the
        handler is built once and re-attached on every ``astream_events``
        call. Without this, ``LangGraphAdapter`` would send no
        ``callbacks=`` to the graph and no spans would reach Langfuse.
        """
        return list(langfuse_callbacks())

    async def aclose(self) -> None:
        """Close the aiosqlite checkpoint connection on shutdown.

        ``aiosqlite.Connection`` runs the underlying ``sqlite3`` on a
        **non-daemon** worker thread (verified in aiosqlite 0.22 source).
        Without an explicit ``await conn.close()``, the thread keeps
        looping after Textual unmounts and the process hangs at the
        shell prompt with no traceback.

        Idempotent — KODA calls this from both the action handlers
        (Ctrl+D / 3rd Ctrl+C) and ``on_unmount`` (every shutdown path
        including OS signals), so the second call must be a clean no-op.
        We clear the stashed reference after the first close so the
        guard kicks in next time.
        """
        graph = self._graph
        if graph is None:
            return
        conn = getattr(graph, "_koda_checkpointer_conn", None)
        if conn is None:
            return
        try:
            await conn.close()
        except Exception:
            _log.debug("aiosqlite close() failed during shutdown", exc_info=True)
        finally:
            # Drop the reference so a second aclose() is a no-op even if
            # the underlying conn is wedged.
            try:
                delattr(graph, "_koda_checkpointer_conn")
            except AttributeError:
                pass


def create_coding_agent_adapter(
    model: str = "anthropic:claude-sonnet-4-6",
    thread_id: str | None = None,
) -> CodingAgentAdapter:
    """Build the coding-agent adapter. Used by ``koda --agent coding_agent``."""
    return CodingAgentAdapter(model=model, thread_id=thread_id)


__all__ = ["CodingAgentAdapter", "create_coding_agent_adapter"]
