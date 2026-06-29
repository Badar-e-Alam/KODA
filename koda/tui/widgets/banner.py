"""
KODA welcome banner — ASCII art + one random tip.

Pure Textual (no deepagents_cli inheritance).
"""

from __future__ import annotations

import random

from textual.widgets import Static

from koda import __version__

_KODA_ART = f"""\
  ██╗  ██╗  ██████╗  ██████╗   █████╗
  ██║ ██╔╝ ██╔═══██╗ ██╔══██╗ ██╔══██╗
  █████╔╝  ██║   ██║ ██║  ██║ ███████║
  ██╔═██╗  ██║   ██║ ██║  ██║ ██╔══██║
  ██║  ██╗ ╚██████╔╝ ██████╔╝ ██║  ██║
  ╚═╝  ╚═╝  ╚═════╝  ╚═════╝  ╚═╝  ╚═╝
  v{__version__}"""

_TIPS = [
    "Use /tree or Ctrl+T to navigate session branches",
    "Use /model to switch between LLM providers on the fly",
    "Use /theme <name> to change the color theme",
    "Use ! prefix for shell commands (e.g. !ls)",
    "Use Ctrl+B to toggle the session sidebar",
    "Use Ctrl+Y to copy the last assistant response",
    "Select & copy text and click links like a normal terminal — Ctrl+O for mouse/scroll mode",
    "Use Ctrl+L to start a new chat",
    "Use /help to list all slash commands",
]


class KodaBanner(Static):
    """Banner at the top of the TUI."""

    def __init__(self, thread_id: str | None = None, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._thread_id = thread_id
        self._tip = random.choice(_TIPS)  # noqa: S311
        self._status: str = "ready"
        self._failure: str | None = None

    def on_mount(self) -> None:
        self._refresh_banner()

    def set_connected(self, *, local: bool = False) -> None:
        self._status = "local" if local else "connected"
        self._failure = None
        self._refresh_banner()

    def set_failed(self, error: str) -> None:
        self._status = "failed"
        self._failure = error
        self._refresh_banner()

    def update_thread_id(self, thread_id: str) -> None:
        self._thread_id = thread_id
        self._refresh_banner()

    def _refresh_banner(self) -> None:
        lines = [_KODA_ART, ""]
        lines.append("  Your AI teammate, right in the terminal.")
        if self._failure:
            lines.append(f"  ⚠ Connection failed: {self._failure}")
        else:
            lines.append(f"  Tip: {self._tip}")
        self.update("\n".join(lines))
