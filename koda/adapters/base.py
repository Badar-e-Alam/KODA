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
    AgentDescription,
    AgentEvent,
    Done,
    KodaAgent,
    ToolDescription,
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

    # Subclasses set this to surface their backend in ``describe()``.
    # Kept as a class attribute so the default ``describe`` works without
    # subclass boilerplate when only the backend name needs to change.
    _backend: str = "unknown"

    def __init__(self, model: str, thread_id: str | None = None) -> None:
        self._model = model
        self._thread_id = thread_id or uuid.uuid4().hex
        self._cancel = asyncio.Event()

    # ── KodaAgent interface ──────────────────────────────────────────

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        self._cancel.set()

    async def aclose(self) -> None:
        """Best-effort: release any non-daemon resources held by the adapter.

        Default is a no-op. Subclasses that hold long-lived async resources
        (notably ``aiosqlite`` connections under a LangGraph checkpointer,
        whose worker thread is *non-daemon* and otherwise blocks process
        exit) override this and clean up. KODA's TUI calls this from
        its quit paths before ``self.exit()``.
        """
        return None

    async def _ensure_graph(self) -> None:
        """Hook for adapters whose backend graph is built lazily.

        Default no-op. ``CodingAgentAdapter`` overrides it to build its
        compiled LangGraph on first async use (its ``AsyncSqliteSaver``
        must bind to a running loop). ``LangGraphAdapter._native_stream``
        awaits this before streaming, and the TUI warms it at startup so
        the first turn never pays the build cost mid-stream.
        """
        return None

    def describe(self) -> AgentDescription:
        """Default adapter description. Override to report tools,
        capability flags, or a system-prompt preview."""
        return AgentDescription(name=self._model, backend=self._backend)

    async def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn end-to-end. Emits events, cleans up cancel state,
        always yields a final ``Done``.

        The chunk loop is built around ``asyncio.wait`` so the cancel
        signal interrupts a stalled stream within ~50 ms instead of
        waiting for the next ``__anext__`` to resolve. Without this,
        Ctrl+C looks dead during slow LLM responses or long tool
        executions because ``async for`` blocks on the awaitable from
        the native stream and never re-enters the loop body to see
        ``self._cancel.is_set()``.
        """
        self._cancel.clear()
        usage = Usage()

        stream_iter = self._native_stream(message, history).__aiter__()
        try:
            while True:
                next_task = asyncio.ensure_future(stream_iter.__anext__())
                cancel_task = asyncio.ensure_future(self._cancel.wait())
                try:
                    done, pending = await asyncio.wait(
                        {next_task, cancel_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    # Outer task was cancelled (Ctrl+C → ``self._turn_task.cancel()``).
                    next_task.cancel()
                    cancel_task.cancel()
                    raise

                # Whichever future didn't complete needs to be cancelled
                # so we don't leak a pending ``__anext__`` waiting on the
                # SDK forever. ``cancel_task`` is just an Event.wait() —
                # cheap to cancel.
                for p in pending:
                    p.cancel()

                if cancel_task in done:
                    # User asked to stop. Best-effort cleanup of the
                    # native iterator before breaking out.
                    next_task.cancel()
                    aclose = getattr(stream_iter, "aclose", None)
                    if aclose is not None:
                        try:
                            await aclose()
                        except Exception:
                            pass
                    break

                # Native stream produced a chunk (or raised).
                try:
                    chunk = next_task.result()
                except StopAsyncIteration:
                    break
                except asyncio.CancelledError:
                    # next_task was cancelled out from under us — treat
                    # as a stop signal.
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


_THINKING_HINTS = (
    "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
    "claude-3-7", "o1-", "o3-", "deepseek-r", "deepseek-v3",
)
_VISION_HINTS = (
    "claude-3", "claude-4", "claude-sonnet", "claude-opus", "claude-haiku",
    "gpt-4o", "gpt-4-turbo", "gemini", "llava", "qwen-vl",
)


def model_supports_thinking(model: str) -> bool:
    """Heuristic: does the named model emit reasoning/thinking deltas?

    Best-effort string match used by adapters that don't have a richer
    capability signal from their SDK. False negatives are fine — the
    badge just won't show.
    """
    low = (model or "").lower()
    return any(h in low for h in _THINKING_HINTS)


def model_supports_vision(model: str) -> bool:
    """Heuristic: does the named model accept image input?"""
    low = (model or "").lower()
    return any(h in low for h in _VISION_HINTS)


def tools_from_objects(objs: Iterable[Any]) -> tuple[ToolDescription, ...]:
    """Best-effort conversion of arbitrary tool objects to ToolDescriptions.

    Adapters wrap a wide variety of tool flavors (LangChain BaseTool,
    OpenAI-Agents-SDK FunctionTool, raw callables). All we need for the
    TUI is ``name`` + a one-line ``description`` — grab whichever attrs
    are present and clamp the description to a single line under 120 chars.
    """
    out: list[ToolDescription] = []
    for t in objs:
        name = getattr(t, "name", None) or getattr(t, "__name__", None) or repr(t)
        desc_raw = (
            getattr(t, "description", None)
            or getattr(t, "__doc__", None)
            or ""
        )
        desc = (desc_raw or "").strip().split("\n", 1)[0][:120]
        out.append(ToolDescription(name=str(name), description=desc))
    return tuple(out)


__all__ = [
    "BaseAdapter",
    "Extractor",
    "merge_usage",
    "model_supports_thinking",
    "model_supports_vision",
    "tools_from_objects",
]
