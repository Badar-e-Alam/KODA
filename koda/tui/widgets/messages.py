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
from typing import Any

from textual.widgets import Static


class BaseMessage(Static):
    """Common base — individual subclasses tweak CSS classes."""


class UserMessage(BaseMessage):
    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._content = content


class AssistantMessage(BaseMessage):
    """Streaming assistant text. Call `append(delta)` for each TextDelta."""

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._content = content

    def append(self, delta: str) -> None:
        self._content += delta
        self.update(self._content)

    def set_text(self, text: str) -> None:
        self._content = text
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
        self._full_output = output
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
