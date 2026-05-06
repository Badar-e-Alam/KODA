"""
Status bar — bottom row. Shows model · ↑in/↓out tokens · cache · mode.

Updated from `Usage` events by the stream pump and from model-switch events
by the app.
"""

from __future__ import annotations

from typing import Any

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

    # ── Usage updates ─────────────────────────────────────────────────
    #
    # Each token streamed by some providers fires a Usage event. Setting
    # the three reactive properties immediately would trigger three
    # ``_refresh_display`` repaints per event — noticeable mid-stream.
    # ``update_usage_throttled`` accumulates deltas in a private buffer
    # and flushes them at most ~1 Hz; ``update_usage`` (un-throttled) is
    # kept for the final ``Done`` flush and the legacy direct-call path.
    _USAGE_FLUSH_INTERVAL = 1.0

    def update_usage(self, usage: Usage) -> None:
        """Apply a Usage delta immediately. Use for one-shot updates."""
        self._flush_pending_usage()
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read += usage.cache_read_tokens

    def update_usage_throttled(self, usage: Usage) -> None:
        """Coalesce mid-stream Usage deltas into a ~1 Hz repaint."""
        pending = getattr(self, "_pending_usage", None)
        if pending is None:
            pending = [0, 0, 0]
            self._pending_usage = pending
        pending[0] += usage.input_tokens
        pending[1] += usage.output_tokens
        pending[2] += usage.cache_read_tokens
        timer: Any = getattr(self, "_usage_flush_timer", None)
        if timer is None:
            self._usage_flush_timer = self.set_timer(
                self._USAGE_FLUSH_INTERVAL, self._flush_pending_usage
            )

    def _flush_pending_usage(self) -> None:
        timer: Any = getattr(self, "_usage_flush_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            self._usage_flush_timer = None
        pending = getattr(self, "_pending_usage", None)
        if not pending or not any(pending):
            self._pending_usage = None
            return
        self.input_tokens += pending[0]
        self.output_tokens += pending[1]
        self.cache_read += pending[2]
        self._pending_usage = None

    def reset_usage(self) -> None:
        self._flush_pending_usage()
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
