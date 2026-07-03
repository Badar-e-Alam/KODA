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

Behaviour depends on the current :class:`~koda.modes.Mode`:

  * ``PLAN``    — every mutating tool is auto-rejected (advisory-only;
                  the user reviews the plan and presses Shift+A to apply).
  * ``EDITS``   — file writes/edits auto-approve; shell calls auto-approve
                  too, *except* ``rm`` (file removal), which still asks.
  * ``DEFAULT`` — every mutating tool asks on first use, unless the user
                  has already "always allowed" it for this session (an
                  explicit "always" overrides the EDITS ``rm`` guard).

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
import re
import threading
from typing import Callable, Literal

from koda.modes import Mode

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

# Blanket auto-approve for unattended runs (``koda --no-tui -y`` and the eval
# harness). When set, ``decide`` approves every gated tool — including
# ``execute`` and the dangerous-execute backstop — because there is no human
# to answer a prompt. Off by default; only headless callers flip it on.
_auto_approve: bool = False


# ── Tool classification ────────────────────────────────────────────────

# Tools that change the filesystem or run shell commands. These are the
# tools wired into ``create_deep_agent(interrupt_on=…)`` (see
# ``coding_agent/agent.py``); every other tool (ls, read_file, glob, grep,
# web_search, git, …) is read-only and never interrupts.
MUTATING_TOOLS: set[str] = {"write_file", "edit_file", "multi_edit", "execute", "bash_background"}

# Subset of MUTATING_TOOLS that count as "file edits" — these pass through
# silently in EDITS mode.
FILE_EDIT_TOOLS: set[str] = {"write_file", "edit_file", "multi_edit"}

# ``execute`` commands that are dangerous enough to ALWAYS prompt, even when
# the user has session-allowed ``execute`` or is in EDITS mode. This is the
# defense-in-depth backstop for the class of bug where a single shell call
# walks the entire disk or destroys files: an orphaned
# ``glob('/**/.env', recursive=True)`` once pegged a CPU for 75 minutes. The
# session-allow escape hatch is keyed by tool *name* only (args aren't part
# of the key), so without this a single "always allow execute" would wave
# such a command straight through. Patterns are deliberately broad — a false
# "ask" costs one keypress; a false "approve" can cost the machine.
_DANGEROUS_EXECUTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Filesystem-root- or home-rooted walks (find / grep -r / rg / ls -R).
    re.compile(r"\b(find|grep|rg|ls|du|chmod|chown)\b[^\n|;&]*\s(/|~|\$HOME)(\s|$)"),
    # Python recursive walks anchored at root/home: glob('/**…'), os.walk('/'),
    # Path('/').rglob(…), pathlib rglob from root.
    re.compile(r"""glob\(\s*['"](/|~|\$HOME|/\*\*)"""),
    re.compile(r"""(os\.walk|rglob)\(\s*['"]?(/|~|os\.path\.expanduser)"""),
    re.compile(r"recursive\s*=\s*True[^\n]*['\"]/\*\*"),
    # Root-anchored shell globs: /**/…  or  /*  walks.
    re.compile(r"(^|\s)/\*\*?/"),
    # Destructive classics.
    re.compile(r"\brm\b[^\n|;&]*\s-[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME)(\s|$)"),
    re.compile(r"\b(mkfs|dd)\b[^\n]*\bif=|\bof=/dev/"),
    re.compile(r">\s*/dev/[sh]d[a-z]"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}"),  # fork bomb
)


def is_dangerous_execute(command: str) -> bool:
    """True if a shell command matches a known catastrophic pattern.

    Matching forces an ``"ask"`` verdict regardless of session-allow / mode
    (except PLAN, which already rejects everything). See
    ``_DANGEROUS_EXECUTE_PATTERNS`` for the rationale.
    """
    if not command:
        return False
    return any(p.search(command) for p in _DANGEROUS_EXECUTE_PATTERNS)


# ``rm`` (file removal) is the one shell verb that ACCEPT-EDITS mode still
# refuses to auto-approve. Removing a file is easy to trigger by accident,
# hard to undo, and unlike a build/edit can't be recovered from version
# control if it was never tracked. The pattern matches ``rm`` as a *command*
# word (optionally prefixed by sudo / nohup / exec / xargs or chained after
# ``;``/``&&``/``|``), so ``grep rm …`` or ``ls rm`` don't trip it.
_RM_CMD_PATTERN = re.compile(
    r"(?:^|[|;&]+\s*|\b(?:sudo|nohup|exec|xargs)\s+)rm\b"
)


