"""
Message widgets for the KODA TUI.

Each message is a Static with its own style. The stream pump in
`koda/tui/stream.py` mounts these into the #messages container.

Widgets:
  UserMessage     — what the user typed
  AssistantMessage— streamed agent text (call .append(delta))
  ToolCallMessage — tool header + result preview (call .set_result(...))
  AppMessage      — informational (model switch, session resumed, ...)
  ErrorMessage    — errors (bold, red)
"""

from __future__ import annotations

import json
import re
from typing import Any

from textual.widgets import Static


# Matches both CSI (``ESC [ ... final``) and OSC (``ESC ] ... BEL``) sequences
# plus the odd ``\x1b(B`` character-set selector some tools emit. We strip
# these from tool output before rendering — otherwise raw cursor-up /
# clear-line codes land in a Static widget and the terminal misinterprets
# them on the next paint, breaking the TUI layout (e.g. `make` output
# producing ghost-duplicated input rows).
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z])"
)


def _sanitize_tool_output(text: str) -> str:
    """Strip ANSI escape sequences and normalize carriage returns.

    - Removes color, cursor, clear, and OSC sequences.
    - Collapses ``\\r\\n`` → ``\\n`` and drops bare ``\\r`` (progress-bar
      artifacts — the pre-``\\r`` content is what the shell meant to
      overwrite, so keeping only the final segment is the right call).
    """
    if not text:
        return text
    clean = _ANSI_RE.sub("", text)
    clean = clean.replace("\r\n", "\n")
    # For each line, keep only the part after the last \r (simulates what
    # the user would have seen in a real terminal after progress overwrites).
    clean = "\n".join(ln.rsplit("\r", 1)[-1] for ln in clean.split("\n"))
    return clean


class BaseMessage(Static):
    """Common base — individual subclasses tweak CSS classes."""


class UserMessage(BaseMessage):
    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._content = content


class AssistantMessage(BaseMessage):
    """Streaming assistant text. Call `append(delta)` for each TextDelta.

    Deltas are buffered and flushed at ~30 fps. Per-token ``self.update()``
    forces Textual to re-measure and re-layout the entire growing message
    on every token; coalescing into a single concat + update per frame
    keeps streaming smooth on slow terminals.
    """

    _FLUSH_INTERVAL = 0.033  # ~30 fps

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._content = content
        self._buffer: list[str] = []
        self._flush_timer: Any = None
        self._on_flush: Any = None  # optional callback (e.g. scroll-to-end)

    def append(self, delta: str) -> None:
        self._buffer.append(delta)
        if self._flush_timer is None:
            self._flush_timer = self.set_timer(self._FLUSH_INTERVAL, self._flush)

    def set_on_flush(self, cb: Any) -> None:
        """Register a no-arg callback fired after each buffer flush."""
        self._on_flush = cb

    def _flush(self) -> None:
        self._flush_timer = None
        if not self._buffer:
            return
        self._content += "".join(self._buffer)
        self._buffer.clear()
        self.update(self._content)
        if self._on_flush is not None:
            try:
                self._on_flush()
            except Exception:
                pass

    def finalize(self) -> None:
        """Flush any pending deltas and stop the timer. Idempotent."""
        if self._flush_timer is not None:
            try:
                self._flush_timer.stop()
            except Exception:
                pass
            self._flush_timer = None
        self._flush()

    def set_text(self, text: str) -> None:
        self._content = text
        self._buffer.clear()
        self.update(text)


class ToolCallMessage(BaseMessage):
    """Tool invocation header + 1-line result preview.

    Full output is stored on `_full_output` for copy/yank; the preview is
    clamped to `PREVIEW_LINES` lines / `PREVIEW_CHARS` characters.
    """

    PREVIEW_LINES = 1
    PREVIEW_CHARS = 80

    def __init__(
        self,
        tool_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__("", **kwargs)
        self._tool_id = tool_id
        self._tool_name = name
        self._args = arguments or {}
        self._full_output: str = ""
        self._is_error = False
        self._refresh_tool_display()

    def set_result(self, output: str, is_error: bool = False) -> None:
        self._full_output = _sanitize_tool_output(output)
        self._is_error = is_error
        if is_error:
            self.add_class("-error")
        self._refresh_tool_display()

    def _refresh_tool_display(self) -> None:
        args_str = _format_args(self._args)
        header = f"● {self._tool_name}({args_str})"
        body = self._preview(self._full_output) if self._full_output else "…"
        self.update(f"{header}\n  ↳ {body}")

    @classmethod
    def _preview(cls, text: str) -> str:
        lines = text.strip().splitlines()[: cls.PREVIEW_LINES]
        preview = " | ".join(lines)
        if len(preview) > cls.PREVIEW_CHARS:
            preview = preview[: cls.PREVIEW_CHARS - 1] + "…"
        total = text.count("\n") + 1 if text else 0
        if total > cls.PREVIEW_LINES:
            preview += f"  (+{total - cls.PREVIEW_LINES} lines)"
        return preview or "(empty)"


class AppMessage(BaseMessage):
    """Informational message (model switch, session resumed, etc.)."""

    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(f"· {content}", **kwargs)
        self._content = content


class ErrorMessage(BaseMessage):
    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(f"⚠ {content}", **kwargs)
        self._content = content


class ThinkingMessage(BaseMessage):
    """Pulsing placeholder shown while the agent is preparing its response.

    Removed as soon as the first TextDelta or ToolStart event arrives.
    The first paint is delayed 300 ms so fast model responses (first
    delta in <200 ms) never animate at all — saves a render storm on
    every turn.
    """

    _FRAMES = ("·   ", "··  ", "··· ", " ···", "  ··", "   ·")
    _START_DELAY = 0.30
    _TICK_INTERVAL = 0.25  # 4 Hz — slow enough not to fight streaming

    def __init__(self, label: str = "Thinking", **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._label = label
        self._frame = 0
        self._timer = None
        self._start_timer = None

    def on_mount(self) -> None:
        self._start_timer = self.set_timer(self._START_DELAY, self._begin_animation)

    def _begin_animation(self) -> None:
        self._start_timer = None
        self._tick()
        self._timer = self.set_interval(self._TICK_INTERVAL, self._tick)

    def on_unmount(self) -> None:
        if self._start_timer is not None:
            try:
                self._start_timer.stop()
            except Exception:
                pass
        if self._timer is not None:
            self._timer.stop()

    def _tick(self) -> None:
        frame = self._FRAMES[self._frame % len(self._FRAMES)]
        self._frame += 1
        self.update(f"[dim italic]{self._label} {frame}[/]")


def _format_args(args: dict[str, Any]) -> str:
    """Compact single-line repr for tool arguments."""
    if not args:
        return ""
    pairs = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 40:
            v = v[:37] + "…"
        try:
            pairs.append(f"{k}={json.dumps(v, default=str)}")
        except Exception:
            pairs.append(f"{k}={v!r}")
    joined = ", ".join(pairs)
    return joined if len(joined) <= 80 else joined[:79] + "…"
