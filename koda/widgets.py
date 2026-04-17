"""
KODA banner widget — extends deepagents-cli WelcomeBanner.

Overrides the ASCII art with KODA branding while inheriting all
lifecycle methods (set_connected, set_failed, update_thread_id).
"""

from __future__ import annotations

import random
from typing import Any

from textual.content import Content
from textual.color import Color as TColor
from textual.style import Style as TStyle

from deepagents_cli.widgets.welcome import (
    WelcomeBanner,
    build_connecting_footer,
    build_failure_footer,
    build_welcome_footer,
)
from deepagents_cli import theme

from koda import __version__

# ── ASCII Art ──────────────────────────────────────────────────────────

_KODA_ART = f"""\
  ██╗  ██╗  ██████╗  ██████╗   █████╗
  ██║ ██╔╝ ██╔═══██╗ ██╔══██╗ ██╔══██╗
  █████╔╝  ██║   ██║ ██║  ██║ ███████║
  ██╔═██╗  ██║   ██║ ██║  ██║ ██╔══██║
  ██║  ██╗ ╚██████╔╝ ██████╔╝ ██║  ██║
  ╚═╝  ╚═╝  ╚═════╝  ╚═════╝  ╚═╝  ╚═╝
  v{__version__}"""

_TIPS = [
    "Use @ to attach files to your message",
    "Use /tree or Ctrl+T to navigate session branches",
    "Use /model to switch between LLM providers on the fly",
    "Use /theme to change the color theme",
    "Use /mcp to see your loaded MCP servers and tools",
    "Use Ctrl+X to open your message in $EDITOR",
    "Use ! prefix for shell commands (e.g. !ls)",
    "Use Ctrl+B to toggle the session sidebar",
    "Ctrl+C copies selected text, Ctrl+Y copies last response",
    "Use --auto-approve or -y flag to skip tool confirmations",
    "Use /offload to compress long conversations",
]


class KodaBanner(WelcomeBanner):
    """KODA-branded banner — drop-in replacement for WelcomeBanner."""

    def __init__(
        self,
        thread_id: str | None = None,
        mcp_tool_count: int = 0,
        *,
        connecting: bool = False,
        resuming: bool = False,
        local_server: bool = False,
        **kwargs: Any,
    ) -> None:
        self._koda_tip: str = random.choice(_TIPS)  # noqa: S311
        super().__init__(
            thread_id=thread_id,
            mcp_tool_count=mcp_tool_count,
            connecting=connecting,
            resuming=resuming,
            local_server=local_server,
            **kwargs,
        )
        self._tip = self._koda_tip
        self._project_name = None
        self._project_url = None

    def on_mount(self) -> None:
        """Skip parent's background fetch — just watch theme."""
        self.watch(self.app, "theme", self._on_theme_change, init=False)

    def _build_banner(self, project_url: str | None = None) -> Content:
        """Build KODA-branded banner content."""
        parts: list[str | tuple[str, str | TStyle] | Content] = []

        try:
            colors = theme.get_theme_colors(self)
            ansi = self.app.theme == "textual-ansi"
        except Exception:
            colors = None
            ansi = True

        primary_style: str | TStyle = (
            "bold"
            if ansi or colors is None
            else TStyle(foreground=TColor.parse(colors.primary), bold=True)
        )

        parts.append((_KODA_ART + "\n", primary_style))

        # MCP tools
        if self._mcp_tool_count:
            success_color: str = "bold green" if (ansi or colors is None) else colors.success
            parts.extend([
                ("  + ", success_color),
                (f"{self._mcp_tool_count} MCP tools loaded", "dim"),
                "\n",
            ])

        # Footer: failure / connecting / ready
        if self._failed:
            parts.append(build_failure_footer(self._failure_error))
        elif self._connecting:
            parts.append(
                build_connecting_footer(
                    resuming=self._resuming,
                    local_server=self._local_server,
                )
            )
        else:
            parts.extend([
                "\n",
                ("  Your AI teammate, right in the terminal.\n", "bold"),
                ("  Tip: ", "dim"),
                (self._tip + "\n", "dim italic"),
            ])

        return Content.assemble(*parts)
