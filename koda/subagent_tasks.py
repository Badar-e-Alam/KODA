"""
Background subagent tasks — the "execution-control object".

A subagent normally runs *inside* the main agent's turn (blocking). This module
makes it a first-class, controllable record instead: launch it, keep working,
then check on it / stop / resume / restart it — like a job in a shell.

The trick is simple and reuses everything KODA already has:

  * Each task is an ordinary ``CodingAgentAdapter`` with its **own thread_id**.
    LangGraph's checkpointer persists that thread after every super-step, so the
    task's whole context survives a cancel — which is exactly what makes
    **stop / resume / restart** free:

        stop     → cancel the asyncio task; the checkpoint stays put.
        resume   → stream the same thread again with a new message; it picks up
                   from the checkpoint (memory intact).
        restart  → run the original brief again on a fresh thread (clean slate).

  * The task runs on the bridge's event loop as a detached ``asyncio.Task``, so
    the main agent is free while it works. Its ``AgentEvent`` stream is folded
    into a compact live ``summary`` (state, current tool, counts) instead of the
    main transcript, and an ``on_update`` callback lets the bridge surface that
    to the UI and post a notification when a task finishes.

This mirrors the background-shell registry in ``coding_agent/tools.py`` — same
launch / poll / stop shape, but the "process" is a LangGraph run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from koda.agent_api import (
    PermissionRequest,
    TextDelta,
    ToolResult,
    ToolStart,
    Usage,
)

_log = logging.getLogger("koda.subagent_tasks")

# Role preambles prepended to the brief so a full coding_agent graph behaves
# like the chosen specialist. (We run a full agent per task rather than the
# tool-restricted subgraph, so all three roles — including edit — can run in the
# background, per the product decision.)
ROLE_PREAMBLES: dict[str, str] = {
    "explore": (
        "You are operating as an EXPLORE subagent: read-only codebase "
        "orientation. Do not edit files or run mutating shell commands. "
        "Return a short structured report with file:line citations.\n\nTask: "
    ),
    "plan": (
        "You are operating as a PLAN subagent: produce a concrete, ordered "
        "implementation plan (files, steps, risks, verification). Read-only — "
        "your output is the plan, not the code.\n\nTask: "
    ),
    "edit": (
        "You are operating as an EDIT subagent: apply the specified change end "
        "to end, then verify (typecheck/lint/tests) and report what changed "
        "with file:line citations.\n\nTask: "
    ),
    "general-purpose": "Task: ",
}

# Status vocabulary matches deepagents' official async-subagents middleware
# (https://docs.langchain.com/oss/python/deepagents/async-subagents) so the
# agent-facing tools report the same statuses the docs describe. "queued" and
# "paused" (awaiting a permission decision) are KODA extensions — the official
# remote transport has no HITL, so it never pauses.
TaskState = str  # "queued" | "running" | "paused" | "success" | "error" | "cancelled"


@dataclass
class TaskSummary:
    """Compact, UI-facing snapshot of a task — cheap to serialize each update."""

    id: str
    description: str
    subagent_type: str
    state: TaskState
    tool_count: int = 0
    current: str = ""          # what it's doing right now (last tool or "thinking")
    reply_chars: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0
    error: str = ""
    awaiting_permission: bool = False
    input_tokens: int = 0
    output_tokens: int = 0

    def to_json(self) -> dict[str, Any]:
        elapsed = (self.updated_at or self.started_at) - self.started_at if self.started_at else 0.0
        return {
            "id": self.id,
            "description": self.description,
            "subagent_type": self.subagent_type,
            "state": self.state,
            "tool_count": self.tool_count,
            "current": self.current,
            "reply_chars": self.reply_chars,
            "elapsed": round(elapsed, 1),
            "error": self.error,
            "awaiting_permission": self.awaiting_permission,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class BackgroundTask:
    id: str
    description: str
    subagent_type: str
    adapter: Any                       # CodingAgentAdapter (own thread_id)
    summary: TaskSummary
    final_text: str = ""
    tool_log: list[str] = field(default_factory=list)
    # Human-readable, chronological log of what the agent did (one line per tool
    # call, error-annotated). The dashboard scrolls this — it is the record of
    # "what is done", separate from the live streaming text.
    activity: list[str] = field(default_factory=list)
    _task: asyncio.Task | None = None
    _pending_perm: PermissionRequest | None = None
    # Last emitted change-key — lets _emit drop no-op updates (token streaming
    # would otherwise fire an identical task_update per delta).
    _last_emit_key: tuple | None = None


# Callback the registry fires whenever a task's summary changes, and again with
# ``done=True`` when a task finishes (so the bridge can post a notification).
UpdateCb = Callable[[BackgroundTask, bool], Awaitable[None] | None]


def _arg_hint(args: dict[str, Any]) -> str:
    """A short, human-readable target for a tool call (path, command, query…)
    so the activity log reads like ``edit_file(koda/bridge.py)`` instead of a
    bare tool name."""
    if not isinstance(args, dict):
        return ""
    for k in ("path", "file_path", "filename", "command", "cmd", "pattern", "query", "url", "description"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())[:48]
    return ""


class BackgroundTaskRegistry:
    """Owns all background subagent tasks for one KODA session."""

    def __init__(
        self,
        *,
        factory: Callable[..., Any],
        model: str,
        on_update: UpdateCb | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._model = model
        self._on_update = on_update
        self._clock = clock
        self._tasks: dict[str, BackgroundTask] = {}
        self._counter = 0

    # ── introspection ────────────────────────────────────────────────

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[TaskSummary]:
        return [t.summary for t in self._tasks.values()]

    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.summary.state in ("running", "paused", "queued"))

    def set_model(self, model: str) -> None:
        """Follow a /model switch — tasks spawned after this use the new model."""
        self._model = model

    # ── lifecycle ────────────────────────────────────────────────────

    def spawn(self, description: str, subagent_type: str = "general-purpose") -> str:
        """Create a task and start it running in the background. Returns its id."""
        self._counter += 1
        task_id = f"task_{self._counter}"
        thread_id = f"{task_id}-{int(self._clock() * 1000)}"
        adapter = self._factory(model=self._model, thread_id=thread_id)
        summary = TaskSummary(
            id=task_id,
            description=description,
            subagent_type=subagent_type,
            state="queued",
            started_at=self._clock(),
            updated_at=self._clock(),
            current="starting…",
        )
        task = BackgroundTask(
            id=task_id,
            description=description,
            subagent_type=subagent_type,
            adapter=adapter,
            summary=summary,
        )
        self._tasks[task_id] = task
        preamble = ROLE_PREAMBLES.get(subagent_type, ROLE_PREAMBLES["general-purpose"])
        task._task = asyncio.ensure_future(self._run(task, preamble + description))
        return task_id

    def stop(self, task_id: str) -> bool:
        """Cancel a running task. Its checkpoint survives, so it can be resumed."""
        task = self._tasks.get(task_id)
        if task is None or task._task is None or task._task.done():
            return False
        task._task.cancel()
        return True

    def resume(self, task_id: str, message: str = "Continue where you left off.") -> bool:
        """Send a message to a task on the SAME thread (memory intact).

        Mirrors the official ``update_async_task`` semantics: if the task is
        currently running, its run is interrupted and a fresh one starts on
        the same thread — so the subagent sees everything it did before plus
        the new message. Works equally on finished/cancelled tasks.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        prev = task._task
        if prev is not None and not prev.done():
            prev.cancel()

            async def _restart_after_cancel() -> None:
                try:
                    await asyncio.wait_for(asyncio.shield(prev), timeout=5)
                except Exception:
                    pass
                await self._run(task, message)

            task._task = asyncio.ensure_future(_restart_after_cancel())
            return True
        task._task = asyncio.ensure_future(self._run(task, message))
        return True

    def restart(self, task_id: str) -> bool:
        """Re-run the original brief on a FRESH thread (clean slate)."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task._task is not None and not task._task.done():
            task._task.cancel()
        # New adapter → new thread_id → no memory of the previous run.
        thread_id = f"{task_id}-{int(self._clock() * 1000)}"
        task.adapter = self._factory(model=self._model, thread_id=thread_id)
        task.final_text = ""
        task.tool_log.clear()
        task.activity.clear()
        task.summary.tool_count = 0
        task.summary.reply_chars = 0
        task.summary.input_tokens = 0
        task.summary.output_tokens = 0
        task.summary.started_at = self._clock()
        task.summary.error = ""
        preamble = ROLE_PREAMBLES.get(task.subagent_type, ROLE_PREAMBLES["general-purpose"])
        task._task = asyncio.ensure_future(self._run(task, preamble + task.description))
        return True

    def answer_permission(self, task_id: str, outcomes: list[str]) -> bool:
        """Answer a background task's gated tool call (approve/always/deny)."""
        from koda.tools import permissions as _perms

        task = self._tasks.get(task_id)
        if task is None or task._pending_perm is None:
            return False
        items = list(task._pending_perm.items)
        decisions: list[dict[str, Any]] = []
        for i, item in enumerate(items):
            outcome = outcomes[i] if i < len(outcomes) else "deny"
            if outcome == "always":
                _perms.allow_tool(item.tool_name)
                decisions.append({"type": "approve"})
            elif outcome == "allow":
                decisions.append({"type": "approve"})
            else:
                decisions.append({"type": "reject", "message": _perms.reject_message(item.tool_name)})
        provide = getattr(task.adapter, "provide_decisions", None)
        if provide is not None:
            provide(decisions)
        task._pending_perm = None
        task.summary.awaiting_permission = False
        # The graph resumes from its checkpoint the moment decisions land —
        # reflect that, or the task would report "paused" for the rest of its
        # run (and finish as paused instead of success).
        task.summary.state = "running"
        task.summary.current = "thinking…"
        return True

    async def aclose(self) -> None:
        """Cancel every task and close its adapter."""
        for task in list(self._tasks.values()):
            if task._task is not None and not task._task.done():
                task._task.cancel()
        for task in list(self._tasks.values()):
            if task._task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(task._task), timeout=2)
                except Exception:
                    pass
            aclose = getattr(task.adapter, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass

    # ── the run loop ─────────────────────────────────────────────────

    async def _run(self, task: BackgroundTask, message: str) -> None:
        task.summary.state = "running"
        task.summary.current = "thinking…"
        await self._emit(task)
        try:
            async for ev in task.adapter.stream(message, []):
                if isinstance(ev, ToolStart):
                    task.summary.tool_count += 1
                    hint = _arg_hint(ev.arguments)
                    label = f"{ev.name}({hint})" if hint else ev.name
                    task.summary.current = label
                    task.tool_log.append(ev.name)
                    task.activity.append(f"→ {label}")
                elif isinstance(ev, ToolResult):
                    task.summary.current = "thinking…"
                    if ev.is_error and task.activity:
                        task.activity[-1] = "✗ " + task.activity[-1][2:]
                elif isinstance(ev, Usage):
                    # Usage may arrive cumulatively mid-stream — take the max so a
                    # late smaller snapshot can't shrink the reported totals.
                    task.summary.input_tokens = max(task.summary.input_tokens, ev.input_tokens)
                    task.summary.output_tokens = max(task.summary.output_tokens, ev.output_tokens)
                elif isinstance(ev, TextDelta):
                    task.final_text += ev.content
                    task.summary.reply_chars = len(task.final_text)
                elif isinstance(ev, PermissionRequest):
                    task._pending_perm = ev
                    task.summary.state = "paused"
                    task.summary.awaiting_permission = True
                    task.summary.current = "awaiting permission"
                    await self._emit(task)
                    continue
                task.summary.updated_at = self._clock()
                await self._emit(task)
            # A stream only ends after any pause was resolved and the graph ran
            # to completion, so a normal end is always success.
            task.summary.state = "success"
            task.summary.current = "done"
        except asyncio.CancelledError:
            task.summary.state = "cancelled"
            task.summary.current = "cancelled"
            # Best-effort: tell the adapter to unwind its stream cleanly.
            try:
                await task.adapter.interrupt()
            except Exception:
                pass
            task.summary.updated_at = self._clock()
            await self._emit(task, done=True)
            raise
        except Exception as e:
            _log.exception("background task %s failed", task.id)
            task.summary.state = "error"
            task.summary.error = f"{type(e).__name__}: {e}"
            task.summary.current = "failed"
        task.summary.updated_at = self._clock()
        await self._emit(task, done=True)

    async def _emit(self, task: BackgroundTask, done: bool = False) -> None:
        if self._on_update is None:
            return
        # Coalesce: skip updates whose visible summary hasn't changed (reply
        # growth is bucketed to ~400 chars so streaming still shows progress
        # without one event per token). done=True always goes through.
        key = (
            task.summary.state,
            task.summary.current,
            task.summary.tool_count,
            task.summary.awaiting_permission,
            task.summary.reply_chars // 400,
            len(task.activity),
            task.summary.output_tokens // 200,
        )
        if not done and key == task._last_emit_key:
            return
        task._last_emit_key = key
        try:
            res = self._on_update(task, done)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            _log.debug("on_update callback failed", exc_info=True)
