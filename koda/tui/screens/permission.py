"""
Permission modal — pauses the agent when a mutating tool needs approval.

Shown when a tool (write_file / edit_file / execute) is called in
``default`` mode (or ``edits`` mode for shell). Dismisses with one of
three outcomes:

  * ``"allow"``  — run once
  * ``"always"`` — run, AND add the tool name to the session allow-list
                   so subsequent calls skip this prompt for this session
  * ``"deny"``   — refuse; the tool returns a refusal string to the agent

The agent runs in a worker thread (LangGraph sync tools), so the
modal's resolution has to cross the asyncio↔thread boundary. The bridge
lives in ``KodaApp.on_mount`` — it wraps ``push_screen_wait`` in
``asyncio.run_coroutine_threadsafe`` so the tool thread blocks cleanly
on a Future until the user answers.
"""

from __future__ import annotations

import json
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


# Return value of the modal — a small string so the bridge code can read
# it without importing the screen module from a tool thread.
Outcome = str  # "allow" | "always" | "deny"


class PermissionScreen(ModalScreen[Outcome]):
    """Modal prompt: agent wants to call <tool>(args). Allow?"""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "allow", "Allow", show=True),
        Binding("a", "always", "Allow + remember", show=True),
        Binding("n", "deny", "Deny", show=True),
        Binding("escape", "deny", "Deny", show=False),
    ]

    def __init__(self, tool_name: str, args: dict) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._args = args

    def compose(self) -> ComposeResult:
        # Pretty-print args, but clamp long string values so a 10k-line
        # write_file payload doesn't tile the whole screen.
        clamped = _clamp_args(self._args)
        args_pretty = json.dumps(clamped, indent=2, default=str)[:1200]

        with Vertical(id="perm-root"):
            yield Static(
                f"[reverse $accent] ALLOW TOOL? [/]  [b]{self._tool_name}[/]",
                classes="perm-head",
            )
            yield Static(
                "The agent is about to call this tool. "
                "Approve once, approve for the rest of the session, or deny.",
                classes="perm-msg",
            )
            with VerticalScroll(classes="perm-body"):
                yield Static(f"[dim]args[/]\n{args_pretty}")
            yield Static(
                "[b green]y[/] allow once   "
                "[b yellow]a[/] always allow this tool   "
                "[b red]n[/] deny   "
                "[dim](esc = deny)[/]",
                classes="perm-foot",
            )

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("deny")


def _clamp_args(args: dict, *, max_chars: int = 400) -> dict:
    """Trim long string fields so the modal body stays readable."""
    out: dict = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > max_chars:
            out[k] = v[:max_chars] + f"… (+{len(v) - max_chars} chars)"
        else:
            out[k] = v
    return out
