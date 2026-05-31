"""Inline card for the agent's ``ask_user`` tool.

Mirrors ``PermissionPrompt``: mounted in ``#messages``, ``priority=True``
bindings so the chat input doesn't swallow ``y`` / number / arrow keys.
The agent passes a question + optional list of choices; the user
navigates with arrows (or jk / number keys 1-9) and confirms with
``Enter``. ``Esc`` cancels (returns an empty string).

Encouraged usage from the agent's side: in PLAN mode, before drafting,
or whenever the agent would otherwise guess at a requirement. The
system prompt has a ``<AskUser>`` block telling the agent when to reach
for this tool.
"""

from __future__ import annotations

from typing import Callable, ClassVar

from textual.binding import Binding, BindingType
from textual.widgets import Static


def _clamp(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


class AskUserPrompt(Static):
    """Inline 'agent has a question for you' card.

    Construct with the question text, an optional list of choices, and a
    callback that fires with the user's answer (string). When options is
    empty, the card surfaces a "press any key" prompt and dismisses on
    Enter with a placeholder reply — free-text input lives in the chat
    composer in v1, so the agent should phrase the question to allow a
    composer-style follow-up if it really needs typed text.
    """

    can_focus = True

    # ``priority=True`` is load-bearing: keeps keystrokes from leaking
    # to the focused chat input. See ``PermissionPrompt`` for the same
    # rationale.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "prev", "Up", show=False, priority=True),
        Binding("k", "prev", "Up", show=False, priority=True),
        Binding("down", "next", "Down", show=False, priority=True),
        Binding("j", "next", "Down", show=False, priority=True),
        Binding("enter", "confirm", "Confirm", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ] + [
        # 1-9 jump to that option directly (helpful when you have a
        # handful of choices and want to skip the arrow dance).
        Binding(str(i), f"pick_{i}", f"Pick {i}", show=False, priority=True)
        for i in range(1, 10)
    ]

    def __init__(
        self,
        question: str,
        options: list[str],
        on_answer: Callable[[str], None],
    ) -> None:
        super().__init__(" ")
        self._question = question
        self._options = list(options)
        self._on_answer: Callable[[str], None] | None = on_answer
        self._idx = 0
        self.add_class("ask-user-prompt")

    def on_mount(self) -> None:
        self._refresh_content()
        try:
            self.focus()
        except Exception:
            pass

    # ── Actions ──────────────────────────────────────────────────────

    def action_prev(self) -> None:
        if not self._options:
            return
        self._idx = (self._idx - 1) % len(self._options)
        self._refresh_content()

    def action_next(self) -> None:
        if not self._options:
            return
        self._idx = (self._idx + 1) % len(self._options)
        self._refresh_content()

    def action_confirm(self) -> None:
        if self._options:
            self._resolve(self._options[self._idx])
        else:
            # No options offered — return a sentinel so the agent knows
            # the user acknowledged but didn't pick anything specific.
            self._resolve("(acknowledged)")

    def action_cancel(self) -> None:
        # Esc returns empty string. The tool's caller (the agent) sees
        # the empty answer and can decide whether to retry or proceed.
        self._resolve("")

    def try_key(self, key: str) -> bool:
        """Run the action this key maps to; return True if handled. Used by
        the app-level focus-independent fallback (see ``PermissionPrompt.try_key``)."""
        nav = {"up": "prev", "k": "prev", "down": "next", "j": "next",
               "enter": "confirm", "escape": "cancel"}
        if key in nav:
            getattr(self, f"action_{nav[key]}")()
            return True
        if len(key) == 1 and key in "123456789":
            getattr(self, f"action_pick_{key}")()
            return True
        return False

    def __getattr__(self, name: str):
        # Wires number-key actions to ``_resolve(self._options[n-1])``.
        # Defined as ``__getattr__`` instead of nine separate methods so
        # we don't pollute the class with boilerplate.
        if name.startswith("action_pick_"):
            try:
                n = int(name[len("action_pick_") :])
            except ValueError:
                raise AttributeError(name)

            def _pick() -> None:
                if 1 <= n <= len(self._options):
                    self._idx = n - 1
                    self._resolve(self._options[n - 1])

            return _pick
        raise AttributeError(name)

    # ── Helpers ──────────────────────────────────────────────────────

    def _resolve(self, answer: str) -> None:
        cb = self._on_answer
        if cb is None:
            return
        self._on_answer = None
        try:
            cb(answer)
        except Exception:
            # Bridge handles its own future cancellation; we don't want
            # a callback exception to leave the card half-resolved.
            pass

    def _refresh_content(self) -> None:
        # NB: see PermissionPrompt for why this isn't named ``_render``.
        # Hex literal (#fb923c) is the KodaBanner orange — keeps the
        # ``AGENT ASKS`` chip and the highlighted option visually
        # anchored to the same brand color the left border uses, even
        # after a ``/theme`` switch.
        question = _clamp(self._question.strip().replace("\n", " "), 200)
        header = f"[reverse #fb923c] AGENT ASKS [/]  [b]{question}[/]"
        lines: list[str] = [header, ""]
        if self._options:
            for i, opt in enumerate(self._options):
                short = _clamp(opt, 100)
                if i == self._idx:
                    lines.append(f"  [b reverse #fb923c] ❯ {i + 1}. {short} [/]")
                else:
                    lines.append(f"    [dim]{i + 1}. {short}[/]")
            lines.append("")
            lines.append(
                "  [dim]↑↓ / jk navigate · 1-9 jump · enter submit · esc cancel[/]"
            )
        else:
            lines.append(
                "  [dim]No options offered — press enter to acknowledge, esc to cancel[/]"
            )
        self.update("\n".join(lines))


__all__ = ["AskUserPrompt"]
