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

    class SuggestionsDismissed(Message):
        """Fired when the user dismisses the popup (escape / accept).

        Lets the app restore any UI state it altered while the popup was open
        (e.g. a collapsed banner).
        """

        def __init__(self, sender: "ChatInput") -> None:
            super().__init__()
            self.chat_input = sender

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
        # Set True around programmatic value mutations (suggestion accept) so
        # watch_value doesn't re-fire the completer with stale cursor state.
        self._suppress_suggestions: bool = False

    def attach_popup(self, popup: "SuggestionPopup") -> None:
        self._popup = popup

    # ── Value / mode tracking ────────────────────────────────────────

    def watch_value(self, value: str) -> None:
        new_mode = _detect_mode(value)
        if new_mode != self.mode:
            self.mode = new_mode
        # Ask the app to refresh suggestions — unless we're mid-accept and
        # the cursor hasn't been moved yet (see _accept_suggestion).
        if not self._suppress_suggestions:
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
            # Enter is *not* intercepted — we let it reach ``action_submit``
            # which both accepts the highlighted suggestion and (when the
            # inserted text is a complete command, i.e. no trailing space)
            # submits in one press. Intercepting here used to require a
            # second Enter press, breaking /theme, /model, etc.
            if event.key == "tab":
                event.prevent_default(); event.stop()
                self._accept_suggestion()
                return
            if event.key == "escape":
                event.prevent_default(); event.stop()
                popup.clear()
                self.post_message(self.SuggestionsDismissed(self))
                return
        # Otherwise fall through to Input's default handler

    # ── Actions ──────────────────────────────────────────────────────

    async def action_submit(self) -> None:  # overrides Input.action_submit
        # If popup is visible, accept the highlighted suggestion first.
        # If the inserted text ends with a space, the command expects an
        # argument — stay in input mode and wait. Otherwise submit immediately.
        if self._popup is not None and self._popup.is_visible:
            suggestion = self._popup.current_selection
            if suggestion is not None:
                self._accept_suggestion()
                if suggestion.insert.endswith(" "):
                    # Wait for the user to type the argument
                    return
                # Complete: fall through and submit
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

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Gate the Esc → ``dismiss_popup`` binding on popup visibility.

        Without this, ChatInput unconditionally consumes Esc (it has
        focus by default), which silently swallowed the app-level
        ``escape`` → ``interrupt_turn`` binding any time the user
        tried to stop the agent from inside the composer. Returning
        ``False`` from ``check_action`` tells Textual to skip the
        binding for this dispatch, so Esc bubbles up to the app and
        ``KodaApp.action_interrupt_turn`` fires as intended.
        """
        if action == "dismiss_popup":
            return self._popup is not None and self._popup.is_visible
        return True

    def action_paste(self) -> None:
        """Ctrl+V — paste from the OS clipboard into the input at the cursor.

        Overrides Input.action_paste (which only uses Textual's in-process
        clipboard) so users can paste text copied from outside the TUI.
        Falls back to the in-process clipboard if pyperclip is unavailable.
        """
        text = ""
        try:
            import pyperclip

            text = pyperclip.paste() or ""
        except Exception:
            text = ""
        if not text:
            text = self.app.clipboard or ""
        if not text:
            return
        # Collapse newlines — ChatInput is single-line
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        start, end = self.selection
        self.replace(text, start, end)

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

        Important: we clear the popup and set the ``_suppress_suggestions``
        flag *before* mutating ``self.value`` — otherwise the reactive
        ``watch_value`` fires ``SuggestionsRequested`` with the *old* cursor
        position, and the completer re-suggests the same file/theme, so the
        next Enter appends the suggestion again instead of submitting.
        """
        popup = self._popup
        if popup is None or not popup.is_visible:
            return False
        suggestion = popup.current_selection
        if suggestion is None:
            return False
        replace_range = getattr(self, "_last_replace_range", None)
        if replace_range is None:
            return False
        start, end = replace_range
        value = self.value
        new_value = value[:start] + suggestion.insert + value[end:]
        new_cursor = start + len(suggestion.insert)

        # Silence the completer during the value+cursor update.
        self._suppress_suggestions = True
        try:
            popup.clear()
            self._last_replace_range = None
            self.value = new_value
            try:
                self.cursor_position = new_cursor
            except Exception:
                pass
        finally:
            self._suppress_suggestions = False
        # Now that cursor + value are in sync, let the completer re-evaluate
        # against the fresh state (may legitimately open a new popup — e.g.
        # after /theme <space>, show theme names).
        self.post_message(self.SuggestionsRequested(self, self.value, self.cursor_position))
        self.post_message(self.SuggestionsDismissed(self))
        return True


def _detect_mode(value: str) -> str:
    if value.startswith("!"):
        return "shell"
    if value.startswith("/"):
        return "command"
    return "chat"
