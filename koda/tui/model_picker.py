"""
Model-picker modal for KODA.

Opened by ``/model`` with no arguments. Lists every reachable
``provider:model`` from :func:`koda.model_config.get_available_models`
in an OptionList; pressing Enter dismisses with the picked id, Escape
cancels.

Keybindings:
  Up/Down  — move
  Enter    — select
  Escape   — cancel
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class ModelPickerScreen(ModalScreen[str | None]):
    """Modal listing every ``provider:model`` we can reach right now.

    Returns the selected ``provider:model`` string, or ``None`` if cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    CSS = """
    ModelPickerScreen {
        align: center middle;
    }

    #model-picker-container {
        width: 80;
        height: 80%;
        border: solid $success 40%;
        background: $surface;
        padding: 1 2;
    }

    #model-picker-title {
        height: 1;
        text-style: bold;
        color: $success;
        margin: 0 0 1 0;
    }

    #model-picker-empty {
        color: $text-muted;
        margin: 1 0;
    }

    #model-picker-help {
        height: 1;
        color: $text-muted;
        dock: bottom;
    }

    #model-picker-options {
        height: 1fr;
        border: none;
        background: $surface;
    }

    #model-picker-options > .option-list--option-highlighted {
        background: $success 20%;
    }
    """

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        from koda.model_config import get_available_models

        try:
            available = get_available_models()
        except Exception:
            available = {}

        with Vertical(id="model-picker-container"):
            yield Static(
                f"[bold green]Select a model[/]  —  current: [dim]{self._current or 'none'}[/]",
                id="model-picker-title",
            )
            options: list[Option] = []
            for provider, models in sorted(available.items()):
                for m in sorted(models):
                    full = f"{provider}:{m}"
                    marker = " [yellow]<- current[/]" if full == self._current else ""
                    options.append(Option(f"{full}{marker}", id=full))
            if options:
                yield OptionList(*options, id="model-picker-options")
            else:
                yield Static(
                    "[dim]No reachable providers — check your API keys "
                    "or local Ollama / LM Studio.[/]",
                    id="model-picker-empty",
                )
            yield Static(
                "[dim]Up/Down[/] navigate  |  "
                "[dim]Enter[/] select  |  "
                "[dim]Esc[/] cancel",
                id="model-picker-help",
            )

    def on_mount(self) -> None:
        try:
            options = self.query_one("#model-picker-options", OptionList)
        except Exception:
            return
        # Highlight the current model if it's in the list, else the first row.
        target = 0
        for idx in range(options.option_count):
            opt = options.get_option_at_index(idx)
            if opt.id == self._current:
                target = idx
                break
        options.highlighted = target
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id:
            self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)
