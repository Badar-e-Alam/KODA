"""
Regression tests for the bug where a whole user turn disappeared from the
TUI even though the conversation log captured it.

Guarantees:
  * User query renders (full text).
  * Tool call renders with a truncated preview (<= PREVIEW_CHARS).
  * Assistant response renders with its full text, not truncated.
  * #messages has a positive rendered height after a turn (so the layout
    cannot collapse the chat to zero rows).
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from koda.agent_api import (
    AgentEvent,
    Done,
    KodaAgent,
    TextDelta,
    ToolResult,
    ToolStart,
)
from koda.tui.app import KodaApp
from koda.tui.widgets.messages import (
    AssistantMessage,
    ToolCallMessage,
    UserMessage,
)


class _FakeAgent(KodaAgent):
    """Canned-event stand-in so the TUI doesn't hit a real LLM."""

    def __init__(self, events: list[AgentEvent]) -> None:
        self._events = events

    def model_name(self) -> str:
        return "fake:test"

    async def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]:
        for ev in self._events:
            yield ev
        yield Done()

    async def interrupt(self) -> None:
        return None


_ASSISTANT_FULL = (
    "Here is a deliberately long answer so we can confirm the assistant "
    "message renders in full. It spans well over 80 characters to make "
    "sure no preview truncation fires on assistant content."
)
_TOOL_OUTPUT = "line1-this-output-is-longer-than-eighty-characters-so-the-preview-clamps\nline2\nline3"


def _scripted_events() -> list[AgentEvent]:
    return [
        ToolStart(tool_id="t1", name="ls", arguments={"path": "/"}),
        ToolResult(tool_id="t1", output=_TOOL_OUTPUT),
        TextDelta(content=_ASSISTANT_FULL),
    ]


@pytest.mark.asyncio
async def test_user_tool_and_assistant_all_visible():
    agent = _FakeAgent(_scripted_events())
    async with KodaApp(adapter=agent, model="fake:test").run_test(size=(100, 24)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        user_query = "run ls and explain"
        await app._handle_user_message(user_query)
        await pilot.pause()

        # User bubble — full text rendered
        user_widgets = list(app.query(UserMessage))
        assert len(user_widgets) == 1
        assert user_query in str(user_widgets[0].render())

        # Tool widget — preview is clamped to ToolCallMessage.PREVIEW_CHARS
        tool_widgets = list(app.query(ToolCallMessage))
        assert len(tool_widgets) == 1
        tool_render = str(tool_widgets[0].render())
        assert "ls" in tool_render
        # First line only, clamped, with the "+N lines" summary
        assert "+2 lines" in tool_render
        # The clamp is 80 chars for the output portion; the header adds a bit
        # more, so we just assert the full multi-line output isn't present.
        assert "line3" not in tool_render
        assert "line2" not in tool_render

        # Assistant bubble — FULL text, not truncated
        assistant_widgets = list(app.query(AssistantMessage))
        assert len(assistant_widgets) == 1
        assert str(assistant_widgets[0].render()).strip() == _ASSISTANT_FULL


@pytest.mark.asyncio
async def test_messages_container_has_non_zero_height_after_turn():
    """Guards the regression: small terminals were squeezing #messages to 0."""
    agent = _FakeAgent([TextDelta(content="ok")])
    async with KodaApp(adapter=agent, model="fake:test").run_test(size=(80, 20)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        await app._handle_user_message("hi")
        await pilot.pause()

        container = app._messages_container
        assert container is not None
        assert container.region.height >= 3, (
            f"#messages collapsed to {container.region.height} rows — "
            "banner collapse + min-height guards failed"
        )


@pytest.mark.asyncio
async def test_banner_collapses_on_first_user_message():
    agent = _FakeAgent([TextDelta(content="ok")])
    async with KodaApp(adapter=agent, model="fake:test").run_test(size=(100, 24)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        assert app._banner is not None
        assert "-compact" not in app._banner.classes  # tall at launch

        await app._handle_user_message("hello")
        await pilot.pause()

        assert "-compact" in app._banner.classes  # collapsed after first turn
