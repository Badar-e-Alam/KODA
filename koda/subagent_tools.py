"""
Agent-facing tools for background (async) subagents.

Implements the SAME five-tool surface as deepagents' official async-subagents
middleware (https://docs.langchain.com/oss/python/deepagents/async-subagents):

    start_async_task(description, subagent_type) -> task_id immediately
    check_async_task(task_id)                    -> status + result when done
    update_async_task(task_id, message)          -> follow-up on the same thread
    cancel_async_task(task_id)                   -> stop a running task
    list_async_tasks(status_filter)              -> all tracked tasks + statuses

The official middleware launches runs on a remote Agent Protocol server
(LangGraph Platform / self-hosted). KODA is a single local process, so these
tools drive the in-process ``BackgroundTaskRegistry`` (``koda/subagent_tasks.py``)
instead — same names, same schemas, same status vocabulary (``running`` /
``success`` / ``error`` / ``cancelled``), same workflow contract, but the
"server" is the bridge's own event loop. That also buys two things the remote
transport can't do locally: live task status streamed to the UI, and permission
prompts for gated subagent tools.

If real remote async subagents are configured (``.koda/async_subagents.json``),
``coding_agent/agent.py`` passes them straight to ``create_deep_agent`` and the
genuine ``AsyncSubAgentMiddleware`` provides these tool names instead — these
in-process variants are then removed to avoid collisions.

When no registry is bound (Textual TUI, headless one-shot mode), the tools
degrade to a clear "unavailable" message instead of failing. The registry lives
on the asyncio loop; these ``@tool`` functions run in a worker thread under
LangGraph, so they hop onto the loop with ``run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import tool

_REGISTRY: Any = None
_LOOP: asyncio.AbstractEventLoop | None = None

VALID_TYPES = ("explore", "plan", "edit", "general-purpose")

# Wire statuses follow the official middleware; internal "queued"/"paused"
# states surface as "running" (a paused task is alive, just awaiting the
# user's permission decision in the UI).
_STATUS_MAP = {"queued": "running", "paused": "running"}


def bind(registry: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Wire the tools to a live registry + its loop (called by the bridge)."""
    global _REGISTRY, _LOOP
    _REGISTRY = registry
    _LOOP = loop


def unbind() -> None:
    global _REGISTRY, _LOOP
    _REGISTRY = None
    _LOOP = None


def _on_loop(fn, *args, **kwargs):
    """Run ``fn(*args)`` on the registry's loop thread and return its result."""
    if _REGISTRY is None or _LOOP is None:
        raise RuntimeError("unavailable")
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _LOOP:
        return fn(*args, **kwargs)

    async def _call():
        return fn(*args, **kwargs)

    return asyncio.run_coroutine_threadsafe(_call(), _LOOP).result(timeout=15)


_UNAVAILABLE = (
    "[async subagent tasks unavailable in this session — they need the inline "
    "UI (plain `koda`). In the classic Textual TUI or one-shot mode, use the "
    "blocking `task` tool instead.]"
)


def _wire_status(state: str) -> str:
    return _STATUS_MAP.get(state, state)


@tool
def start_async_task(description: str, subagent_type: str = "general-purpose") -> str:
    """Start an async subagent in the background. Returns a task ID immediately.

    The subagent runs on its own thread with its own context window and its own
    persistent memory; you do NOT wait for it. After launching, report the
    task_id to the user and stop — do NOT immediately check status. Multiple
    async subagents can run concurrently: launch several for independent work.

    Args:
        description: A detailed, self-contained brief of the task for the async
            subagent to perform — goal, relevant context, and the shape of
            answer you want.
        subagent_type: One of ``explore`` (read-only orientation), ``plan``
            (read-only implementation plan), ``edit`` (make + verify a change),
            or ``general-purpose`` (default).

    Returns:
        ``Launched async subagent. task_id: <id>`` on success.
    """
    if _REGISTRY is None:
        return _UNAVAILABLE
    st = subagent_type if subagent_type in VALID_TYPES else "general-purpose"
    tid = _on_loop(_REGISTRY.spawn, description, st)
    return f"Launched async subagent. task_id: {tid}"


