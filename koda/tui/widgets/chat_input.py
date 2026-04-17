"""
Chat input widget.

A Textual Input with:
  - mode tracking (chat / shell / command) based on first character
  - up/down arrow history navigation
  - posts `ChatInput.Submitted` on Enter
"""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input


class ChatInput(Input):
    """The chat/shell/command input at the bottom of the TUI."""

    mode: reactive[str] = reactive("chat")

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "history_prev", "Prev in history", show=False),
        Binding("down", "history_next", "Next in history", show=False),
    ]

    class Submitted(Message):
        def __init__(self, sender: "ChatInput", value: str, mode: str) -> None:
            super().__init__()
            self.chat_input = sender
            self.value = value
            self.mode = mode

        @property
        def control(self) -> "ChatInput":
            return self.chat_input

    def __init__(self, placeholder: str = "Ask KODA anything… (/ for commands, ! for shell)") -> None:
        super().__init__(placeholder=placeholder)
        self._history: list[str] = []
        self._history_idx: int | None = None

    def watch_value(self, value: str) -> None:
        new_mode = _detect_mode(value)
        if new_mode != self.mode:
            self.mode = new_mode

    def watch_mode(self, mode: str) -> None:
        for cls in ("-chat", "-shell", "-command"):
            self.remove_class(cls)
        self.add_class(f"-{mode}")

    async def action_submit(self) -> None:  # overrides Input.action_submit
        text = self.value.strip()
        if not text:
            return
        self._history.append(text)
        self._history_idx = None
        mode = self.mode
        self.value = ""
        self.post_message(self.Submitted(self, text, mode))

    def action_history_prev(self) -> None:
        if not self._history:
            return
        if self._history_idx is None:
            self._history_idx = len(self._history) - 1
        else:
            self._history_idx = max(0, self._history_idx - 1)
        self.value = self._history[self._history_idx]

    def action_history_next(self) -> None:
        if self._history_idx is None:
            return
        self._history_idx += 1
        if self._history_idx >= len(self._history):
            self._history_idx = None
            self.value = ""
        else:
            self.value = self._history[self._history_idx]


def _detect_mode(value: str) -> str:
    if value.startswith("!"):
        return "shell"
    if value.startswith("/"):
        return "command"
    return "chat"
