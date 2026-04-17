"""
Stream pump — routes KodaAgent events into TUI widgets.

One `run_turn()` call per user message. Yields back to Textual's event loop
between events so the UI stays responsive.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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
from koda.tui.widgets.messages import (
    AssistantMessage,
    ErrorMessage,
    ToolCallMessage,
)

if TYPE_CHECKING:
    from koda.tui.app import KodaApp

_log = logging.getLogger("koda.tui.stream")


async def run_turn(
    app: "KodaApp",
    adapter: KodaAgent,
    message: str,
    history: list[dict[str, Any]],
) -> str:
    """Stream one user turn. Returns the final assistant text."""
    current_assistant: AssistantMessage | None = None
    pending_tools: dict[str, ToolCallMessage] = {}
    final_text_parts: list[str] = []

    try:
        async for ev in adapter.stream(message, history):
            await _dispatch(app, ev, current_assistant, pending_tools, final_text_parts)
            # Update our local reference (Python closure mutation workaround)
            current_assistant = _active_assistant(app, ev, current_assistant)
    except Exception as e:
        _log.exception("Stream failed")
        await app.mount_message(ErrorMessage(f"Stream failed: {type(e).__name__}: {e}"))

    return "".join(final_text_parts)


async def _dispatch(
    app: "KodaApp",
    ev: AgentEvent,
    current_assistant: AssistantMessage | None,
    pending_tools: dict[str, ToolCallMessage],
    final_text_parts: list[str],
) -> None:
    if isinstance(ev, TextDelta):
        if current_assistant is None:
            current_assistant = AssistantMessage()
            await app.mount_message(current_assistant)
            app._last_assistant_widget = current_assistant
        current_assistant.append(ev.content)
        final_text_parts.append(ev.content)

    elif isinstance(ev, ThinkingDelta):
        # For now, reasoning is silent. Future: dedicated ThinkingMessage.
        pass

    elif isinstance(ev, ToolStart):
        widget = ToolCallMessage(ev.tool_id, ev.name, ev.arguments)
        pending_tools[ev.tool_id] = widget
        await app.mount_message(widget)

    elif isinstance(ev, ToolResult):
        widget = pending_tools.get(ev.tool_id)
        if widget is not None:
            widget.set_result(ev.output, is_error=ev.is_error)
        else:
            # Orphan result (e.g. adapter error fabricated one)
            from koda.tui.widgets.messages import AppMessage
            await app.mount_message(AppMessage(f"{ev.output[:200]}"))

    elif isinstance(ev, Usage):
        if app._status_bar is not None:
            app._status_bar.update_usage(ev)

    elif isinstance(ev, Done):
        if ev.usage and app._status_bar is not None:
            app._status_bar.update_usage(ev.usage)


def _active_assistant(
    app: "KodaApp",
    ev: AgentEvent,
    current: AssistantMessage | None,
) -> AssistantMessage | None:
    """Break the active assistant streak when a tool call starts."""
    if isinstance(ev, ToolStart):
        return None
    if isinstance(ev, TextDelta) and current is None:
        return app._last_assistant_widget
    return current