def _is_rm_command(command: str) -> bool:
    """True if ``command`` removes files via ``rm`` (ACCEPT-EDITS guard).

    Deliberately narrow to the ``rm`` verb so ordinary shell calls
    (``ls``, ``git status``, ``pytest``, ``npm run build`` …) auto-approve
    in ACCEPT-EDITS mode; only ``rm`` still surfaces a prompt there.
    """
    if not command:
        return False
    return bool(_RM_CMD_PATTERN.search(command))


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


def set_auto_approve(on: bool) -> None:
    """Approve every gated tool for the rest of this session.

    Used by unattended entry points — ``koda --no-tui --auto-approve`` and the
    SWE-bench eval harness — where no human can answer a permission prompt.
    Once on, ``decide`` short-circuits to ``"approve"`` for everything
    (overriding PLAN reject and the dangerous-execute backstop), so only flip
    it on when the working tree is disposable (a one-shot run or a throwaway
    clone)."""
    global _auto_approve
    _auto_approve = on
    _log.info("auto-approve → %s", on)


def is_auto_approve() -> bool:
    return _auto_approve


def allow_tool(tool_name: str) -> None:
    """Add a tool to the session allow-list. Subsequent calls auto-approve."""
    _session_allow.add(tool_name)
    _log.info("session-allow: %s", tool_name)


def is_allowed(tool_name: str) -> bool:
    return tool_name in _session_allow


def clear_session_allow() -> None:
    """Reset the session allow-list. Called on /clear so a new chat starts
    from a clean permission slate."""
    global _auto_approve
    _session_allow.clear()
    _auto_approve = False


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
      * dangerous ``execute`` → ``"ask"`` (overrides session-allow / EDITS).
      * session-allowed tool → ``"approve"`` (honoured before EDITS, so an
        explicit "always" wins over the EDITS ``rm`` guard).
      * ``EDITS`` → ``"approve"`` for file edits *and* shell calls, except
        shell calls that remove files (``rm``) → ``"ask"``.
      * otherwise → ``"ask"``.

    A non-mutating tool should never reach here (it isn't in
    ``interrupt_on``); if one does, default to ``"approve"`` so we never
    wedge on an unexpected interrupt.
    """
    if tool_name not in MUTATING_TOOLS:
        return "approve"

    # Unattended runs approve everything — checked before PLAN and the
    # dangerous-execute backstop, since neither has a human to fall back on.
    if _auto_approve:
        return "approve"

    mode = _current_mode
    if mode is Mode.PLAN:
        return "reject"
    # Catastrophic shell commands always surface a prompt — the session-allow
    # escape hatch and EDITS auto-approve must not wave a disk-wide walk or an
    # ``rm -rf /`` straight through. Checked before those two branches.
    if tool_name in ("execute", "bash_background") and is_dangerous_execute((args or {}).get("command", "")):
        _log.warning("dangerous %s forced to ask: %r", tool_name, (args or {}).get("command", "")[:120])
        return "ask"
    # An explicit "always allow" from the user wins over mode-specific rules —
    # once the user has accepted a tool for the session, honour it everywhere
    # (including ACCEPT-EDITS, so the ``rm`` guard below doesn't re-prompt on
    # a tool the user already opted into for the rest of the session).
    if tool_name in _session_allow:
        return "approve"
    if mode is Mode.EDITS:
        # File writes/edits auto-approve silently.
        if tool_name in FILE_EDIT_TOOLS:
            return "approve"
        # Shell calls auto-approve too — *except* file removal (``rm``). A stray
        # ``rm`` is the one destructive shell verb that still asks, because it
        # is easy to trigger by accident and hard to undo. ``rm`` flagged by
        # ``is_dangerous_execute`` above is already handled; this catches the
        # ordinary ``rm somefile`` case.
        if tool_name in ("execute", "bash_background"):
            if _is_rm_command((args or {}).get("command", "")):
                return "ask"
            return "approve"
        # Any other mutating tool in EDITS (future-proofing) falls through to ask.
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
