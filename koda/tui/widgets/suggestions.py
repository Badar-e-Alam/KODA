"""
SuggestionPopup — floating OptionList above the ChatInput.

Shown when the user types `/`, `/model `, or `@` in the input. The ChatInput
owns keyboard routing (up/down/enter/escape) and forwards actions to this
widget via `highlight_next`, `highlight_prev`, `current_selection`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import OptionList
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from koda.tui.completers import Suggestion


class SuggestionPopup(OptionList):
    """Floating suggestion list. Hidden when empty."""

    DEFAULT_CSS = """
    SuggestionPopup {
        height: auto;
        max-height: 12;
        dock: bottom;
        offset-y: -3;
        background: $surface;
        border: round $accent;
        padding: 0 1;
        display: none;
    }

    SuggestionPopup.-visible {
        display: block;
    }

    SuggestionPopup > .option-list--option-highlighted {
        background: $accent 30%;
        text-style: bold;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._suggestions: list["Suggestion"] = []

    def set_suggestions(self, suggestions: list["Suggestion"]) -> None:
        self.clear_options()
        self._suggestions = suggestions
        if not suggestions:
            self.remove_class("-visible")
            return
        for s in suggestions:
            self.add_option(Option(s.display()))
        self.add_class("-visible")
        self.highlighted = 0

    def clear(self) -> None:  # type: ignore[override]
        self._suggestions = []
        self.clear_options()
        self.remove_class("-visible")

    @property
    def is_visible(self) -> bool:
        return "-visible" in self.classes and bool(self._suggestions)

    @property
    def current_selection(self) -> "Suggestion | None":
        idx = self.highlighted
        if idx is None or not (0 <= idx < len(self._suggestions)):
            return None
        return self._suggestions[idx]

    def highlight_next(self) -> None:
        if not self._suggestions:
            return
        cur = self.highlighted if self.highlighted is not None else -1
        self.highlighted = (cur + 1) % len(self._suggestions)

    def highlight_prev(self) -> None:
        if not self._suggestions:
            return
        cur = self.highlighted if self.highlighted is not None else 0
        self.highlighted = (cur - 1) % len(self._suggestions)
