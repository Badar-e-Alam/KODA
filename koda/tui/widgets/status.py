"""
Status bar — bottom row. Shows model · ↑in/↓out tokens · cache · mode.

Updated from `Usage` events by the stream pump and from model-switch events
by the app.
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

from koda.agent_api import Usage


def _fmt(n: int) -> str:
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


class StatusBar(Static):
    model: reactive[str] = reactive("")
    mode: reactive[str] = reactive("chat")
    input_tokens: reactive[int] = reactive(0)
    output_tokens: reactive[int] = reactive(0)
    cache_read: reactive[int] = reactive(0)

    def on_mount(self) -> None:
        self._refresh_display()

    def watch_model(self, *_a) -> None:
        self._refresh_display()

    def watch_mode(self, *_a) -> None:
        self._refresh_display()

    def watch_input_tokens(self, *_a) -> None:
        self._refresh_display()

    def watch_output_tokens(self, *_a) -> None:
        self._refresh_display()

    def watch_cache_read(self, *_a) -> None:
        self._refresh_display()

    def set_model(self, provider: str, model: str) -> None:
        self.model = f"{provider}:{model}" if provider else (model or "")

    def update_usage(self, usage: Usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read += usage.cache_read_tokens

    def reset_usage(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0

    def _refresh_display(self) -> None:
        model = self.model or "(no model)"
        tokens = f"↑{_fmt(self.input_tokens)} ↓{_fmt(self.output_tokens)}"
        if self.cache_read:
            tokens += f" cache {_fmt(self.cache_read)}"
        mode = self.mode or "chat"
        self.update(f" {model}  ·  {tokens}  ·  {mode} ")
