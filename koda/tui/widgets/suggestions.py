"""
SuggestionPopup — inline dropdown that sits right above the ChatInput.

Claude-Code / Codex layout:
  ╭─ Commands  (8) ─────────────────────────╮
  │  /clear      start a new chat            │
  │  /copy       copy last response          │
  │ ❯/tree       open the session tree       │   (highlighted)
  │  ↑↓ navigate · ⏎ accept · esc dismiss   │
  ╰──────────────────────────────────────────╯

Shown when the user types `/`, `/model `, `/theme `, or `@` in the input.
The ChatInput owns keyboard routing (up/down/enter/escape) and forwards
actions to this widget via `highlight_next`, `highlight_prev`,
`current_selection`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from koda.tui.completers import Suggestion


# Max label width so description columns align
_LABEL_WIDTH = 28

# Footer hint line shown at the bottom of the popup
_FOOTER_HINT = "↑↓ navigate · ⏎ accept · tab complete · esc dismiss"


class SuggestionPopup(Container):
    """Inline suggestion list with a category header + keybind hint footer."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._suggestions: list["Suggestion"] = []
        self._title: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="suggest-header", classes="suggest-header")
        yield OptionList(id="suggest-list")
        yield Static(f"  {_FOOTER_HINT}", id="suggest-footer", classes="suggest-footer")

    def set_suggestions(self, suggestions: list["Suggestion"], title: str = "") -> None:
        self._suggestions = suggestions
        self._title = title
        header = self.query_one("#suggest-header", Static)
        options = self.query_one("#suggest-list", OptionList)
        options.clear_options()
        if not suggestions:
            self.remove_class("-visible")
            return
        count = len(suggestions)
        header.update(f"  {title}  ({count})" if title else f"  ({count})")
        for s in suggestions:
            options.add_option(Option(_format_row(s)))
        self.add_class("-visible")
        options.highlighted = 0

    def clear(self) -> None:
        self._suggestions = []
        try:
            self.query_one("#suggest-list", OptionList).clear_options()
        except Exception:
            pass
        self.remove_class("-visible")

    @property
    def is_visible(self) -> bool:
        return "-visible" in self.classes and bool(self._suggestions)

    @property
    def highlighted(self) -> int | None:
        try:
            return self.query_one("#suggest-list", OptionList).highlighted
        except Exception:
            return None

    @highlighted.setter
    def highlighted(self, value: int | None) -> None:
        try:
            self.query_one("#suggest-list", OptionList).highlighted = value
        except Exception:
            pass

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


def _format_row(s: "Suggestion") -> str:
    """Two-column row: label padded, then dim description."""
    label = s.label
    if len(label) > _LABEL_WIDTH:
        label = label[: _LABEL_WIDTH - 1] + "…"
    padded = label.ljust(_LABEL_WIDTH)
    if s.description:
        return f"[b cyan]{padded}[/]  [dim]{s.description}[/]"
    return f"[b cyan]{padded}[/]"