@tool
def check_async_task(task_id: str) -> str:
    """Check the status of an async subagent task. Returns status and, if
    complete, the result.

    Use only when the user asks for a status update or result, or after a
    task-finished notification. If the status is "running", report that and
    stop — do not poll in a loop.

    Args:
        task_id: The exact task_id string returned by start_async_task.

    Returns:
        JSON with ``status`` (running/success/error/cancelled), ``task_id``,
        and ``result`` (on success) or ``error`` (on failure).
    """
    if _REGISTRY is None:
        return _UNAVAILABLE
    task = _on_loop(_REGISTRY.get, task_id.strip())
    if task is None:
        return f"No tracked task found for task_id: {task_id!r}"
    s = task.summary
    status = _wire_status(s.state)
    out: dict[str, Any] = {"status": status, "task_id": s.id}
    if s.state == "success":
        out["result"] = task.final_text or "(completed with no output messages)"
    elif s.state == "error":
        out["error"] = s.error or "The async subagent encountered an error."
    elif s.state == "paused":
        out["note"] = "awaiting the user's permission decision in the UI"
    elif s.state == "cancelled" and task.final_text:
        out["partial_result"] = task.final_text
    return json.dumps(out)


@tool
def update_async_task(task_id: str, message: str) -> str:
    """Send updated instructions to an async subagent. Interrupts the current
    run and starts a new one on the same thread, so the subagent sees the full
    conversation history plus your new message. The task_id remains the same.

    Args:
        task_id: The exact task_id string returned by start_async_task.
        message: Follow-up instructions or context to send to the subagent.

    Returns:
        Confirmation, or an error if the task is unknown.
    """
    if _REGISTRY is None:
        return _UNAVAILABLE
    ok = _on_loop(_REGISTRY.resume, task_id.strip(), message)
    if not ok:
        return f"No tracked task found for task_id: {task_id!r}"
    return f"Updated async subagent. task_id: {task_id.strip()}"


@tool
def cancel_async_task(task_id: str) -> str:
    """Cancel a running async subagent task. Use this to stop a task that is no
    longer needed. Its memory is kept, so update_async_task can revive it.

    Args:
        task_id: The exact task_id string returned by start_async_task.

    Returns:
        Confirmation, or an error if the task isn't running.
    """
    if _REGISTRY is None:
        return _UNAVAILABLE
    ok = _on_loop(_REGISTRY.stop, task_id.strip())
    if not ok:
        return f"Could not cancel {task_id!r} (unknown task, or not running)."
    return f"Cancelled async subagent task: {task_id.strip()}"


@tool
def list_async_tasks(status_filter: str = "all") -> str:
    """List tracked async subagent tasks with their current live statuses.

    Use this to see all tasks at once, or to recall task IDs after context
    compaction. Task statuses in your conversation history are ALWAYS stale —
    call this (or check_async_task) rather than reporting an old status.

    Args:
        status_filter: One of ``running``, ``success``, ``error``,
            ``cancelled``, or ``all`` (default).

    Returns:
        One line per task: task_id, agent type, live status.
    """
    if _REGISTRY is None:
        return _UNAVAILABLE
    summaries = _on_loop(_REGISTRY.list)
    rows = []
    for s in summaries:
        status = _wire_status(s.state)
        if status_filter not in ("", "all") and status != status_filter:
            continue
        rows.append(
            f"- task_id: {s.id}  agent: {s.subagent_type}  status: {status}"
            f"  ({s.tool_count} tools, {s.current})"
        )
    if not rows:
        return "No async subagent tasks tracked."
    return f"{len(rows)} tracked task(s):\n" + "\n".join(rows)


SUBAGENT_TASK_TOOLS = [
    start_async_task,
    check_async_task,
    update_async_task,
    cancel_async_task,
    list_async_tasks,
]

# Tool names owned by this surface — used by coding_agent/agent.py to strip the
# in-process variants when real remote AsyncSubAgent specs are configured.
ASYNC_TASK_TOOL_NAMES = {t.name for t in SUBAGENT_TASK_TOOLS}
