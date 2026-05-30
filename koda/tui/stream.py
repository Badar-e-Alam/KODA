"""
Stream pump — routes KodaAgent events into TUI widgets.

One `run_turn()` call per user message. Yields back to Textual's event loop
between events so the UI stays responsive.
"""

from __future__ import annotations

import logging
import time
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
    TodoMessage,
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
    """Stream one user turn. Returns the final assistant text.

    Keeps a ``ThinkingMessage`` pinned at the bottom of the messages
    container for the *entire* turn so the TUI never looks dead. The
    previous behaviour removed the spinner on the first event and left
    nothing visible during slow TTFT on cloud models or between tool
    calls — that's what makes the terminal feel frozen. We now re-pin
    a fresh spinner after every new message widget mounts, threading a
    single ``turn_start`` timestamp through so the elapsed clock stays
    continuous across re-mounts.
    """
    current_assistant: AssistantMessage | None = None
    pending_tools: dict[str, ToolCallMessage] = {}
    # `write_todos` calls are routed to the pinned TodoPanel instead of an
    # inline tool widget. We track their ids so the matching ToolResult is
    # skipped rather than mistaken for an orphaned (error) result below.
    todo_tool_ids: set[str] = set()
    final_text_parts: list[str] = []

    turn_start = time.monotonic()
    thinking: ThinkingMessage | None = ThinkingMessage(start_time=turn_start)
    await app.mount_message(thinking)

    async def _repin_thinking() -> None:
        """Remove the current spinner and mount a fresh one at the bottom.

        Called only when an event materially advances the conversation
        (new tool call, new assistant message, orphaned-error fallback).
        Cheap text-delta updates that just append to the existing
        ``AssistantMessage`` leave the spinner in place — re-mounting on
        every token would flicker without helping.
        """
        nonlocal thinking
        if thinking is not None:
            try:
                await thinking.remove()
            except Exception:
                pass
            thinking = None
        thinking = ThinkingMessage(start_time=turn_start)
        await app.mount_message(thinking)

    try:
        async for ev in adapter.stream(message, history):
            # Take the spinner down ahead of any event that's about to
            # mount a new widget. TextDelta only mounts on the FIRST
            # delta of a streak (when ``current_assistant`` is still
            # None); subsequent deltas just append.
            will_mount_new = isinstance(ev, (ToolStart,)) or (
                isinstance(ev, TextDelta) and current_assistant is None
            )
            if thinking is not None and will_mount_new:
                try:
                    await thinking.remove()
                except Exception:
                    pass
                thinking = None

            await _dispatch(
                app, ev, current_assistant, pending_tools, todo_tool_ids, final_text_parts
            )
            current_assistant = _active_assistant(app, ev, current_assistant)

            # Re-pin the spinner to the bottom after any event that
            # changed the message list. We never re-pin after Done — the
            # finally block tears the spinner down for good there.
            if isinstance(ev, Done):
                continue
            if will_mount_new or (
                isinstance(ev, ToolResult) and ev.tool_id not in pending_tools
            ):
                # ToolResult with no matching pending tool ⇒ orphan path
                # in _dispatch mounted an ErrorMessage; re-pin too.
                await _repin_thinking()
    except Exception as e:
        _log.exception("Stream failed")
        await app.mount_message(ErrorMessage(f"Stream failed: {type(e).__name__}: {e}"))
    finally:
        if thinking is not None:
            try:
                await thinking.remove()
            except Exception:
                pass

    return "".join(final_text_parts)


