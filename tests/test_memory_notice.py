"""
Tests for the memory-update notice and /reload-memory command.

Contract:
  - When the agent calls ``edit_file`` (or ``write_file``) on AGENTS.md, a
    muted "Memory updated — active next session (or /reload-memory)"
    AppMessage is mounted right after the tool call.
  - Non-memory edits do NOT trigger the notice.
  - /reload-memory calls the adapter factory again (same model + thread_id)
    and mounts an "Memory reloaded" confirmation.
"""

from __future__ import annotations

import asyncio

import pytest

from koda.agent_api import KodaAgent
from koda.tui.app import KodaApp
from koda.tui.commands import dispatch
from koda.tui.widgets import AppMessage
from koda.tui.widgets.messages import ToolCallMessage


class _StubAdapter(KodaAgent):
    def __init__(self, model: str, thread_id: str) -> None:
        self._model = model
        self._thread_id = thread_id

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        pass

    async def stream(self, message, history):  # pragma: no cover
        if False:
            yield None


@pytest.mark.asyncio
async def test_edit_on_agents_md_triggers_notice():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        tc = ToolCallMessage(
            tool_id="t1",
            name="edit_file",
            arguments={"file_path": "/AGENTS.md", "old_string": "a", "new_string": "b"},
        )
        await app.mount_message(tc)
        await pilot.pause()

        notices = [m._content for m in app.query(AppMessage)
                   if "Memory updated" in m._content]
        assert notices, "expected a memory-update notice after editing /AGENTS.md"


@pytest.mark.asyncio
async def test_write_on_agents_md_also_triggers_notice():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        tc = ToolCallMessage(
            tool_id="t2",
            name="write_file",
            arguments={"file_path": "AGENTS.md", "content": "# notes"},
        )
        await app.mount_message(tc)
        await pilot.pause()

        notices = [m._content for m in app.query(AppMessage)
                   if "Memory updated" in m._content]
        assert notices


@pytest.mark.asyncio
async def test_edit_on_non_memory_file_does_not_trigger_notice():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        tc = ToolCallMessage(
            tool_id="t3",
            name="edit_file",
            arguments={"file_path": "/src/foo.py", "old_string": "a", "new_string": "b"},
        )
        await app.mount_message(tc)
        await pilot.pause()

        notices = [m._content for m in app.query(AppMessage)
                   if "Memory updated" in m._content]
        assert not notices


@pytest.mark.asyncio
async def test_reload_memory_runs_factory_and_confirms():
    calls: list[tuple[str, str]] = []

    def factory(model: str, thread_id: str) -> KodaAgent:
        calls.append((model, thread_id))
        return _StubAdapter(model, thread_id)

    app = KodaApp(
        adapter=factory("openai:x", "tid"),
        adapter_factory=factory,
        model="openai:x",
        thread_id="tid",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        calls.clear()
        handled = await dispatch(app, "/reload-memory")
        await pilot.pause()
        notices = [m._content for m in app.query(AppMessage)]

    assert handled
    # Exactly one rebuild at the same model + thread_id
    assert calls == [("openai:x", "tid")], calls
    # Confirmation + progress notices should exist
    assert any("Reloading" in n for n in notices), notices
    assert any("Memory reloaded" in n for n in notices), notices


@pytest.mark.asyncio
async def test_reload_memory_factory_error_shows_error():
    def factory(model: str, thread_id: str) -> KodaAgent:
        if len(factory.calls) > 0:
            raise RuntimeError("boom")
        return _StubAdapter(model, thread_id)
    factory.calls = []

    # Wrap to track first-call success, second-call failure
    real = factory
    def counting(model, thread_id):
        counting.calls.append((model, thread_id))
        if len(counting.calls) == 1:
            return _StubAdapter(model, thread_id)
        raise RuntimeError("boom")
    counting.calls = []

    app = KodaApp(
        adapter=counting("openai:x", "tid"),
        adapter_factory=counting,
        model="openai:x",
        thread_id="tid",
    )
    from koda.tui.widgets import ErrorMessage

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.reload_memory()
        await pilot.pause()
        errs = [str(m._content) for m in app.query(ErrorMessage)]
        assert any("boom" in e for e in errs), errs


@pytest.mark.asyncio
async def test_reload_memory_is_in_help_registry():
    """Slash-command completer must expose /reload-memory."""
    from koda.tui.commands import _HELP
    assert "reload-memory" in _HELP
