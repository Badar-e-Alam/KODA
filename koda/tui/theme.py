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


# Palette lifted from the KODA TUI redesign reference (true-black bg,
# warm-white foreground, orange accent, muted tagline greys). Hex values
# correspond 1:1 to the `:root` CSS variables in the design HTML:
#   --bg #000000   --bg-soft #0c0c0c   --fg #e8e6e1   --fg-3 #8a8780
#   --accent #fb923c   --accent-2 #c2410c
#   --ok #84a86b   --err #d97474
DARK_KODA = Palette(
    primary="#ffffff",   # --fg  (banner / headings) — pure white text
    accent="#fb923c",    # --accent  (orange highlight)
    assistant="#ffffff", # --fg  — pure white assistant/body text
    user="#84a86b",      # --ok  (green border for user msg)
    tool="#fb923c",      # --accent  (orange for tool headers)
    tool_ok="#84a86b",   # --ok
    tool_err="#d97474",  # --err
    error="#d97474",     # --err
    muted="#8a8780",     # --fg-3
    background="#000000",# --bg
    surface="#0c0c0c",   # --bg-soft
)

# Tokyo Night kept as its own palette (was previously an alias to DARK_KODA;
# the redesign moved DARK_KODA off the Tokyo Night colors so we restore the
# original blues here for users who explicitly pick "tokyo-night").
TOKYO_NIGHT = Palette(
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


def to_textual_theme(name: str):
    """Return a textual.theme.Theme object representing one of our palettes.

    Custom variables (user, tool, tool-ok, tool-err, muted, assistant) are
    exposed via ``Theme.variables`` so the app CSS can reference them with
    ``$user``, ``$tool``, etc.
    """
    from textual.theme import Theme

    p = get(name)
    return Theme(
        name=name,
        primary=p.primary,
        accent=p.accent,
        background=p.background,
        surface=p.surface,
        foreground=p.assistant,
        error=p.error,
        success=p.user,
        warning=p.tool,
        dark=True,
        variables={
            "user": p.user,
            "tool": p.tool,
            "tool-ok": p.tool_ok,
            "tool-err": p.tool_err,
            "muted": p.muted,
            "assistant": p.assistant,
        },
    )
