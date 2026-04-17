"""Structured conversation logger.

Writes a readable ``.md`` file alongside each session's debug log.
Each turn is numbered and timestamped. Tool call outputs use HTML
``<details>`` so they collapse in any markdown viewer (VS Code,
GitHub, etc.).

Usage in KodaApp::

    self._conv_log = ConversationLog("logs/session_2026-04-15.md", model="openai:gpt-4o")
    self._conv_log.user("Fix the bug in app.py")
    self._conv_log.tool_call("read_file", {"path": "app.py"})
    self._conv_log.tool_result("read_file", "(file contents ...)")
    self._conv_log.assistant("I found the bug on line 42 ...")
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("koda.conversation")


class ConversationLog:
    """Append-only markdown conversation log."""

    def __init__(self, path: str | Path, *, model: str = "") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._step = 0
        self._model = model
        self._write_header()

    # ── Public API ────────────────────────────────────────────────────

    def user(self, message: str) -> None:
        """Log a user message (starts a new step)."""
        self._step += 1
        ts = self._ts()
        self._append(
            f"\n---\n\n"
            f"### Step {self._step} — {ts}\n\n"
            f"**User:**\n"
            f"> {self._quote(message)}\n"
        )

    def assistant(self, text: str) -> None:
        """Log an assistant text response."""
        if not text.strip():
            return
        self._append(f"\n**Assistant:**\n{text.strip()}\n")

    def tool_call(self, name: str, args: dict | None = None) -> None:
        """Log a tool invocation (before execution)."""
        args_str = self._format_args(args) if args else ""
        self._append(
            f"\n<details>\n"
            f"<summary><b>Tool: {name}</b>{args_str}</summary>\n\n"
        )

    def tool_result(self, name: str, output: str, *, error: bool = False) -> None:
        """Log tool output and close the ``<details>`` block."""
        label = "Error" if error else "Output"
        trimmed = output.strip()
        if len(trimmed) > 2000:
            trimmed = trimmed[:2000] + f"\n\n... ({len(output) - 2000} more chars)"
        self._append(
            f"**{label}:**\n"
            f"```\n{trimmed}\n```\n\n"
            f"</details>\n"
        )

    # ── Internals ─────────────────────────────────────────────────────

    def _write_header(self) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"# KODA Session — {ts}\n"]
        if self._model:
            lines.append(f"**Model:** `{self._model}`\n")
        self._path.write_text("\n".join(lines), encoding="utf-8")

    def _append(self, text: str) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            _log.debug("Conversation log write failed: %s", exc)

    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%H:%M:%S")

    @staticmethod
    def _quote(text: str) -> str:
        """Blockquote multi-line text."""
        return "\n> ".join(text.strip().split("\n"))

    @staticmethod
    def _format_args(args: dict) -> str:
        """One-line summary of tool args for the ``<summary>`` tag."""
        parts: list[str] = []
        for key, val in args.items():
            s = str(val)
            if len(s) > 60:
                s = s[:57] + "..."
            parts.append(f"{key}={s}")
        joined = ", ".join(parts)
        if len(joined) > 120:
            joined = joined[:117] + "..."
        return f" — `{joined}`"
