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
import os
import re
import time
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

    ``append`` does NOT immediately repaint — that used to trigger a
    full transcript re-layout per LLM token, which on token-streaming
    backends (50–100 deltas/sec) saturated the asyncio loop and made
    the TUI feel frozen during long replies. Instead, deltas accumulate
    in ``self._content`` and a one-shot timer flushes the visible text
    every ``_BATCH_INTERVAL`` seconds (default 50 ms = ~20 repaints/sec,
    well within terminal refresh budgets). Override with
    ``KODA_STREAM_BATCH_MS`` if your terminal can't keep up.
    """

    # 50 ms ≈ one frame at 20 fps — fast enough that streaming still
    # *feels* live but slow enough that we coalesce 5–10 tokens per
    # repaint on most backends.
    _BATCH_INTERVAL = float(os.environ.get("KODA_STREAM_BATCH_MS", "50")) / 1000.0

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._content = content
        self._flush_timer = None  # type: ignore[var-annotated]

    def append(self, delta: str) -> None:
        self._content += delta
        # Already a flush scheduled? Just accumulate; the timer will
        # repaint with the latest content when it fires.
        if self._flush_timer is not None:
            return
        try:
            # ``set_timer`` (one-shot) is preferred over ``set_interval``
            # because we want exactly one repaint per batch — repeated
            # interval ticks would keep firing after the stream ends.
            self._flush_timer = self.set_timer(self._BATCH_INTERVAL, self._flush)
        except Exception:
            # Widget not mounted yet — leave _content accumulating; the
            # next append after mount will succeed in scheduling, and
            # _flush() will repaint with everything collected so far.
            self._flush_timer = None

    def set_text(self, text: str) -> None:
        # Direct setters (e.g. /tree replay) want immediate paint, no batching.
        self._content = text
        if self._flush_timer is not None:
            try:
                self._flush_timer.stop()
            except Exception:
                pass
            self._flush_timer = None
        try:
            self.update(text)
        except Exception:
            pass

    def _flush(self) -> None:
        """Paint the accumulated content. Called by the one-shot timer."""
        self._flush_timer = None
        try:
            self.update(self._content)
        except Exception:
            pass

    def on_unmount(self) -> None:
        """Cancel a pending flush so the timer can't fire after teardown."""
        if self._flush_timer is not None:
            try:
                self._flush_timer.stop()
            except Exception:
                pass
            self._flush_timer = None


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


_TODO_GLYPHS = {
    "pending": "○",
    "in_progress": "◐",
    "completed": "✓",
}


class TodoMessage(BaseMessage):
    """Inline todo checklist, fed by the agent's ``write_todos`` calls.

    Rendered as a normal message in the transcript so it flows with the
    conversation (like Claude). The stream pump updates the most recent
    TodoMessage in place while it's still the last message, otherwise it
    mounts a fresh block — so progress shows where it happens in the flow.

    Each todo is ``{"content": str, "status": "pending"|"in_progress"|"completed"}``.
    """

    def __init__(self, todos: list[dict[str, Any]] | None = None, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._todos: list[dict[str, Any]] = []
        self.set_todos(todos)

    def set_todos(self, todos: list[dict[str, Any]] | None) -> None:
        # Keep only well-formed items so a malformed payload can't crash render.
        self._todos = [t for t in (todos or []) if isinstance(t, dict)]
        self._refresh_display()

    def _refresh_display(self) -> None:
        if not self._todos:
            self.update("[dim]· (no tasks)[/]")
            return
        done = sum(1 for t in self._todos if t.get("status") == "completed")
        lines = [f"[b]Tasks[/] [dim]({done}/{len(self._todos)})[/]"]
        for todo in self._todos:
            status = str(todo.get("status", "pending"))
            glyph = _TODO_GLYPHS.get(status, _TODO_GLYPHS["pending"])
            content = str(todo.get("content", "")).strip()
            if status == "completed":
                lines.append(f"  [dim strike]{glyph} {content}[/]")
            elif status == "in_progress":
                lines.append(f"  [b]{glyph} {content}[/]")
            else:
                lines.append(f"  {glyph} {content}")
        self.update("\n".join(lines))


class ThinkingMessage(BaseMessage):
    """Pulsing 'agent is working' indicator.

    The stream pump (``koda/tui/stream.py``) keeps one of these mounted at
    the bottom of the messages container for the entire turn so the user
    always has visual confirmation the agent is alive — between tool calls,
    during slow TTFT on a cold cloud model, while a long ``run_tests``
    blocks, etc. The pump re-mounts a fresh instance after each new
    widget lands so the spinner stays pinned to the bottom.

    ``start_time`` is the turn's monotonic start so the clock keeps
    counting across re-mounts (a fresh instance per event would otherwise
    reset to ``0:00`` and hide that time is actually passing).
    """

    _FRAMES = ("·   ", "··  ", "··· ", " ···", "  ··", "   ·")

    # Sparkle "breathing" pulse: orange shades ramp dim → bright → dim so the
    # icon dims and brightens in a loop. Paired with a filled/outline glyph
    # twinkle (✦/✧) for a subtle sparkle. Indexed by ``self._frame``.
    _SPARKLE = (
        ("#7a3a10", "☆"),
        ("#9a4a12", "★"),
        ("#c2410c", "★"),
        ("#fb923c", "★"),
        ("#ffd0a3", "★"),
        ("#fb923c", "★"),
        ("#c2410c", "★"),
        ("#9a4a12", "☆"),
    )

    def __init__(
        self,
        label: str = "Thinking",
        start_time: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__("", **kwargs)
        self._label = label
        self._frame = 0
        self._timer = None
        # If the caller passes a start_time we anchor the elapsed clock to it;
        # otherwise (first mount of the turn) we anchor to mount time.
        self._start = start_time

    def on_mount(self) -> None:
        if self._start is None:
            self._start = time.monotonic()
        self._tick()
        self._timer = self.set_interval(0.15, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    def _tick(self) -> None:
        frame = self._FRAMES[self._frame % len(self._FRAMES)]
        self._frame += 1
        elapsed = time.monotonic() - (self._start or time.monotonic())
        mins = int(elapsed) // 60
        secs = int(elapsed) % 60
        ts = f"{mins}:{secs:02d}"
        color, glyph = self._SPARKLE[(self._frame - 1) % len(self._SPARKLE)]
        self.update(f"[{color}]{glyph}[/] [dim italic]{self._label} {ts} {frame}[/]")


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
