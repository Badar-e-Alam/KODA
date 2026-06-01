"""Inline card for the agent's ``ask_user`` tool.

Shows the agent's question with optional preset choices **and** a free-text
field ("say something else…") so the user can either pick an option or type
a custom reply that goes straight back to the agent.

Controls (the card owns the keyboard — the composer is disabled while it's
up, see ``KodaApp._lock_composer``):

  ↑ / ↓      move the highlighted option
  type       fill the free-text field
  enter      send the typed text if there is any, else the highlighted option
  backspace  edit the typed text
  esc        cancel (returns "")

The answer the agent receives is the verbatim typed text when the user
typed something, otherwise the chosen option's text. Empty (Esc) means the
user declined. ``KodaApp`` also routes keys here via ``try_key`` as a
focus-race fallback (navigation/submit only — typing needs real focus).
"""

from __future__ import annotations

from typing import Callable

from textual import events
from textual.widgets import Static

_PLACEHOLDER = "say something else…"


def _clamp(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


class AskUserPrompt(Static):
    """Inline 'agent has a question for you' card with a free-text reply.

    Construct with the question, an optional list of choices, and a callback
    that fires with the user's answer (string). The user can pick a preset
    option (↑↓ + Enter) or type a custom reply in the "say something else"
    field; a non-empty typed reply wins over the highlighted option.
    """

    can_focus = True

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
        self._typed = ""  # free-text buffer for "say something else"
        self.add_class("ask-user-prompt")

    def on_mount(self) -> None:
        self._refresh_content()
        try:
            self.focus()
        except Exception:
            pass

    # ── Key handling ─────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        if self._handle(event.key, event.character):
            event.stop()
            event.prevent_default()

    def _handle(self, key: str, character: str | None) -> bool:
        if key == "escape":
            self._resolve("")
            return True
        if key == "enter":
            self._submit()
            return True
        if key == "up":
            self._move(-1)
            return True
        if key == "down":
            self._move(1)
            return True
        if key == "backspace":
            if self._typed:
                self._typed = self._typed[:-1]
                self._refresh_content()
            return True
        # Any single printable character fills the free-text field.
        if character and len(character) == 1 and character.isprintable():
            self._typed += character
            self._refresh_content()
            return True
        return False

    def try_key(self, key: str) -> bool:
        """Focus-race fallback used by ``KodaApp.on_key`` — navigation and
        submit only. Free-text typing needs real focus (and the character),
        which is handled in ``on_key``."""
        if key == "up":
            self._move(-1)
            return True
        if key == "down":
            self._move(1)
            return True
        if key == "enter":
            self._submit()
            return True
        if key == "escape":
            self._resolve("")
            return True
        return False

    # ── Actions ──────────────────────────────────────────────────────

    def _move(self, delta: int) -> None:
        if not self._options:
            return
        self._idx = (self._idx + delta) % len(self._options)
        self._refresh_content()

    def _submit(self) -> None:
        """Enter: typed text wins; else the highlighted option; else ack."""
        text = self._typed.strip()
        if text:
            self._resolve(text)
        elif self._options:
            self._resolve(self._options[self._idx])
        else:
            self._resolve("(acknowledged)")

    # ── Helpers ──────────────────────────────────────────────────────

    def _resolve(self, answer: str) -> None:
        cb = self._on_answer
        if cb is None:
            return
        self._on_answer = None
        try:
            cb(answer)
        except Exception:
            # Bridge handles its own future cancellation; don't let a
            # callback exception leave the card half-resolved.
            pass

    def _refresh_content(self) -> None:
        # NB: see PermissionPrompt for why this isn't named ``_render``.
        typing = bool(self._typed)
        question = _clamp(self._question.strip().replace("\n", " "), 200)
        lines: list[str] = [f"[reverse #fb923c] AGENT ASKS [/]  [b]{question}[/]", ""]

        for i, opt in enumerate(self._options):
            short = _clamp(opt, 100)
            # When the user is typing, dim the options to signal the typed
            # reply will win; otherwise highlight the selected option.
            if not typing and i == self._idx:
                lines.append(f"  [b reverse #fb923c] ❯ {i + 1}. {short} [/]")
            else:
                lines.append(f"    [dim]{i + 1}. {short}[/]")

        # Free-text "say something else" row — always available.
        if typing:
            shown = _clamp(self._typed, 120)
            lines.append(f"  [b reverse #fb923c] ✎ {shown}▌ [/]")
        else:
            lines.append(f"    [dim]✎ {_PLACEHOLDER}[/]")

        lines.append("")
        if self._options:
            lines.append("  [dim]↑↓ select · type to reply · enter send · esc cancel[/]")
        else:
            lines.append("  [dim]type to reply · enter send · esc cancel[/]")
        self.update("\n".join(lines))


__all__ = ["AskUserPrompt"]
