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
        await app.mount_message(AppMessage(f"Current model: {app._model}"))
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


async def _compact(app: "KodaApp", _args: str) -> bool:
    """Summarize older messages to free up the model's context window."""
    adapter = app._adapter
    compact = getattr(adapter, "compact", None)
    if adapter is None or compact is None:
        await app.mount_message(
            AppMessage("Compaction isn't supported by the active agent.")
        )
        return True
    await app.mount_message(AppMessage("Compacting conversation…"))
    result = await compact()
    if result.compacted:
        await app.mount_message(
            AppMessage(
                f"✓ Compacted {result.summarized_messages} message(s) into a "
                "summary; recent turns kept intact."
            )
        )
    else:
        await app.mount_message(AppMessage(result.reason))
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


async def _set_mode(app: "KodaApp", mode_name: str) -> bool:
    """Slash-command path to switch agent mode without Shift+Tab cycling."""
    from koda.tui.modes import Mode, style_for

    aliases = {
        "default": Mode.DEFAULT, "normal": Mode.DEFAULT,
        "edits": Mode.EDITS, "edit": Mode.EDITS, "accept-edits": Mode.EDITS,
        "plan": Mode.PLAN, "planning": Mode.PLAN,
    }
    target = aliases.get(mode_name.strip().lower())
    if target is None:
        await app.mount_message(ErrorMessage(
            f"Unknown mode {mode_name!r}. Try: default, edits, plan."
        ))
        return True
    app._apply_agent_mode(target)
    s = style_for(target)
    await app.mount_message(AppMessage(f"Mode → {s.label.lower()}"))
    return True


async def _plan(app: "KodaApp", _args: str) -> bool:
    return await _set_mode(app, "plan")


async def _edits(app: "KodaApp", _args: str) -> bool:
    return await _set_mode(app, "edits")


async def _default_mode(app: "KodaApp", _args: str) -> bool:
    return await _set_mode(app, "default")


async def _agents(app: "KodaApp", _args: str) -> bool:
    from koda.agent_api import describe_agent

    if app._adapter is None:
        await app.mount_message(AppMessage("No agent loaded yet — give it a moment."))
        return True
    desc = describe_agent(app._adapter)
    caps: list[str] = []
    if desc.supports_thinking:
        caps.append("thinking")
    if desc.supports_vision:
        caps.append("vision")
    caps_str = ", ".join(caps) if caps else "—"
    lines = [
        "Current agent:",
        f"  name:         {desc.name}",
        f"  backend:      {desc.backend}",
        f"  capabilities: {caps_str}",
        f"  tools:        {len(desc.tools)}",
    ]
    if desc.system_prompt_preview:
        preview = desc.system_prompt_preview.replace("\n", " ").strip()
        lines.append(f"  system:       {preview}")
    await app.mount_message(AppMessage("\n".join(lines)))
    return True


async def _tools(app: "KodaApp", _args: str) -> bool:
    from koda.agent_api import describe_agent

    if app._adapter is None:
        await app.mount_message(AppMessage("No agent loaded yet — give it a moment."))
        return True
    desc = describe_agent(app._adapter)
    if not desc.tools:
        await app.mount_message(
            AppMessage(f"{desc.name}: no tool surface reported by this adapter.")
        )
        return True
    width = max(len(t.name) for t in desc.tools)
    lines = [f"Tools ({len(desc.tools)}):"]
    for tool in desc.tools:
        if tool.description:
            lines.append(f"  {tool.name:<{width}}  — {tool.description}")
        else:
            lines.append(f"  {tool.name}")
    await app.mount_message(AppMessage("\n".join(lines)))
    return True


_HELP: dict[str, tuple[Handler, str]] = {
    "clear": (_clear, "start a new chat session"),
    "model": (_model, "[provider:model] — switch model or show current"),
    "tree": (_tree, "open the session tree"),
    "compact": (_compact, "summarize older messages to free up context"),
    "copy": (_copy, "copy the last assistant response to clipboard"),
    "theme": (_theme, "[name] — switch color theme (or list)"),
    "usage": (_usage, "show cumulative token usage"),
    "agents": (_agents, "describe the active agent (backend, capabilities, tool count)"),
    "tools": (_tools, "list the active agent's tools"),
    "reload-memory": (_reload_memory, "re-read AGENTS.md (mid-session)"),
    "plan": (_plan, "switch agent to plan mode (advisory, no writes/shell)"),
    "edits": (_edits, "switch agent to accept-edits (file writes silent; shell asks)"),
    "default": (_default_mode, "switch agent back to default mode"),
    "help": (_help, "list all slash commands"),
    "quit": (_quit, "exit KODA"),
    "exit": (_quit, "exit KODA"),
}

HANDLERS: dict[str, Handler] = {name: h for name, (h, _) in _HELP.items()}
