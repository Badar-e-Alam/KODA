"""
KODA theme registry — small set of curated dark palettes.

Each theme maps semantic roles (primary, user, assistant, tool, error, ...)
to color hex strings. Widgets reference roles; themes swap live via the
`theme` reactive on `KodaApp`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    primary: str        # headings, banner, status model
    accent: str         # highlights (selected, emphasis)
    assistant: str      # assistant text (default = inherit)
    user: str           # user bubble border
    tool: str           # tool call header
    tool_ok: str        # tool result ok
    tool_err: str       # tool result error
    error: str          # error messages
    muted: str          # dimmed text
    background: str     # base background
    surface: str        # panels / cards


DARK_KODA = Palette(
    primary="#7aa2f7",
    accent="#bb9af7",
    assistant="#c0caf5",
    user="#9ece6a",
    tool="#e0af68",
    tool_ok="#73daca",
    tool_err="#f7768e",
    error="#f7768e",
    muted="#565f89",
    background="#1a1b26",
    surface="#24283b",
)

TOKYO_NIGHT = DARK_KODA  # alias

DRACULA = Palette(
    primary="#bd93f9",
    accent="#ff79c6",
    assistant="#f8f8f2",
    user="#50fa7b",
    tool="#ffb86c",
    tool_ok="#8be9fd",
    tool_err="#ff5555",
    error="#ff5555",
    muted="#6272a4",
    background="#282a36",
    surface="#44475a",
)

SOLARIZED_DARK = Palette(
    primary="#268bd2",
    accent="#d33682",
    assistant="#93a1a1",
    user="#859900",
    tool="#b58900",
    tool_ok="#2aa198",
    tool_err="#dc322f",
    error="#dc322f",
    muted="#586e75",
    background="#002b36",
    surface="#073642",
)

THEMES: dict[str, Palette] = {
    "koda": DARK_KODA,
    "tokyo-night": TOKYO_NIGHT,
    "dracula": DRACULA,
    "solarized-dark": SOLARIZED_DARK,
}

DEFAULT_THEME = "koda"


def get(name: str | None) -> Palette:
    return THEMES.get(name or DEFAULT_THEME, DARK_KODA)
