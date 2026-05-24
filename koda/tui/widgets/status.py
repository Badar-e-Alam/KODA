"""
Status bar — bottom row. Shows model · ↑in/↓out tokens · cache · mode.

Updated from `Usage` events by the stream pump and from model-switch events
by the app.
"""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

from koda.agent_api import AgentDescription, Usage
from koda.tui.modes import Mode, style_for


def _fmt(n: int) -> str:
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


class StatusBar(Static):
    model: reactive[str] = reactive("")
    mode: reactive[str] = reactive("chat")
    # Agent operating mode (default / edits / plan) — separate from the
    # input-mode reactive above which tracks the chat-input prefix
    # (chat / shell / command). Switched via Shift+Tab; see koda.tui.modes.
    agent_mode: reactive[str] = reactive(Mode.DEFAULT.value)
    input_tokens: reactive[int] = reactive(0)
    output_tokens: reactive[int] = reactive(0)
    cache_read: reactive[int] = reactive(0)
    supports_thinking: reactive[bool] = reactive(False)
    supports_vision: reactive[bool] = reactive(False)
    tool_count: reactive[int] = reactive(0)

    def on_mount(self) -> None:
        self._refresh_display()

    def watch_model(self, *_a) -> None:
        self._refresh_display()

    def watch_mode(self, *_a) -> None:
        self._refresh_display()

    def watch_agent_mode(self, *_a) -> None:
        self._refresh_display()

    def watch_input_tokens(self, *_a) -> None:
        self._refresh_display()

    def watch_output_tokens(self, *_a) -> None:
        self._refresh_display()

    def watch_cache_read(self, *_a) -> None:
        self._refresh_display()

    def watch_supports_thinking(self, *_a) -> None:
        self._refresh_display()

    def watch_supports_vision(self, *_a) -> None:
        self._refresh_display()

    def watch_tool_count(self, *_a) -> None:
        self._refresh_display()

    def set_model(self, provider: str, model: str) -> None:
        self.model = f"{provider}:{model}" if provider else (model or "")

    def set_capabilities(self, description: AgentDescription | None) -> None:
        """Refresh the capability badges from an :class:`AgentDescription`.

        Pass ``None`` (or an empty description) to clear — e.g. while an
        adapter is being rebuilt and a stale set of badges would mislead.
        """
        if description is None:
            self.supports_thinking = False
            self.supports_vision = False
            self.tool_count = 0
            return
        self.supports_thinking = bool(description.supports_thinking)
        self.supports_vision = bool(description.supports_vision)
        self.tool_count = len(description.tools)

    def update_usage(self, usage: Usage) -> None:
        """Replace displayed totals with the latest cumulative snapshot.

        Every KODA adapter emits *cumulative* usage on each Usage event
        (LangChain ``usage_metadata`` is cumulative-per-chunk, Anthropic's
        ``message.usage`` is the final-message running total,
        ``coding_agent`` explicitly forwards ``run_total``). ``BaseAdapter
        .merge_usage`` already uses REPLACE semantics for the same
        reason. Adding here would multiply the displayed count by the
        number of Usage events emitted per turn (one per LLM step) and
        compound across turns, so the context-window readout drifts
        upward forever.

        Non-zero gate keeps us from clobbering a known value when a
        chunk happens to omit one field — e.g. some providers report
        only output tokens during streaming and input only on the final
        chunk.
        """
        if usage.input_tokens:
            self.input_tokens = usage.input_tokens
        if usage.output_tokens:
            self.output_tokens = usage.output_tokens
        if usage.cache_read_tokens:
            self.cache_read = usage.cache_read_tokens

    def reset_usage(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0

    def _capability_badges(self) -> str:
        parts: list[str] = []
        if self.supports_thinking:
            parts.append("🧠")
        if self.supports_vision:
            parts.append("👁")
        if self.tool_count > 0:
            parts.append(f"⚙{self.tool_count}")
        return " ".join(parts)

    def _refresh_display(self) -> None:
        model = self.model or "(no model)"
        badges = self._capability_badges()
        tokens = f"↑{_fmt(self.input_tokens)} ↓{_fmt(self.output_tokens)}"
        if self.cache_read:
            tokens += f" cache {_fmt(self.cache_read)}"
        mode = self.mode or "chat"

        # Agent-mode pill: colored uppercase badge so users can see at a
        # glance which permission regime the next tool call will hit.
        try:
            am_style = style_for(Mode(self.agent_mode))
        except ValueError:
            am_style = style_for(Mode.DEFAULT)
        agent_pill = f"[reverse {am_style.color}] {am_style.label} [/]"

        segments = [model]
        if badges:
            segments.append(badges)
        segments.append(tokens)
        segments.append(mode)
        segments.append(agent_pill)
        self.update(f" {'  ·  '.join(segments)} ")
