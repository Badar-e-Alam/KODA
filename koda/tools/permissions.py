"""
Permission gate for mutating tools.

Tools call :func:`check` before performing any mutation. Behavior
depends on the current ``Mode``:

  * ``PLAN``    — every mutating tool is rejected with a refusal string
                  (the agent sees this and stays advisory; user reviews
                  the plan and presses Shift+A to apply).
  * ``EDITS``   — file writes/edits pass through silently; shell-style
                  mutations still prompt.
  * ``DEFAULT`` — every mutating tool prompts on first use unless the
                  user has already "always allowed" it for this session.

The prompt itself runs as a Textual modal — pure asyncio land. But
LangChain ``@tool`` functions are sync and execute on the LangGraph
worker thread. The bridge is :func:`set_prompt_hook`: the TUI installs
a callable that, given ``(tool_name, args)``, runs the modal on the
event loop and blocks the calling thread until the user answers. We
use ``asyncio.run_coroutine_threadsafe`` for that bridge — see
``KodaApp.on_mount``.
"""

from __future__ import annotations

import logging
from typing import Callable

from koda.tui.modes import Mode

_log = logging.getLogger("koda.permissions")

# ── Module state — single-process truth source ─────────────────────────
#
# ``_current_mode`` is the live mode. The TUI mutates it through
# ``set_mode``; tools read it via ``current_mode``. A plain module var
# is fine because all relevant code runs in one process — the LangGraph
# tool threads share the interpreter with the asyncio loop, and the GIL
# makes the read/write atomic at the granularity we care about.
_current_mode: Mode = Mode.DEFAULT

# Tools the user has explicitly "always allowed" for this session.
# Keyed by tool name — args are not part of the key on purpose; matching
# args here would force a prompt for every distinct invocation, which
# defeats the point of the "always" escape hatch.
_session_allow: set[str] = set()

# Optional hook: called from the tool thread to show the permission
# modal. Returns True to allow, False to deny. Install via
# :func:`set_prompt_hook`; if not installed the gate degrades to
# "allow" (so non-TUI consumers of the tools — tests, scripts — keep
# working).
PromptHook = Callable[[str, dict], bool]
_prompt_hook: PromptHook | None = None


# ── Tool classification ────────────────────────────────────────────────

# Tools that change the filesystem or run shell commands. Every other
# tool (ls, read_file, glob, grep, web_search, …) is read-only and
# bypasses the gate.
MUTATING_TOOLS: set[str] = {"write_file", "edit_file", "execute"}

# Subset of MUTATING_TOOLS that count as "file edits" — these pass
# through silently in EDITS mode.
FILE_EDIT_TOOLS: set[str] = {"write_file", "edit_file"}


# ── Public API ─────────────────────────────────────────────────────────


def current_mode() -> Mode:
    return _current_mode


def set_mode(mode: Mode) -> None:
    global _current_mode
    _current_mode = mode
    _log.info("mode → %s", mode.value)


def allow_tool(tool_name: str) -> None:
    """Add a tool to the session allow-list. Subsequent calls skip the prompt."""
    _session_allow.add(tool_name)
    _log.info("session-allow: %s", tool_name)


def clear_session_allow() -> None:
    """Reset the session allow-list. Called on /clear so a new chat
    starts from a clean permission slate."""
    _session_allow.clear()


def set_prompt_hook(hook: PromptHook | None) -> None:
    """Install (or remove) the blocking permission prompt."""
    global _prompt_hook
    _prompt_hook = hook


def check(tool_name: str, args: dict) -> str | None:
    """Gate a tool invocation.

    Returns ``None`` to allow the tool to run, or a refusal string that
    the tool should return to the agent (so the model sees *why* the
    call was blocked and can adapt).
    """
    if tool_name not in MUTATING_TOOLS:
        return None

    mode = _current_mode

    if mode is Mode.PLAN:
        return (
            f"[plan mode] `{tool_name}` is disabled. "
            "Outline the change in your reply; the user will press Shift+A "
            "(or send 'apply') to switch to default mode and execute it."
        )

    if mode is Mode.EDITS and tool_name in FILE_EDIT_TOOLS:
        return None

    if tool_name in _session_allow:
        return None

    # Default mode (or edits + execute): prompt.
    if _prompt_hook is None:
        # Hook not installed — running headless / from tests. Default to
        # allow so non-TUI consumers don't deadlock waiting on a UI that
        # isn't there. The TUI installs the hook in on_mount.
        return None
    try:
        allowed = _prompt_hook(tool_name, args)
    except Exception:
        _log.exception("permission hook raised; denying")
        return f"[denied] permission prompt failed for {tool_name}."
    if not allowed:
        return f"[denied] User rejected `{tool_name}`."
    return None
