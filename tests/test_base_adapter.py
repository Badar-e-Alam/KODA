"""Contract tests for ``koda.adapters.base.BaseAdapter``.

Covers the reusable plumbing every subclass inherits:
  * Extractors are called in order, multi-events from one chunk all flow through.
  * Usage events accumulate into the final ``Done``.
  * Exceptions in ``_native_stream`` surface as a ``ToolResult(is_error=True)``
    AND a final ``Done`` (the TUI should never miss the turn terminator).
  * ``interrupt()`` stops the stream mid-flight.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Iterable

import pytest

from koda.adapters.base import BaseAdapter
from koda.agent_api import (
    AgentEvent,
    Done,
    TextDelta,
    ToolResult,
    Usage,
)


class _ScriptedAdapter(BaseAdapter):
    """Test double: replays a fixed list of chunks, optionally raising mid-stream."""

    def __init__(self, chunks: list[Any], raise_at: int | None = None) -> None:
        super().__init__(model="fake:scripted")
        self._chunks = chunks
        self._raise_at = raise_at
        self._extractors = (_echo_text_extractor, _usage_extractor)

    async def _native_stream(self, message: str, history) -> AsyncIterator[Any]:
        for i, c in enumerate(self._chunks):
            if self._raise_at is not None and i == self._raise_at:
                raise RuntimeError("boom")
            await asyncio.sleep(0)  # yield control so interrupt() can race us
            yield c


def _echo_text_extractor(chunk: Any) -> Iterable[AgentEvent] | None:
    if isinstance(chunk, str):
        return (TextDelta(content=chunk),)
    return None


def _usage_extractor(chunk: Any) -> Iterable[AgentEvent] | None:
    if isinstance(chunk, dict) and "usage" in chunk:
        u = chunk["usage"]
        return (Usage(input_tokens=u.get("in", 0), output_tokens=u.get("out", 0)),)
    return None


async def _collect(adapter: BaseAdapter) -> list[AgentEvent]:
    return [ev async for ev in adapter.stream("hi", [])]


@pytest.mark.asyncio
async def test_extractors_run_in_order_and_yield_events():
    adapter = _ScriptedAdapter(["Hello", " ", "world"])
    events = await _collect(adapter)
    texts = [ev.content for ev in events if isinstance(ev, TextDelta)]
    assert texts == ["Hello", " ", "world"]
    assert isinstance(events[-1], Done), "stream must end with Done"


@pytest.mark.asyncio
async def test_usage_accumulates_into_final_done():
    adapter = _ScriptedAdapter([
        "tok",
        {"usage": {"in": 10, "out": 0}},
        {"usage": {"in": 10, "out": 42}},  # final cumulative snapshot
    ])
    events = await _collect(adapter)
    done = events[-1]
    assert isinstance(done, Done)
    assert done.usage is not None
    assert done.usage.input_tokens == 10
    assert done.usage.output_tokens == 42


@pytest.mark.asyncio
async def test_exception_becomes_tool_result_plus_done():
    adapter = _ScriptedAdapter(["ok", "bad"], raise_at=1)
    events = await _collect(adapter)
    errors = [ev for ev in events if isinstance(ev, ToolResult) and ev.is_error]
    assert len(errors) == 1
    assert "RuntimeError" in errors[0].output
    assert "boom" in errors[0].output
    assert isinstance(events[-1], Done), "Done must fire even after an error"


@pytest.mark.asyncio
async def test_interrupt_stops_stream_midflight():
    # Large script; interrupt after the first chunk is emitted.
    adapter = _ScriptedAdapter([f"chunk-{i}" for i in range(50)])

    async def consume():
        events = []
        async for ev in adapter.stream("hi", []):
            events.append(ev)
            if len(events) == 2:
                await adapter.interrupt()
        return events

    events = await consume()
    texts = [ev.content for ev in events if isinstance(ev, TextDelta)]
    assert len(texts) < 50, "interrupt() should have stopped the stream early"
    assert isinstance(events[-1], Done), "Done still fires on interrupt"


@pytest.mark.asyncio
async def test_done_always_last_even_with_no_extractors():
    class _Silent(BaseAdapter):
        _extractors = ()

        async def _native_stream(self, message, history):
            yield "ignored"
            yield "ignored again"

    events = await _collect(_Silent(model="fake:silent"))
    assert len(events) == 1
    assert isinstance(events[0], Done)
    assert events[0].usage is None  # no usage observed → attribute stays None
