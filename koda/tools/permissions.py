"""
Permission policy for mutating tools.

KODA gates the agent's mutating tools (``write_file`` / ``edit_file`` /
``multi_edit`` / ``execute``) through LangGraph's human-in-the-loop
``interrupt()`` mechanism: the graph pauses *before* running a gated tool,
checkpoints its state, and the adapter surfaces a ``PermissionRequest`` to
the TUI. Nothing blocks — neither the event loop nor a worker thread.

This module is the single source of truth for the *policy* that decides,
per gated tool call, whether to:

  * ``"approve"`` — let it run (auto, no prompt),
  * ``"reject"``  — refuse it (auto, no prompt), or
  * ``"ask"``     — surface a prompt and wait for the user.

Behaviour depends on the current :class:`~koda.tui.modes.Mode`:

  * ``PLAN``    — every mutating tool is auto-rejected (advisory-only;
                  the user reviews the plan and presses Shift+A to apply).
  * ``EDITS``   — file writes/edits auto-approve; ``execute`` still asks.
  * ``DEFAULT`` — every mutating tool asks on first use, unless the user
                  has already "always allowed" it for this session.

The decision is consumed by ``koda.adapters.langgraph.LangGraphAdapter``,
which turns ``"ask"`` results into a prompt and the user's answer into a
LangGraph resume command.

A separate *soft-pause* primitive (``wait_until_unpaused`` /
``mark_prompt_*``) is still used by the ``ask_user`` tool bridge so that
its worker-thread → UI-loop handoff coordinates focus; it is unrelated to
the permission policy above.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable, Literal

from koda.tui.modes import Mode

_log = logging.getLogger("koda.permissions")


# ── Soft pause (used only by the ``ask_user`` bridge) ──────────────────
#
# When the ``ask_user`` tool puts a question on screen from its worker
# thread, this lets any other in-flight worker-thread tool wait so it
# doesn't steal focus. The permission gate no longer uses this — it pauses
# the *whole graph* via ``interrupt()`` instead — but ask_user still does.
#
# Lazy-created because the module imports before the event loop exists, and
# ``asyncio.Event()`` binds to the current loop on first wait.

_pause_event: asyncio.Event | None = None
_pause_lock = threading.Lock()


def _ensure_pause_event() -> asyncio.Event:
    """Lazily create the pause event. Default state: set (no pause)."""
    global _pause_event
    with _pause_lock:
        if _pause_event is None:
            _pause_event = asyncio.Event()
            _pause_event.set()
    return _pause_event


async def wait_until_unpaused() -> None:
    """Block while an ``ask_user`` prompt is being shown. No-op when none."""
    await _ensure_pause_event().wait()


def mark_prompt_pending() -> None:
    """Pause: signal that an ``ask_user`` prompt is now visible.

    MUST be called on the asyncio loop's thread (``asyncio.Event.clear`` is
    not thread-safe in the general case). The TUI bridge uses
    ``App.call_from_thread`` to satisfy this from the worker thread.
    """
    _ensure_pause_event().clear()


def mark_prompt_resolved() -> None:
    """Resume: signal that the ``ask_user`` prompt has been dismissed."""
    _ensure_pause_event().set()


# ── Module state — single-process truth source ─────────────────────────
#
# ``_current_mode`` is the live mode. The TUI mutates it through
# ``set_mode``; the adapter reads it via ``decide``. A plain module var is
# fine because all relevant code runs in one process under one event loop.
_current_mode: Mode = Mode.DEFAULT

# Tools the user has explicitly "always allowed" for this session.
# Keyed by tool name — args are not part of the key on purpose; matching
# args here would force a prompt for every distinct invocation, which
# defeats the point of the "always" escape hatch.
_session_allow: set[str] = set()


# ── Tool classification ────────────────────────────────────────────────

# Tools that change the filesystem or run shell commands. These are the
# tools wired into ``create_deep_agent(interrupt_on=…)`` (see
# ``coding_agent/agent.py``); every other tool (ls, read_file, glob, grep,
# web_search, git, …) is read-only and never interrupts.
MUTATING_TOOLS: set[str] = {"write_file", "edit_file", "multi_edit", "execute"}

# Subset of MUTATING_TOOLS that count as "file edits" — these pass through
# silently in EDITS mode.
FILE_EDIT_TOOLS: set[str] = {"write_file", "edit_file", "multi_edit"}

# The exact mapping handed to deepagents' ``interrupt_on``. Keeping it here
# keeps the gated-tool list and the policy in one place.
INTERRUPT_ON: dict[str, dict] = {
    name: {"allowed_decisions": ["approve", "reject"]} for name in MUTATING_TOOLS
}

Verdict = Literal["approve", "reject", "ask"]


# ── Public API ─────────────────────────────────────────────────────────


def current_mode() -> Mode:
    return _current_mode


def set_mode(mode: Mode) -> None:
    global _current_mode
    _current_mode = mode
    _log.info("mode → %s", mode.value)


def allow_tool(tool_name: str) -> None:
    """Add a tool to the session allow-list. Subsequent calls auto-approve."""
    _session_allow.add(tool_name)
    _log.info("session-allow: %s", tool_name)


def is_allowed(tool_name: str) -> bool:
    return tool_name in _session_allow


def clear_session_allow() -> None:
    """Reset the session allow-list. Called on /clear so a new chat starts
    from a clean permission slate."""
    _session_allow.clear()


def reject_message(tool_name: str) -> str:
    """The message handed back to the model when a gated call is rejected.

    In PLAN mode this nudges the agent to stay advisory; otherwise it's a
    plain "the user said no" so the model can adapt instead of retrying.
    """
    if _current_mode is Mode.PLAN:
        return (
            f"[plan mode] `{tool_name}` is disabled. Outline the change in your "
            "reply; the user will press Shift+A (or send 'apply') to switch to "
            "default mode and execute it."
        )
    return f"[denied] The user rejected `{tool_name}`."


def decide(tool_name: str, args: dict | None = None) -> Verdict:
    """Decide what to do with a gated tool call, *without* prompting.

    Returns one of ``"approve"`` / ``"reject"`` / ``"ask"``. The adapter
    auto-resolves approve/reject and only surfaces a prompt for ``"ask"``.

      * ``PLAN``  → ``"reject"`` for every mutating tool (advisory-only).
      * ``EDITS`` → ``"approve"`` for file edits; ``execute`` falls through.
      * session-allowed tool → ``"approve"``.
      * otherwise → ``"ask"``.

    A non-mutating tool should never reach here (it isn't in
    ``interrupt_on``); if one does, default to ``"approve"`` so we never
    wedge on an unexpected interrupt.
    """
    if tool_name not in MUTATING_TOOLS:
        return "approve"

    mode = _current_mode
    if mode is Mode.PLAN:
        return "reject"
    if mode is Mode.EDITS and tool_name in FILE_EDIT_TOOLS:
        return "approve"
    if tool_name in _session_allow:
        return "approve"
    return "ask"


# ── Legacy synchronous gate (for sync @tool backends) ──────────────────
#
# The ``coding_agent`` graph gates via LangGraph's ``interrupt_on`` and the
# adapter — it does NOT use the functions below. But the ``deep`` adapter
# (``koda/adapters/deep.py``) wires plain synchronous ``@tool`` functions
# from ``koda/tools/fs.py`` that can't pause a graph, so they keep using
# this blocking gate: ``check()`` returns ``None`` to allow or a refusal
# string to deny, prompting via ``_prompt_hook`` for the ``"ask"`` case.
#
# The hook (installed by the TUI as ``KodaApp._prompt_from_tool_thread``)
# runs on a *worker* thread and blocks only that thread — never the event
# loop — so it doesn't freeze the TUI. Headless callers (no hook) degrade
# to allow so tests/scripts don't deadlock.

PromptHook = Callable[[str, dict], bool]
_prompt_hook: PromptHook | None = None


def set_prompt_hook(hook: PromptHook | None) -> None:
    """Install (or remove) the blocking permission prompt used by ``check``."""
    global _prompt_hook
    _prompt_hook = hook


def check(tool_name: str, args: dict | None = None) -> str | None:
    """Synchronous gate for sync-``@tool`` backends (the ``deep`` adapter).

    Returns ``None`` to allow the tool to run, or a refusal string the tool
    should hand back to the model. Reuses :func:`decide` for the policy and
    prompts via the installed hook when the verdict is ``"ask"``. The
    ``coding_agent`` graph does not call this — it pauses the graph via
    ``interrupt_on`` instead.
    """
    verdict = decide(tool_name, args or {})
    if verdict == "approve":
        return None
    if verdict == "reject":
        return reject_message(tool_name)
    # "ask" — prompt the user via the blocking hook.
    if _prompt_hook is None:
        # Headless (no TUI) — allow so non-interactive consumers don't hang.
        return None
    try:
        allowed = _prompt_hook(tool_name, args or {})
    except Exception:
        _log.exception("permission hook raised; denying")
        return f"[denied] permission prompt failed for {tool_name}."
    return None if allowed else f"[denied] User rejected `{tool_name}`."
