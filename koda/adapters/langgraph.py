"""LangGraph → KodaAgent adapter.

Wraps any compiled LangGraph graph (anything with ``astream_events(v2)``)
and translates its event stream to KODA's typed `AgentEvent`s. All the
reusable plumbing (cancel, usage, error handling, final Done) lives in
``BaseAdapter``; this file just supplies:

  1. ``_native_stream`` — how to iterate the graph
  2. three tiny extractors — one per LangGraph event type we care about
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, AsyncIterator, Iterable

from koda.adapters.base import (
    BaseAdapter,
    model_supports_thinking,
    model_supports_vision,
    tools_from_objects,
)
from koda.agent_api import (
    AgentDescription,
    AgentEvent,
    TextDelta,
    ThinkingDelta,
    ToolDescription,
    ToolResult,
    ToolStart,
    Usage,
)

_log = logging.getLogger("koda.adapters.langgraph")

# LangGraph defaults to 25 steps per turn, which runs out on multi-step
# research tasks (scrape → parse → convert → write PDF → verify). Bump
# to 100 by default; override with KODA_RECURSION_LIMIT.
_DEFAULT_RECURSION_LIMIT = int(os.environ.get("KODA_RECURSION_LIMIT", "1000"))


class LangGraphAdapter(BaseAdapter):
    """Wrap a compiled LangGraph graph as a KodaAgent."""

    _backend = "langgraph"

    def __init__(self, graph: Any, model: str, thread_id: str | None = None) -> None:
        super().__init__(model=model, thread_id=thread_id)
        self._graph = graph
        # One-shot seed guard for graphs without a checkpointer — see
        # _native_stream for the rationale. Flipped to True after the
        # first turn so subsequent turns don't re-forward history.
        self._seeded: bool = False
        # Bind per-instance so subclasses (or tests) can swap them out.
        self._extractors = (
            _extract_chat_stream,
            _extract_tool_start,
            _extract_tool_end,
        )

    def mark_seeded(self) -> None:
        """Declare that the upstream graph already has this thread's history.

        Use after rebuilding the adapter for a thread that already has a
        checkpoint (model switch, session resume, memory reload). Without
        this, the next ``_native_stream`` would prepend the caller's
        ``history`` argument to the input messages and LangGraph's
        ``add_messages`` reducer would duplicate every prior turn on top
        of what the checkpointer replays.
        """
        self._seeded = True

    def describe(self) -> AgentDescription:
        return AgentDescription(
            name=self._model,
            backend=self._backend,
            supports_thinking=model_supports_thinking(self._model),
            supports_vision=model_supports_vision(self._model),
            tools=_introspect_graph_tools(self._graph),
            system_prompt_preview=None,
        )

    def _extra_callbacks(self) -> list[Any]:
        """Per-adapter callback hook injected into every ``astream_events`` call.

        Default is empty so the base adapter stays agent-agnostic. Subclasses
        override to attach tracing (e.g. ``CodingAgentAdapter`` returns
        ``coding_agent.tracing.langfuse_callbacks()`` so every turn lands in
        Langfuse without the TUI knowing).
        """
        return []

    async def _native_stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Drive the underlying LangGraph graph for one user turn.

        History is **not** forwarded: LangGraph's checkpointer already
        persists the thread's message state under ``thread_id`` and the
        default ``add_messages`` reducer would just append duplicates
        (no stable IDs on plain role/content dicts). We hand it only the
        new user message. Prior messages replay automatically from the
        checkpoint; prompt caches stay hot across turns.

        The ``history`` argument is kept in the signature for the
        stateless-adapter case (e.g. a graph built without a checkpointer
        by a user via ``--agent``). If the graph has no thread state yet
        and history is non-empty, we seed the first call with it.
        """
        config: dict[str, Any] = {
            "configurable": {"thread_id": self._thread_id},
            "recursion_limit": _DEFAULT_RECURSION_LIMIT,
        }
        extra_cbs = self._extra_callbacks()
        if extra_cbs:
            config["callbacks"] = extra_cbs

        input_messages: list[dict[str, Any]] = [
            {"role": "user", "content": message}
        ]
        if history and not self._seeded:
            # One-shot seed for graphs without a checkpointer (or when we
            # resumed a session from disk — state starts empty even though
            # our UI has history). Subsequent turns skip this path.
            input_messages = _history_to_langchain(history) + input_messages
        self._seeded = True

        async for event in self._graph.astream_events(
            {"messages": input_messages}, config=config, version="v2"
        ):
            yield event


