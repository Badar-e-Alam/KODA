// Ported from koda/tui/modes.py — agent operating modes cycled with Shift+Tab.

export type Mode = "default" | "edits" | "plan";

export const ORDER: Mode[] = ["default", "edits", "plan"];

export function nextMode(current: Mode): Mode {
  const i = ORDER.indexOf(current);
  return ORDER[(i + 1) % ORDER.length];
}

export interface ModeStyle {
  label: string;
  color: string;
}

export const STYLES: Record<Mode, ModeStyle> = {
  default: { label: "DEFAULT", color: "#fb923c" },
  edits: { label: "ACCEPT-EDITS", color: "#84a86b" },
  plan: { label: "PLAN", color: "#b48ac4" },
};

export function styleFor(mode: Mode): ModeStyle {
  return STYLES[mode] ?? STYLES.default;
}
