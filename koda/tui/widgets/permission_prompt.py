"""Inline permission prompt — mounts in the messages container.

Replaces the previous full-screen ``PermissionScreen`` modal. Matches the
Claude-Code style: when the agent calls a mutating tool in DEFAULT mode
(or ``execute`` in EDITS mode), a prompt card slides in beneath the
streaming output. The user picks an option with ``y`` / ``a`` / ``n`` /
``Esc`` *or* navigates ↑/↓ (also ``j``/``k``) and hits ``Enter``.

The agent's gate runs on a worker thread (the backend wraps
``_perms.check`` in ``asyncio.to_thread``), so the bridge in
``KodaApp._prompt_from_tool_thread`` mounts this widget via
``App.call_from_thread`` and blocks the worker on a
``concurrent.futures.Future`` that the widget's choice callback resolves.

``priority=True`` on every binding is load-bearing: the chat input keeps
focus while the prompt mounts unless we force focus to ourselves, and
even with explicit ``focus()`` Textual sometimes routes keys to whatever
was focused first. ``priority=True`` short-circuits ahead of any focused
widget's handler so keys reach the prompt regardless.
"""

from __future__ import annotations

import json
from typing import Any, Callable, ClassVar

from textual.binding import Binding, BindingType
from textual.widgets import Static


# (outcome, chip-label, hotkey) — order is also the navigation order.
# Chip labels are intentionally short so the prompt fits on one line
# inside the card; the hotkey letter does the rest of the affordance.
_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("allow", "allow once", "y"),
    ("always", "always", "a"),
    ("deny", "deny", "n"),
)


def _clamp(text: str, max_chars: int) -> str:
    """Trim ``text`` for one-line display; append an ellipsis on overflow."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


class PermissionPrompt(Static):
    """Inline permission card. Mount via ``KodaApp.mount_message``.

    Construct with a ``tool_name``, an ``args`` dict, and an ``on_choice``
    callback. The callback receives one of ``"allow"`` / ``"always"`` /
    ``"deny"`` and runs on the UI event loop (Textual's action handlers).
    """

    can_focus = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "prev", "Up", show=False, priority=True),
        Binding("k", "prev", "Up", show=False, priority=True),
        Binding("down", "next", "Down", show=False, priority=True),
        Binding("j", "next", "Down", show=False, priority=True),
        Binding("enter", "confirm", "Confirm", show=False, priority=True),
        Binding("y", "pick_allow", "Allow once", show=False, priority=True),
        Binding("a", "pick_always", "Always", show=False, priority=True),
        Binding("n", "pick_deny", "Deny", show=False, priority=True),
        Binding("escape", "pick_deny", "Deny", show=False, priority=True),
    ]

    def __init__(
        self,
        tool_name: str,
        args: dict[str, Any],
        on_choice: Callable[[str], None],
    ) -> None:
        # Static needs initial renderable content — without it Textual's
        # first height-measurement pass crashes with
        # ``AttributeError: 'NoneType' object has no attribute 'get_height'``
        # because ``_render()`` returns None before ``on_mount`` runs.
        # We immediately overwrite this in ``on_mount`` via ``_render``.
        super().__init__(" ")
        self._tool_name = tool_name
        self._args = args
        self._on_choice: Callable[[str], None] | None = on_choice
        self._idx = 0  # which option is currently highlighted
        self.add_class("permission-prompt")

    def on_mount(self) -> None:
        self._refresh_content()
        # Take focus so non-priority bindings also resolve cleanly. The
        # ``priority=True`` flags on every binding mean we already win the
        # race against the chat input even without focus, but explicit
        # focus avoids a subtle UX wart where the cursor blinks in the
        # composer while the prompt is up.
        try:
            self.focus()
        except Exception:
            pass

    # ── Actions ──────────────────────────────────────────────────────

    def action_prev(self) -> None:
        self._idx = (self._idx - 1) % len(_OPTIONS)
        self._refresh_content()

    def action_next(self) -> None:
        self._idx = (self._idx + 1) % len(_OPTIONS)
        self._refresh_content()

    def action_confirm(self) -> None:
        self._resolve(_OPTIONS[self._idx][0])

    def action_pick_allow(self) -> None:
        self._resolve("allow")

    def action_pick_always(self) -> None:
        self._resolve("always")

    def action_pick_deny(self) -> None:
        self._resolve("deny")

    # ── Helpers ──────────────────────────────────────────────────────

    def _resolve(self, outcome: str) -> None:
        """Fire the choice callback once and only once.

        Guards against double-fire (rapid keypress, ``Enter`` after a
        hotkey, etc.) so the worker thread blocked on the future doesn't
        see a stale or conflicting resolution.
        """
        cb = self._on_choice
        if cb is None:
            return
        self._on_choice = None
        try:
            cb(outcome)
        except Exception:
            # Swallow — the bridge handles its own future cancellation
            # and we don't want a callback exception to leave the prompt
            # half-resolved on screen.
            pass

    def _refresh_content(self) -> None:
        # NB: must NOT be named ``_render`` — that name is reserved by
        # Textual's ``Widget`` for the framework's render pipeline (which
        # expects a ``Visual``-returning method). Shadowing it makes
        # height measurement crash with ``AttributeError: 'NoneType' has
        # no attribute 'get_height'`` because our void method returns
        # None instead of a renderable.

        # Vertical layout — one option per line — to match
        # ``AskUserPrompt`` so the up/down keys have a natural "stack"
        # feel instead of left/right across a one-line chip row. Hotkey
        # letter (y/a/n) is rendered at the head of each row so the
        # keyboard affordance survives the redesign — users can still
        # tap the letter and skip the arrows entirely.
        summary = self._summarize()
        header_bits = ["[reverse #fb923c] PERMISSION [/]", f"[b]{self._tool_name}[/]"]
        if summary:
            header_bits.append(f"[dim]{summary}[/]")
        lines: list[str] = ["  ".join(header_bits), ""]

        for i, (_outcome, label, hot) in enumerate(_OPTIONS):
            if i == self._idx:
                # Hex literal (not a theme var) so the highlight matches
                # the KodaBanner / left-border orange in every theme.
                lines.append(f"  [b reverse #fb923c] ❯ {hot}. {label} [/]")
            else:
                lines.append(f"    [dim]{hot}. {label}[/]")
        lines.append("")
        lines.append(
            "  [dim]↑↓ navigate · y/a/n jump · enter submit · esc deny[/]"
        )
        self.update("\n".join(lines))

    def _summarize(self) -> str:
        """One-line argument summary, tool-aware.

        ``write_file`` / ``edit_file`` show the path; ``execute`` shows
        the command (clamped). Anything else falls back to a compact
        ``key=value`` join. We keep this short on purpose — the inline
        card lives in the message stream alongside live agent output and
        any vertical bloat makes the stream feel slower than it is.
        """
        if not self._args:
            return ""
        if self._tool_name in ("write_file", "edit_file") and "file_path" in self._args:
            return _clamp(str(self._args["file_path"]), 80)
        if self._tool_name == "execute" and "command" in self._args:
            return _clamp(str(self._args["command"]), 100)
        # Generic fallback: key=value, comma-joined, clamped.
        pairs = []
        for k, v in self._args.items():
            s = v if isinstance(v, str) else json.dumps(v, default=str)
            pairs.append(f"{k}={_clamp(s, 40)}")
        return _clamp(", ".join(pairs), 100)


__all__ = ["PermissionPrompt"]
