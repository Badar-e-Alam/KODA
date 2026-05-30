"""Bridge for the agent's ``ask_user`` tool.

The agent calls ``ask_user(question, options)`` from a LangGraph tool
worker thread; this module hands the question over to the TUI (via a
hook installed by ``KodaApp.on_mount``), blocks the worker thread until
the user picks an option (or types a free-text answer), and returns the
chosen string back through the tool's return value.

Mirrors ``koda.tools.permissions`` in shape:

  * Module-level ``_hook`` — a callable installed by the TUI.
  * ``ask(question, options) -> str`` — what the tool function calls;
    delegates to the hook when present, falls back to a sentinel string
    when headless so non-TUI consumers don't deadlock.
  * The TUI's hook handles the call_from_thread → mount widget → block
    on a ``concurrent.futures.Future`` dance.

The widget itself lives in ``koda.tui.widgets.ask_user_prompt``.
"""

from __future__ import annotations

import logging
from typing import Callable

_log = logging.getLogger("koda.ask_user")

# Hook signature: (question, options) -> user_answer_string.
# Empty options list means "free-text only".
AskHook = Callable[[str, list[str]], str]
_hook: AskHook | None = None


def set_hook(hook: AskHook | None) -> None:
    """Install (or remove) the TUI's ask-user bridge."""
    global _hook
    _hook = hook


def ask(question: str, options: list[str] | None = None) -> str:
    """Ask the user a question and return their answer (blocking).

    Returns a sentinel string when no UI hook is installed (e.g.
    headless eval harness) so the tool stays predictable rather than
    deadlocking on a prompt that will never appear.
    """
    opts = list(options or [])
    if _hook is None:
        return (
            "[ask_user unavailable] No UI hook installed — running headless. "
            "Proceed with your best guess and document the assumption."
        )
    try:
        answer = _hook(question, opts)
    except Exception:
        _log.exception("ask_user hook raised")
        return "[ask_user error] The user prompt failed; proceed with caution."
    return str(answer)


__all__ = ["ask", "set_hook", "AskHook"]
