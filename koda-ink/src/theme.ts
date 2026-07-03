// Ported from koda/tui/theme.py — the same curated dark palettes.
// Ink accepts hex color strings directly (via chalk), so roles map 1:1.

export interface Palette {
  primary: string; // headings, banner
  accent: string; // highlights
  assistant: string; // body text
  user: string; // user message
  tool: string; // tool header
  toolOk: string; // tool result ok
  toolErr: string; // tool result error
  error: string;
  muted: string; // dim text
}

export const THEMES: Record<string, Palette> = {
  koda: {
    primary: "#ffffff",
    accent: "#fb923c",
    assistant: "#e8e6e1",
    user: "#84a86b",
    tool: "#fb923c",
    toolOk: "#84a86b",
    toolErr: "#d97474",
    error: "#d97474",
    muted: "#8a8780",
  },
  "tokyo-night": {
    primary: "#7aa2f7",
    accent: "#bb9af7",
    assistant: "#c0caf5",
    user: "#9ece6a",
    tool: "#e0af68",
    toolOk: "#73daca",
    toolErr: "#f7768e",
    error: "#f7768e",
    muted: "#565f89",
  },
  dracula: {
    primary: "#bd93f9",
    accent: "#ff79c6",
    assistant: "#f8f8f2",
    user: "#50fa7b",
    tool: "#ffb86c",
    toolOk: "#8be9fd",
    toolErr: "#ff5555",
    error: "#ff5555",
    muted: "#6272a4",
  },
  "solarized-dark": {
    primary: "#268bd2",
    accent: "#d33682",
    assistant: "#93a1a1",
    user: "#859900",
    tool: "#b58900",
    toolOk: "#2aa198",
    toolErr: "#dc322f",
    error: "#dc322f",
    muted: "#586e75",
  },
};

export const DEFAULT_THEME = "koda";

export function getTheme(name: string | undefined): Palette {
  return THEMES[name ?? DEFAULT_THEME] ?? THEMES.koda;
}
