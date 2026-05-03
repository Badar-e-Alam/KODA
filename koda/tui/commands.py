"""
Slash-command dispatcher.

Each handler gets (app, args_string). Return True if the command was handled
(so the message is not forwarded to the agent), False otherwise.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from koda.tui.widgets.messages import AppMessage, ErrorMessage

if TYPE_CHECKING:
    from koda.tui.app import KodaApp

_log = logging.getLogger("koda.tui.commands")

Handler = Callable[["KodaApp", str], Awaitable[bool]]


async def dispatch(app: "KodaApp", text: str) -> bool:
    """Handle /command. Returns True if consumed."""
    if not text.startswith("/"):
        return False
    name, _, args = text[1:].partition(" ")
    handler = HANDLERS.get(name.lower())
    if handler is None:
        await app.mount_message(ErrorMessage(f"Unknown command: /{name}"))
        return True
    try:
        await handler(app, args.strip())
    except Exception as e:
        _log.exception("Command /%s failed", name)
        await app.mount_message(ErrorMessage(f"/{name} failed: {e}"))
    return True


# ── Handlers ─────────────────────────────────────────────────────────

async def _clear(app: "KodaApp", _args: str) -> bool:
    await app.action_clear_session()
    return True


async def _model(app: "KodaApp", args: str) -> bool:
    if not args:
        app.action_open_model_picker()
        return True
    await app.switch_model(args)
    return True


async def _tree(app: "KodaApp", _args: str) -> bool:
    app.action_open_tree()
    return True


async def _copy(app: "KodaApp", _args: str) -> bool:
    await app.action_yank_last()
    return True


async def _theme(app: "KodaApp", args: str) -> bool:
    from koda.tui.theme import THEMES

    if not args:
        names = ", ".join(sorted(THEMES.keys()))
        await app.mount_message(AppMessage(f"Available themes: {names}"))
        return True
    if args not in THEMES:
        await app.mount_message(ErrorMessage(f"Unknown theme: {args}"))
        return True
    app.apply_theme(args)
    await app.mount_message(AppMessage(f"Theme: {args}"))
    return True


async def _usage(app: "KodaApp", _args: str) -> bool:
    sb = app._status_bar
    if sb is None:
        return True
    msg = (
        f"Session usage — input: {sb.input_tokens:,}  "
        f"output: {sb.output_tokens:,}  cache read: {sb.cache_read:,}"
    )
    await app.mount_message(AppMessage(msg))
    return True


async def _help(app: "KodaApp", _args: str) -> bool:
    lines = ["Slash commands:"]
    for name, (handler, desc) in sorted(_HELP.items()):
        lines.append(f"  /{name:<8} {desc}")
    await app.mount_message(AppMessage("\n".join(lines)))
    return True


async def _quit(app: "KodaApp", _args: str) -> bool:
    app.exit()
    return True


async def _reload_memory(app: "KodaApp", _args: str) -> bool:
    await app.reload_memory()
    return True


_HELP: dict[str, tuple[Handler, str]] = {
    "clear": (_clear, "start a new chat session"),
    "model": (_model, "[provider:model] — switch model or show current"),
    "tree": (_tree, "open the session tree"),
    "copy": (_copy, "copy the last assistant response to clipboard"),
    "theme": (_theme, "[name] — switch color theme (or list)"),
    "usage": (_usage, "show cumulative token usage"),
    "reload-memory": (_reload_memory, "re-read AGENTS.md (mid-session)"),
    "help": (_help, "list all slash commands"),
    "quit": (_quit, "exit KODA"),
    "exit": (_quit, "exit KODA"),
}

HANDLERS: dict[str, Handler] = {name: h for name, (h, _) in _HELP.items()}
