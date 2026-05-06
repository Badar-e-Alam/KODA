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
    ThinkingMessage,
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

    thinking: ThinkingMessage | None = ThinkingMessage()
    await app.mount_message(thinking)

    try:
        async for ev in adapter.stream(message, history):
            # First real event → remove the thinking placeholder
            if thinking is not None and isinstance(
                ev, (TextDelta, ThinkingDelta, ToolStart, ToolResult)
            ):
                try:
                    await thinking.remove()
                except Exception:
                    pass
                thinking = None
            await _dispatch(app, ev, current_assistant, pending_tools, final_text_parts)
            current_assistant = _active_assistant(app, ev, current_assistant)
    except Exception as e:
        _log.exception("Stream failed")
        await app.mount_message(ErrorMessage(f"Stream failed: {type(e).__name__}: {e}"))
    finally:
        if thinking is not None:
            try:
                await thinking.remove()
            except Exception:
                pass
        # Drain any deltas still in the buffer of the most recent assistant
        # widget so the final word(s) of the response don't sit in-buffer
        # until the next mount_message triggers a refresh.
        last = getattr(app, "_last_assistant_widget", None)
        if last is not None:
            try:
                last.finalize()
            except Exception:
                pass

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
            # Keep the viewport pinned each time the buffered widget flushes
            # (~30 Hz) instead of every TextDelta. mount_message scrolls once
            # at mount; from then on the widget itself drives scroll-pinning
            # at the same cadence as its repaints.
            current_assistant.set_on_flush(lambda: _scroll_to_end(app))
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
            _scroll_to_end(app)
        else:
            # Orphan result — the adapter fabricated one for a graph-level
            # failure (connection refused, auth error, ...). Render it as
            # a proper ErrorMessage with a humanized hint so the user sees
            # *what* went wrong and *what to do* instead of a raw trace.
            text = _humanize_adapter_error(ev.output)
            await app.mount_message(ErrorMessage(text))
            _scroll_to_end(app)

    elif isinstance(ev, Usage):
        if app._status_bar is not None:
            # Mid-stream Usage events are coalesced to ~1 Hz so the
            # status bar doesn't repaint on every token.
            app._status_bar.update_usage_throttled(ev)

    elif isinstance(ev, Done):
        if ev.usage and app._status_bar is not None:
            # Final flush — apply any pending mid-stream delta plus the
            # ``Done`` totals in one synchronous repaint.
            app._status_bar.update_usage(ev.usage)


_HINTS: list[tuple[str, str]] = [
    # lowercase match → actionable message
    ("connecterror", "Model server unreachable. If using ollama, run `ollama serve`."),
    ("connection refused", "Model server refused the connection. Is it running?"),
    ("all connection attempts failed", "Could not reach the model server. Check it's running and the host/port."),
    ("401", "Authentication failed. Check your API key."),
    ("unauthorized", "Authentication failed. Check your API key."),
    ("403", "Access denied. The API key may lack permission for this model."),
    ("429", "Rate-limited by the provider. Wait a moment and retry."),
    ("timeout", "Request timed out. The model server may be slow or overloaded."),
]


def _humanize_adapter_error(raw: str) -> str:
    """Turn a raw 'Agent error: ...' string into something actionable."""
    low = raw.lower()
    for needle, hint in _HINTS:
        if needle in low:
            # Keep the short error tag but replace the noise with the hint
            head = raw.split(":", 2)[-1].strip()[:120]
            return f"{hint}  ({head})"
    return raw[:240]


def _scroll_to_end(app: "KodaApp") -> None:
    """Pin #messages to its bottom so streamed growth stays visible."""
    container = getattr(app, "_messages_container", None)
    if container is not None:
        try:
            container.scroll_end(animate=False)
        except Exception:
            pass


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
