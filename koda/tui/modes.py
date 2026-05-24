"""
Agent operating modes — Claude-Code-inspired.

Three modes, cycled with Shift+Tab in the TUI:

  - default     : agent runs tools freely; risky tools (write/edit/shell)
                  prompt the user the first time, with an "always allow
                  this tool for the session" escape hatch.
  - accept-edits: file writes and edits run without prompting; shell
                  commands still prompt.
  - plan        : agent is in advisory mode. Any mutating tool call is
                  rejected at the gate with a refusal string. The agent
                  outlines a plan; the user reviews and presses Shift+A
                  (or just sends "apply") to switch back to default and
                  execute it.

Modes are reflected in the status bar and as a tint on the composer
border. The truth source is ``koda.tools.permissions`` (so tools can
read the current mode from a sync thread); ``KodaApp`` mirrors it as a
reactive for the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    DEFAULT = "default"
    EDITS = "edits"
    PLAN = "plan"


# Cycle order for Shift+Tab. default → edits → plan → default …
ORDER: tuple[Mode, ...] = (Mode.DEFAULT, Mode.EDITS, Mode.PLAN)


def next_mode(current: Mode) -> Mode:
    try:
        i = ORDER.index(current)
    except ValueError:
        return Mode.DEFAULT
    return ORDER[(i + 1) % len(ORDER)]


@dataclass(frozen=True)
class ModeStyle:
    """Visual treatment for a mode — drives the status-bar badge label
    and the composer-border tint. ``css_class`` is added to the
    ``ChatInput`` so app.tcss can pick the right border color."""

    label: str           # short uppercase badge text
    color: str           # status-bar badge color (hex; readable on dark bg)
    css_class: str       # CSS class added to #app-root + ChatInput


STYLES: dict[Mode, ModeStyle] = {
    Mode.DEFAULT: ModeStyle(label="DEFAULT",     color="#fb923c", css_class="-mode-default"),
    Mode.EDITS:   ModeStyle(label="ACCEPT-EDITS", color="#84a86b", css_class="-mode-edits"),
    Mode.PLAN:    ModeStyle(label="PLAN",        color="#b48ac4", css_class="-mode-plan"),
}


def style_for(mode: Mode) -> ModeStyle:
    return STYLES[mode]
