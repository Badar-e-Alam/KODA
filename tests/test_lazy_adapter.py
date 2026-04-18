"""
Tests for lazy adapter bootstrap.

Before the fix, KODA called the heavy ``factory(model, thread_id)`` on the
main thread before the TUI mounted, so users stared at a blank screen for
5-8 s while langgraph/langchain imports and graph compilation ran. The
fix builds the adapter in a worker thread during ``on_mount`` so the UI
shows up immediately.

Contract:
  - KodaApp(adapter=None, adapter_factory=f) is legal.
  - After mount the factory runs in a thread (not the UI thread).
  - app._adapter eventually becomes the factory's return value.
  - A "Loading agent…" AppMessage is shown and later replaced by
    "Agent ready".
  - Sending a message while loading gives a helpful status (no crash).
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from koda.agent_api import KodaAgent
from koda.tui.app import KodaApp
from koda.tui.widgets import AppMessage, UserMessage


class _SlowFakeAdapter(KodaAgent):
    """Fake adapter whose factory sleeps to simulate a slow build."""

    def __init__(self, model: str, thread_id: str, *, build_thread: int) -> None:
        self._model = model
        self._thread_id = thread_id
        self.build_thread = build_thread

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        pass

    async def stream(self, message, history):  # pragma: no cover
        if False:
            yield None


@pytest.mark.asyncio
async def test_adapter_factory_runs_off_ui_thread():
    ui_thread = threading.get_ident()
    factory_thread: dict[str, int] = {}

    def factory(model: str, thread_id: str) -> KodaAgent:
        factory_thread["tid"] = threading.get_ident()
        time.sleep(0.2)  # simulate heavy build
        return _SlowFakeAdapter(model, thread_id, build_thread=threading.get_ident())

    app = KodaApp(
        adapter=None,
        adapter_factory=factory,
        model="openai:test",
        thread_id="tid",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # The factory must NOT have blocked mount — TUI came up quickly.
        # Wait for the background build to finish
        for _ in range(40):
            await asyncio.sleep(0.05)
            await pilot.pause()
            if app._adapter is not None:
                break

    assert app._adapter is not None, "adapter was never built"
    assert "tid" in factory_thread
    assert factory_thread["tid"] != ui_thread, (
        "factory must run on a worker thread, not the UI thread"
    )


@pytest.mark.asyncio
async def test_loading_message_appears_and_is_replaced():
    def factory(model: str, thread_id: str) -> KodaAgent:
        time.sleep(0.15)
        return _SlowFakeAdapter(model, thread_id, build_thread=0)

    app = KodaApp(
        adapter=None,
        adapter_factory=factory,
        model="openai:slow",
        thread_id="tid",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # Immediately after mount, a "Loading agent…" notice should exist
        notices = [str(m._content) for m in app.query(AppMessage)]
        assert any("Loading" in n for n in notices), notices

        # Wait for build to complete
        for _ in range(40):
            await asyncio.sleep(0.05)
            await pilot.pause()
            if app._adapter is not None:
                break
        # "Agent ready" notice mounted
        notices = [str(m._content) for m in app.query(AppMessage)]
        assert any("Agent ready" in n for n in notices), notices


@pytest.mark.asyncio
async def test_send_while_loading_shows_friendly_notice():
    building = threading.Event()
    released = threading.Event()

    def factory(model: str, thread_id: str) -> KodaAgent:
        building.set()
        released.wait(timeout=5)
        return _SlowFakeAdapter(model, thread_id, build_thread=0)

    app = KodaApp(
        adapter=None,
        adapter_factory=factory,
        model="openai:slow",
        thread_id="tid",
    )
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            # Wait until the factory thread has started
            for _ in range(40):
                if building.is_set():
                    break
                await asyncio.sleep(0.05)
                await pilot.pause()
            assert building.is_set(), "factory never started"
            # Adapter still None, send a message
            await app._handle_user_message("hi there")
            await pilot.pause()
            notices = [str(m._content) for m in app.query(AppMessage)]
            assert any("still loading" in n.lower() for n in notices), notices
            # Original message still shows as a UserMessage
            um = [c._content for c in app._messages_container.children
                  if isinstance(c, UserMessage)]
            assert "hi there" in um
    finally:
        released.set()
