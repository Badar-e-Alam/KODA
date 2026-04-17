"""
Chat input widget.

Features:
  - mode tracking (chat / shell / command) based on first character
  - up/down arrow history navigation (when popup is hidden)
  - when a SuggestionPopup is attached and visible:
      up/down   → navigate popup
      tab/enter → accept current suggestion
      escape    → dismiss popup
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input

if TYPE_CHECKING:
    from koda.tui.widgets.suggestions import SuggestionPopup


class ChatInput(Input):
    """The chat/shell/command input at the bottom of the TUI."""

    mode: reactive[str] = reactive("chat")

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "history_prev", "Prev in history", show=False),
        Binding("down", "history_next", "Next in history", show=False),
        Binding("escape", "dismiss_popup", "Dismiss popup", show=False),
        Binding("tab", "accept_suggestion", "Accept suggestion", show=False),
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

    class SuggestionsRequested(Message):
        """Fired when the input value changes — app computes & sets suggestions."""

        def __init__(self, sender: "ChatInput", value: str, cursor: int) -> None:
            super().__init__()
            self.chat_input = sender
            self.value = value
            self.cursor = cursor

        @property
        def control(self) -> "ChatInput":
            return self.chat_input

    def __init__(
        self,
        placeholder: str = "Ask KODA anything… (/ for commands, @ for files, ! for shell)",
    ) -> None:
        super().__init__(placeholder=placeholder)
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._popup: "SuggestionPopup | None" = None

    def attach_popup(self, popup: "SuggestionPopup") -> None:
        self._popup = popup

    # ── Value / mode tracking ────────────────────────────────────────

    def watch_value(self, value: str) -> None:
        new_mode = _detect_mode(value)
        if new_mode != self.mode:
            self.mode = new_mode
        # Ask the app to refresh suggestions
        self.post_message(self.SuggestionsRequested(self, value, self.cursor_position))

    def watch_mode(self, mode: str) -> None:
        for cls in ("-chat", "-shell", "-command"):
            self.remove_class(cls)
        self.add_class(f"-{mode}")

    # ── Popup-aware key handling ─────────────────────────────────────

    async def _on_key(self, event: events.Key) -> None:
        popup = self._popup
        if popup is not None and popup.is_visible:
            if event.key == "up":
                event.prevent_default(); event.stop()
                popup.highlight_prev()
                return
            if event.key == "down":
                event.prevent_default(); event.stop()
                popup.highlight_next()
                return
            if event.key == "enter":
                if self._accept_suggestion():
                    event.prevent_default(); event.stop()
                    return
            if event.key == "tab":
                event.prevent_default(); event.stop()
                self._accept_suggestion()
                return
            if event.key == "escape":
                event.prevent_default(); event.stop()
                popup.clear()
                return
        # Otherwise fall through to Input's default handler

    # ── Actions ──────────────────────────────────────────────────────

    async def action_submit(self) -> None:  # overrides Input.action_submit
        # If popup is visible, accept suggestion instead of submitting
        if self._popup is not None and self._popup.is_visible:
            if self._accept_suggestion():
                return
        text = self.value.strip()
        if not text:
            return
        self._history.append(text)
        self._history_idx = None
        mode = self.mode
        self.value = ""
        self.post_message(self.Submitted(self, text, mode))

    def action_dismiss_popup(self) -> None:
        if self._popup is not None:
            self._popup.clear()

    def action_accept_suggestion(self) -> None:
        self._accept_suggestion()

    def action_history_prev(self) -> None:
        # If the popup is showing, delegate to it instead of history
        if self._popup is not None and self._popup.is_visible:
            self._popup.highlight_prev()
            return
        if not self._history:
            return
        if self._history_idx is None:
            self._history_idx = len(self._history) - 1
        else:
            self._history_idx = max(0, self._history_idx - 1)
        self.value = self._history[self._history_idx]

    def action_history_next(self) -> None:
        if self._popup is not None and self._popup.is_visible:
            self._popup.highlight_next()
            return
        if self._history_idx is None:
            return
        self._history_idx += 1
        if self._history_idx >= len(self._history):
            self._history_idx = None
            self.value = ""
        else:
            self.value = self._history[self._history_idx]

    # ── Internal ─────────────────────────────────────────────────────

    def _accept_suggestion(self) -> bool:
        """Apply the currently highlighted suggestion to the input value.

        Returns True if a suggestion was applied.
        """
        popup = self._popup
        if popup is None or not popup.is_visible:
            return False
        suggestion = popup.current_selection
        if suggestion is None:
            return False
        # Replace the currently-active completion range in the value
        replace_range = getattr(self, "_last_replace_range", None)
        if replace_range is None:
            return False
        start, end = replace_range
        value = self.value
        new_value = value[:start] + suggestion.insert + value[end:]
        self.value = new_value
        # Put cursor at end of inserted text
        try:
            self.cursor_position = start + len(suggestion.insert)
        except Exception:
            pass
        popup.clear()
        return True


def _detect_mode(value: str) -> str:
    if value.startswith("!"):
        return "shell"
    if value.startswith("/"):
        return "command"
    return "chat"
