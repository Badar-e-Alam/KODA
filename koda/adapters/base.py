"""Shared plumbing for ``KodaAgent`` adapters.

The ``KodaAgent`` Protocol is deliberately small (three methods). Every
time we wrap a new SDK (LangGraph, Anthropic, OpenAI, Gemini, custom
HTTP backends...) the *reusable* parts are always the same:

  * cancel flag (`interrupt()` sets an `asyncio.Event`)
  * running `Usage` accumulator
  * error → `ToolResult(is_error=True)` conversion so the TUI can render
    a clean failure instead of crashing the stream
  * a final `Done(usage=...)` event, always

``BaseAdapter`` captures that. Subclasses only have to:

  1. Implement ``_native_stream(message, history)`` — an async generator
     yielding whatever raw chunks the underlying SDK emits.
  2. Assign a tuple of ``_extractors`` — plain ``(chunk) -> Iterable[AgentEvent]``
     callables. Each extractor looks at a chunk and yields zero or more
     typed KODA events.

That's it. A new SDK adapter is usually 40-80 lines.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator, Callable, Iterable, Sequence

from koda.agent_api import (
    AgentEvent,
    Done,
    KodaAgent,
    ToolResult,
    Usage,
)

_log = logging.getLogger("koda.adapters.base")

# An extractor turns one native chunk into zero or more KODA events.
Extractor = Callable[[Any], Iterable[AgentEvent] | None]


def merge_usage(accum: Usage, fresh: Usage) -> None:
    """Fold a freshly-observed Usage snapshot into the running total.

    Most providers emit cumulative usage, so we take max()-ish semantics:
    any non-zero field on ``fresh`` overrides the accumulator. For
    per-chunk deltas, pass ``fresh`` with strictly incremental values —
    the result is the same.
    """
    for attr in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        v = getattr(fresh, attr, 0) or 0
        if v:
            setattr(accum, attr, v)


def _has_usage(u: Usage) -> bool:
    return any((u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens))


class BaseAdapter(KodaAgent):
    """Reusable KodaAgent implementation driven by a native stream +
    a tuple of per-chunk extractors."""

    # Subclass hook — override with a tuple of Extractor callables.
    _extractors: Sequence[Extractor] = ()

    def __init__(self, model: str, thread_id: str | None = None) -> None:
        self._model = model
        self._thread_id = thread_id or uuid.uuid4().hex
        self._cancel = asyncio.Event()

    # ── KodaAgent interface ──────────────────────────────────────────

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        self._cancel.set()

    def reset_history(self, thread_id: str | None = None) -> None:
        """Drop any cross-turn state so the next ``stream()`` call starts
        fresh from the ``history`` argument alone.

        Default is a no-op: stateless adapters (coding_agent, anthropic)
        already redrive themselves from the per-turn ``history``. Adapters
        that keep state across turns (LangGraph's checkpointer) override
        this to forget the abandoned branch.
        """
        if thread_id is not None:
            self._thread_id = thread_id

    async def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn end-to-end. Emits events, cleans up cancel state,
        always yields a final ``Done``."""
        self._cancel.clear()
        usage = Usage()

        try:
            async for chunk in self._native_stream(message, history):
                if self._cancel.is_set():
                    break
                for extractor in self._extractors:
                    produced = extractor(chunk)
                    if not produced:
                        continue
                    for ev in produced:
                        if isinstance(ev, Usage):
                            merge_usage(usage, ev)
                        yield ev
        except asyncio.CancelledError:
            _log.info("%s stream cancelled", type(self).__name__)
            raise
        except Exception as e:
            _log.exception("%s stream failed", type(self).__name__)
            yield ToolResult(
                tool_id="adapter_error",
                output=f"Agent error: {type(e).__name__}: {e}",
                is_error=True,
            )

        yield Done(usage=Usage(**usage.__dict__) if _has_usage(usage) else None)

    # ── Subclass contract ────────────────────────────────────────────

    async def _native_stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[Any]:
        """Yield native chunks from the underlying SDK. Must be overridden."""
        raise NotImplementedError
        # pragma: no cover - generator-return protocol
        yield  # type: ignore[unreachable]


__all__ = ["BaseAdapter", "Extractor", "merge_usage"]
