"""
LangGraph → KodaAgent adapter.

Wraps any compiled LangGraph graph (anything with `astream_events(v2)`) into
the `KodaAgent` Protocol expected by the TUI. Maps LangGraph event types:

  on_chat_model_stream -> TextDelta / ThinkingDelta
  on_tool_start        -> ToolStart
  on_tool_end          -> ToolResult
  final usage snapshot -> Usage in Done

The graph is called once per user turn with {"messages": [...]} and a
thread-scoped config if a checkpointer is configured upstream.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator

from koda.agent_api import (
    AgentEvent,
    Done,
    KodaAgent,
    TextDelta,
    ThinkingDelta,
    ToolResult,
    ToolStart,
    Usage,
)

_log = logging.getLogger("koda.adapters.langgraph")


class LangGraphAdapter(KodaAgent):
    """Wrap a compiled LangGraph graph as a KodaAgent."""

    def __init__(self, graph: Any, model: str, thread_id: str | None = None) -> None:
        self._graph = graph
        self._model = model
        self._thread_id = thread_id or uuid.uuid4().hex
        self._cancel = asyncio.Event()

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        self._cancel.set()

    async def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]:
        """Yield KODA events for one user turn."""
        self._cancel.clear()

        messages = _history_to_langchain(history) + [{"role": "user", "content": message}]
        config = {"configurable": {"thread_id": self._thread_id}}

        usage = Usage()

        try:
            async for event in self._graph.astream_events(
                {"messages": messages}, config=config, version="v2"
            ):
                if self._cancel.is_set():
                    break

                kind = event.get("event")
                data = event.get("data", {}) or {}

                if kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk is None:
                        continue
                    for ev in _chat_chunk_events(chunk, usage):
                        yield ev

                elif kind == "on_tool_start":
                    tool_id = event.get("run_id") or uuid.uuid4().hex
                    name = event.get("name") or "tool"
                    args = data.get("input") or {}
                    if not isinstance(args, dict):
                        args = {"input": args}
                    yield ToolStart(tool_id=tool_id, name=name, arguments=args)

                elif kind == "on_tool_end":
                    tool_id = event.get("run_id") or uuid.uuid4().hex
                    output = data.get("output")
                    text, is_error = _stringify_tool_output(output)
                    yield ToolResult(tool_id=tool_id, output=text, is_error=is_error)

        except asyncio.CancelledError:
            _log.info("LangGraph stream cancelled")
            raise
        except Exception as e:
            _log.exception("LangGraph stream failed")
            yield ToolResult(
                tool_id="adapter_error",
                output=f"Agent error: {type(e).__name__}: {e}",
                is_error=True,
            )

        yield Done(usage=usage if _has_usage(usage) else None)


# ── Helpers ────────────────────────────────────────────────────────────

def _history_to_langchain(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass through {role, content} dicts — LangGraph accepts them directly."""
    return [h for h in history if h.get("role") in ("user", "assistant", "system")]


def _chat_chunk_events(chunk: Any, usage: Usage) -> list[AgentEvent]:
    """Translate one AIMessageChunk into zero or more AgentEvents.

    Mutates `usage` in place if the chunk carries usage_metadata.
    """
    out: list[AgentEvent] = []

    # Usage metadata (appears on the final chunk with most providers)
    meta = getattr(chunk, "usage_metadata", None)
    if meta:
        usage.input_tokens = meta.get("input_tokens", usage.input_tokens)
        usage.output_tokens = meta.get("output_tokens", usage.output_tokens)
        details_in = meta.get("input_token_details") or {}
        usage.cache_read_tokens = details_in.get("cache_read", usage.cache_read_tokens)
        usage.cache_write_tokens = details_in.get("cache_creation", usage.cache_write_tokens)
        out.append(Usage(**usage.__dict__))

    content = getattr(chunk, "content", "")
    # Anthropic-style multimodal: content is a list of blocks with 'type'
    if isinstance(content, list):
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

    # Some providers surface reasoning in additional_kwargs
    extra = getattr(chunk, "additional_kwargs", None) or {}
    reasoning = extra.get("reasoning_content") or extra.get("thinking")
    if isinstance(reasoning, str) and reasoning:
        out.append(ThinkingDelta(content=reasoning))

    return out


def _stringify_tool_output(output: Any) -> tuple[str, bool]:
    """Return (text, is_error) for a LangGraph tool end event's output."""
    is_error = False
    if output is None:
        return "", False
    # ToolMessage-like
    for attr in ("content", "text"):
        val = getattr(output, attr, None)
        if val is not None:
            status = getattr(output, "status", None)
            is_error = status == "error"
            return str(val), is_error
    if isinstance(output, dict):
        if "content" in output:
            return str(output["content"]), bool(output.get("is_error", False))
        return repr(output), False
    return str(output), False


def _has_usage(u: Usage) -> bool:
    return any((u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens))