# ── Extractors ──────────────────────────────────────────────────────
#
# Each takes one raw LangGraph event dict and yields zero or more
# AgentEvents. They are stateless — any accumulation (Usage, tool
# pairing) is handled by BaseAdapter or downstream in the TUI.


def _extract_chat_stream(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    if event.get("event") != "on_chat_model_stream":
        return None
    chunk = (event.get("data") or {}).get("chunk")
    if chunk is None:
        return None
    return _chat_chunk_events(chunk)


def _extract_tool_start(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    if event.get("event") != "on_tool_start":
        return None
    tool_id = event.get("run_id") or uuid.uuid4().hex
    name = event.get("name") or "tool"
    args = (event.get("data") or {}).get("input") or {}
    if not isinstance(args, dict):
        args = {"input": args}
    return (ToolStart(tool_id=tool_id, name=name, arguments=args),)


def _extract_tool_end(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    if event.get("event") != "on_tool_end":
        return None
    tool_id = event.get("run_id") or uuid.uuid4().hex
    output = (event.get("data") or {}).get("output")
    text, is_error = _stringify_tool_output(output)
    return (ToolResult(tool_id=tool_id, output=text, is_error=is_error),)


# ── Helpers ─────────────────────────────────────────────────────────

def _history_to_langchain(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass through {role, content} dicts — LangGraph accepts them directly."""
    return [h for h in history if h.get("role") in ("user", "assistant", "system")]


def _chat_chunk_events(chunk: Any) -> list[AgentEvent]:
    """Translate one AIMessageChunk into zero or more AgentEvents."""
    out: list[AgentEvent] = []

    # Usage metadata (appears on the final chunk with most providers)
    meta = getattr(chunk, "usage_metadata", None)
    if meta:
        details_in = meta.get("input_token_details") or {}
        out.append(
            Usage(
                input_tokens=meta.get("input_tokens", 0) or 0,
                output_tokens=meta.get("output_tokens", 0) or 0,
                cache_read_tokens=details_in.get("cache_read", 0) or 0,
                cache_write_tokens=details_in.get("cache_creation", 0) or 0,
            )
        )

    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        # Anthropic-style multimodal: list of typed blocks
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if text:
                    out.append(TextDelta(content=text))
            elif btype in ("thinking", "reasoning"):
                text = block.get("thinking") or block.get("text") or ""
                if text:
                    out.append(ThinkingDelta(content=text))
    elif isinstance(content, str) and content:
        out.append(TextDelta(content=content))

    extra = getattr(chunk, "additional_kwargs", None) or {}
    reasoning = extra.get("reasoning_content") or extra.get("thinking")
    if isinstance(reasoning, str) and reasoning:
        out.append(ThinkingDelta(content=reasoning))

    return out


def _stringify_tool_output(output: Any) -> tuple[str, bool]:
    """Return (text, is_error) for a LangGraph tool end event's output."""
    if output is None:
        return "", False
    for attr in ("content", "text"):
        val = getattr(output, attr, None)
        if val is not None:
            status = getattr(output, "status", None)
            return str(val), status == "error"
    if isinstance(output, dict):
        if "content" in output:
            return str(output["content"]), bool(output.get("is_error", False))
        return repr(output), False
    return str(output), False


def _introspect_graph_tools(graph: Any) -> tuple[ToolDescription, ...]:
    """Best-effort extraction of the tools wired into a compiled LangGraph graph.

    ``create_react_agent`` exposes its tool node at a few different paths
    depending on the LangGraph version. We try each in turn and silently
    return an empty tuple if nothing matches — the TUI's ``/tools`` command
    will just report "no tool surface" instead of crashing.
    """
    if graph is None:
        return ()

    # Common attribute paths across LangGraph versions and graph styles.
    # The deepagents path (StateNodeSpec.runnable: ToolNode) is tried first
    # because that's the layout KODA's coding-agent uses; older
    # ``create_react_agent`` layouts (``.data.tools_by_name``) follow.
    candidates: list[Any] = []
    for getter in (
        lambda g: g.builder.nodes["tools"].runnable.tools_by_name.values(),
        lambda g: g.nodes["tools"].runnable.tools_by_name.values(),
        lambda g: g.nodes["tools"].data.tools_by_name.values(),
        lambda g: g.nodes["tools"].data.tools,
        lambda g: g.builder.nodes["tools"].data.tools_by_name.values(),
        lambda g: g.builder.nodes["tools"].data.tools,
        lambda g: g.get_graph().nodes["tools"].data.tools,
    ):
        try:
            tools = list(getter(graph))
        except Exception:
            continue
        if tools:
            candidates = tools
            break

    if not candidates:
        return ()
    return tools_from_objects(candidates)


__all__ = ["LangGraphAdapter"]
