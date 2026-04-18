"""
Tests for LangGraphAdapter's history-forwarding contract.

The adapter must NOT re-send prior messages on every turn — LangGraph's
checkpointer + ``thread_id`` already replays them, and the default
``add_messages`` reducer would just append duplicates (our plain
role/content dicts have no stable IDs).

Contract:
  - Turn 1 with empty history: graph sees only the new user message.
  - Turn 1 with seeded history (e.g. resumed session): graph sees the
    seeded history + the new user message — once.
  - Turn 2 onwards: graph sees only the new user message, regardless of
    what the caller passes as ``history``.
  - thread_id is forwarded in ``config.configurable`` so the checkpointer
    picks the right thread.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from koda.adapters.langgraph import LangGraphAdapter


class _FakeGraph:
    """Records every ``astream_events`` invocation for inspection."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def astream_events(
        self, inputs, *, config, version
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append({"inputs": inputs, "config": config, "version": version})
        # Empty event stream; adapter still needs to yield through the loop.
        if False:
            yield {}
        return


async def _drain(adapter, message, history):
    async for _ in adapter._native_stream(message, history):
        pass


@pytest.mark.asyncio
async def test_turn1_empty_history_sends_only_new_message():
    g = _FakeGraph()
    a = LangGraphAdapter(graph=g, model="x", thread_id="t")
    await _drain(a, "hello", [])
    assert len(g.calls) == 1
    msgs = g.calls[0]["inputs"]["messages"]
    assert msgs == [{"role": "user", "content": "hello"}]
    assert g.calls[0]["config"]["configurable"]["thread_id"] == "t"


@pytest.mark.asyncio
async def test_turn1_with_seeded_history_sends_seed_plus_new():
    """When a session is resumed from disk, checkpointer state is empty but
    our UI history isn't. Seed exactly once so the graph has context.
    """
    g = _FakeGraph()
    a = LangGraphAdapter(graph=g, model="x", thread_id="t")
    seed = [
        {"role": "user", "content": "previous user"},
        {"role": "assistant", "content": "previous assistant"},
    ]
    await _drain(a, "new question", seed)
    msgs = g.calls[0]["inputs"]["messages"]
    assert msgs == seed + [{"role": "user", "content": "new question"}]


@pytest.mark.asyncio
async def test_turn2_ignores_history_even_if_caller_resends_it():
    """Regression guard: even if the caller keeps passing a growing history
    (which KodaApp currently does), the graph must only see the new message
    after the first turn — otherwise every prior user/assistant message
    gets duplicated in the checkpoint.
    """
    g = _FakeGraph()
    a = LangGraphAdapter(graph=g, model="x", thread_id="t")

    # Turn 1
    await _drain(a, "hi", [])
    # Turn 2 — caller naively re-passes full history
    history_after_turn1 = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello back"},
    ]
    await _drain(a, "follow-up", history_after_turn1)
    # Turn 3
    history_after_turn2 = history_after_turn1 + [
        {"role": "user", "content": "follow-up"},
        {"role": "assistant", "content": "sure"},
    ]
    await _drain(a, "third", history_after_turn2)

    assert len(g.calls) == 3
    # Turn 1 → just the new message
    assert g.calls[0]["inputs"]["messages"] == [
        {"role": "user", "content": "hi"}
    ]
    # Turn 2 → just the new message (no seed — _seeded was already set)
    assert g.calls[1]["inputs"]["messages"] == [
        {"role": "user", "content": "follow-up"}
    ]
    # Turn 3 → same
    assert g.calls[2]["inputs"]["messages"] == [
        {"role": "user", "content": "third"}
    ]


@pytest.mark.asyncio
async def test_seed_only_fires_once_even_with_history_on_every_turn():
    """If a resumed session passes seed on both turn 1 and turn 2, the
    adapter must still forward seed only once.
    """
    g = _FakeGraph()
    a = LangGraphAdapter(graph=g, model="x", thread_id="t")
    seed = [{"role": "user", "content": "earlier"}]
    await _drain(a, "first", seed)
    await _drain(a, "second", seed + [{"role": "assistant", "content": "ok"}])

    # Turn 1: seed + first
    assert g.calls[0]["inputs"]["messages"] == seed + [
        {"role": "user", "content": "first"}
    ]
    # Turn 2: only the new message — NOT seed+history again
    assert g.calls[1]["inputs"]["messages"] == [
        {"role": "user", "content": "second"}
    ]


@pytest.mark.asyncio
async def test_thread_id_stable_across_turns():
    g = _FakeGraph()
    a = LangGraphAdapter(graph=g, model="x", thread_id="stable-tid")
    await _drain(a, "a", [])
    await _drain(a, "b", [])
    tids = {c["config"]["configurable"]["thread_id"] for c in g.calls}
    assert tids == {"stable-tid"}