async def _dispatch(
    app: "KodaApp",
    ev: AgentEvent,
    current_assistant: AssistantMessage | None,
    pending_tools: dict[str, ToolCallMessage],
    todo_tool_ids: set[str],
    final_text_parts: list[str],
) -> None:
    if isinstance(ev, TextDelta):
        # Capture "was the user following the stream?" BEFORE mutating
        # the widget. Once ``append`` grows it, ``max_scroll_y`` has
        # already shifted and we can't tell whether they were pinned to
        # the bottom or scrolled up a few lines reading earlier output.
        follow = _is_following(app)
        if current_assistant is None:
            current_assistant = AssistantMessage()
            await app.mount_message(current_assistant)
            app._last_assistant_widget = current_assistant
        current_assistant.append(ev.content)
        final_text_parts.append(ev.content)
        # Mirror onto the app so the cancel handler in on_input_submitted
        # can recover the partial reply if the user interrupts mid-stream.
        # See KodaApp._partial_reply for the rationale.
        app._partial_reply = "".join(final_text_parts)
        # Only auto-scroll when the user was already at the bottom.
        # Unconditional ``scroll_end`` on every chunk snatches the
        # viewport away from anyone who scrolled up — looks like a freeze
        # because every scroll attempt is undone before they see anything.
        if follow:
            _scroll_to_end(app)

    elif isinstance(ev, ThinkingDelta):
        # For now, reasoning is silent. Future: dedicated ThinkingMessage.
        pass

    elif isinstance(ev, ToolStart):
        # `write_todos` carries a full snapshot of the agent's task list.
        # Render it as an inline checklist in the transcript (like Claude),
        # suppressing the generic tool widget. Update the existing block in
        # place while it's still the last message; otherwise start a new one
        # so progress shows where it happens in the flow.
        if ev.name == "write_todos":
            todo_tool_ids.add(ev.tool_id)
            await _show_todos(app, ev.arguments.get("todos", []))
            return
        widget = ToolCallMessage(ev.tool_id, ev.name, ev.arguments)
        pending_tools[ev.tool_id] = widget
        await app.mount_message(widget)

    elif isinstance(ev, ToolResult):
        # The paired result for a routed write_todos call carries no useful
        # display payload ("Updated todo list to ..."); drop it so it isn't
        # treated as an orphaned error result below.
        if ev.tool_id in todo_tool_ids:
            return
        widget = pending_tools.get(ev.tool_id)
        if widget is not None:
            follow = _is_following(app)
            widget.set_result(ev.output, is_error=ev.is_error)
            if follow:
                _scroll_to_end(app)
        else:
            # Orphan result — the adapter fabricated one for a graph-level
            # failure (connection refused, auth error, ...). Render it as
            # a proper ErrorMessage with a humanized hint so the user sees
            # *what* went wrong and *what to do* instead of a raw trace.
            follow = _is_following(app)
            text = _humanize_adapter_error(ev.output)
            await app.mount_message(ErrorMessage(text))
            if follow:
                _scroll_to_end(app)

    elif isinstance(ev, Usage):
        if app._status_bar is not None:
            app._status_bar.update_usage(ev)

    elif isinstance(ev, Done):
        if ev.usage and app._status_bar is not None:
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


async def _show_todos(app: "KodaApp", todos: list[dict[str, Any]]) -> None:
    """Render the agent's todo snapshot inline in the transcript.

    Updates the active TodoMessage in place while it's still the bottom-most
    message (consecutive write_todos calls collapse into one evolving block);
    otherwise mounts a fresh block so it appears at the current point in the
    conversation flow.
    """
    follow = _is_following(app)
    container = getattr(app, "_messages_container", None)
    active = getattr(app, "_active_todo_widget", None)
    last = container.children[-1] if (container and container.children) else None

    if active is not None and active is last:
        active.set_todos(todos)
    else:
        widget = TodoMessage(todos)
        app._active_todo_widget = widget
        await app.mount_message(widget)

    if follow:
        _scroll_to_end(app)


def _scroll_to_end(app: "KodaApp") -> None:
    """Pin #messages to its bottom so streamed growth stays visible."""
    container = getattr(app, "_messages_container", None)
    if container is not None:
        try:
            container.scroll_end(animate=False)
        except Exception:
            pass


def _is_following(app: "KodaApp") -> bool:
    """True when the user is pinned at (or within a couple of lines of)
    the bottom of the messages container.

    "Following the stream" means we should keep auto-scrolling as new
    content arrives. As soon as the user scrolls up to read earlier
    output we stop snapping the viewport back — otherwise every text
    delta cancels their scroll and the TUI feels frozen.

    The 2-line tolerance covers a one-frame gap between a previous chunk
    appending content (which bumps ``max_scroll_y``) and the next chunk
    arriving before the autoscroll has caught up. Without it, fast
    streams can flip the follow state to False between deltas even when
    the user hasn't touched anything.
    """
    container = getattr(app, "_messages_container", None)
    if container is None:
        return True
    try:
        return container.scroll_y >= container.max_scroll_y - 2
    except Exception:
        return True


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
