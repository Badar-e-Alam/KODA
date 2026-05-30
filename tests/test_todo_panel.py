"""Tests for the inline agent-driven todo checklist and its stream routing."""

from __future__ import annotations

import pytest

from koda.agent_api import ToolResult, ToolStart
from koda.tui import stream
from koda.tui.app import KodaApp
from koda.tui.widgets.messages import AppMessage, TodoMessage, ToolCallMessage


def test_todo_message_renders_header_and_items() -> None:
    msg = TodoMessage(
        [
            {"content": "Read the code", "status": "completed"},
            {"content": "Write the panel", "status": "in_progress"},
            {"content": "Add tests", "status": "pending"},
        ]
    )
    rendered = str(msg.renderable)
    assert "Tasks" in rendered and "(1/3)" in rendered
    assert "Read the code" in rendered
    assert "Write the panel" in rendered
    assert "Add tests" in rendered


def test_todo_message_ignores_malformed_items() -> None:
    msg = TodoMessage([{"content": "ok", "status": "pending"}, "garbage", None])  # type: ignore[list-item]
    assert len(msg._todos) == 1


async def _dispatch_todos(app: KodaApp, tool_id: str, todos: list, todo_ids: set[str]) -> None:
    await stream._dispatch(
        app,
        ToolStart(tool_id=tool_id, name="write_todos", arguments={"todos": todos}),
        None,
        {},
        todo_ids,
        [],
    )


@pytest.mark.asyncio
async def test_write_todos_renders_inline_and_updates_in_place() -> None:
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        container = app._messages_container
        todo_ids: set[str] = set()

        # First snapshot → a new inline TodoMessage in the transcript.
        await _dispatch_todos(app, "t1", [{"content": "Step 1", "status": "in_progress"}], todo_ids)
        await pilot.pause()
        todos = container.query(TodoMessage)
        assert len(todos) == 1
        assert "Step 1" in str(todos.first().renderable)
        assert not container.query(ToolCallMessage)  # no generic tool line

        # Consecutive update (still the last message) → same block, updated.
        await _dispatch_todos(
            app, "t2", [{"content": "Step 1", "status": "completed"}], todo_ids
        )
        await pilot.pause()
        assert len(container.query(TodoMessage)) == 1  # updated in place
        assert "(1/1)" in str(container.query(TodoMessage).first().renderable)

        # Something else lands in between → next snapshot starts a fresh block.
        await app.mount_message(AppMessage("interruption"))
        await pilot.pause()
        await _dispatch_todos(app, "t3", [{"content": "Step 2", "status": "pending"}], todo_ids)
        await pilot.pause()
        assert len(container.query(TodoMessage)) == 2


@pytest.mark.asyncio
async def test_write_todos_result_is_swallowed() -> None:
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        container = app._messages_container
        todo_ids: set[str] = set()

        await _dispatch_todos(app, "t1", [{"content": "Step 1", "status": "pending"}], todo_ids)
        await pilot.pause()
        before = len(container.children)

        # The paired result must not render an orphan ErrorMessage.
        await stream._dispatch(
            app,
            ToolResult(tool_id="t1", output="Updated todo list to [...]"),
            None,
            {},
            todo_ids,
            [],
        )
        await pilot.pause()
        assert len(container.children) == before


@pytest.mark.asyncio
async def test_clear_session_resets_todo_state() -> None:
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        todo_ids: set[str] = set()
        await _dispatch_todos(app, "t1", [{"content": "Step 1", "status": "pending"}], todo_ids)
        await pilot.pause()
        assert app._active_todo_widget is not None

        await app.action_clear_session()
        await pilot.pause()
        assert app._active_todo_widget is None
        assert not app._messages_container.query(TodoMessage)
