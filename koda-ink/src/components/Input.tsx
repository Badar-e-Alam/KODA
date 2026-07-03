import React, { useRef, useState } from "react";
import { Box, Text, useInput } from "ink";
import type { Palette } from "../theme.js";
import { styleFor, type Mode } from "../modes.js";
import { complete, type CompleteResult } from "../completer.js";
import { Completion } from "./Completion.js";

interface Props {
  active: boolean;
  palette: Palette;
  mode: Mode;
  streaming: boolean;
  onSubmit: (text: string) => void;
  onInterrupt: () => void;
  onCycleMode: () => void;
  onExit: () => void;
}

function detectMode(value: string): "chat" | "shell" | "command" {
  if (value.startsWith("!")) return "shell";
  if (value.startsWith("/")) return "command";
  return "chat";
}

// The input box grows one row per line up to MAX_INPUT_ROWS, then holds that
// height and scrolls to keep the cursor visible — so a big paste expands a bit
// and then becomes scrollable instead of shoving the whole transcript up.
const MAX_INPUT_ROWS = 8;

// Wrap `value` into fixed-width visual rows and locate the cursor within them.
// We wrap manually (rather than letting Ink reflow) so the rendered height is
// deterministic and we can window/scroll it. Cursor is an index in [0, len].
export function layout(
  value: string,
  cursor: number,
  width: number,
): { rows: string[]; cursorRow: number; cursorCol: number } {
  const w = Math.max(1, width);
  const rows: string[] = [];
  let cursorRow = 0;
  let cursorCol = 0;
  let found = false;
  const lines = value.split("\n");
  let abs = 0; // absolute index into `value` at the start of the current line
  for (const line of lines) {
    const nChunks = Math.max(1, Math.ceil(line.length / w));
    for (let ci = 0; ci < nChunks; ci++) {
      const start = ci * w;
      const text = line.slice(start, start + w);
      const rowIndex = rows.length;
      rows.push(text);
      const rowStart = abs + start;
      const rowEnd = rowStart + text.length; // exclusive of any trailing '\n'
      if (!found && cursor >= rowStart && cursor <= rowEnd) {
        // At a mid-line wrap boundary the cursor belongs to the NEXT row's col 0.
        if (cursor === rowEnd && ci < nChunks - 1) {
          // defer to the next chunk
        } else {
          cursorRow = rowIndex;
          cursorCol = cursor - rowStart;
          found = true;
        }
      }
    }
    abs += line.length + 1; // +1 for the '\n' that split() removed
  }
  if (!found) {
    cursorRow = Math.max(0, rows.length - 1);
    cursorCol = rows[cursorRow]?.length ?? 0;
  }
  return { rows, cursorRow, cursorCol };
}

export function Input({ active, palette, mode, streaming, onSubmit, onInterrupt, onCycleMode, onExit }: Props) {
  const [value, setValue] = useState("");
  const [cursor, setCursor] = useState(0);
  const [popupIdx, setPopupIdx] = useState(0);
  const [popupDismissed, setPopupDismissed] = useState(false);
  const history = useRef<string[]>([]);
  const histIdx = useRef<number | null>(null);
  // Bracketed-paste accumulator (cli.tsx enables mode 2004). Non-null while a
  // paste is in flight — large pastes arrive split across ~1KB PTY chunks, and
  // without buffering a chunk ending in \n would auto-submit partial content.
  const pasteBuf = useRef<string | null>(null);

  const comp: CompleteResult | null = active ? complete(value, cursor) : null;
  const popupVisible = !popupDismissed && !!comp && comp.suggestions.length > 0;
  const n = comp?.suggestions.length ?? 0;
  const idx = n ? Math.min(popupIdx, n - 1) : 0;

  function edit(nextValue: string, nextCursor: number) {
    setValue(nextValue);
    setCursor(Math.max(0, Math.min(nextCursor, nextValue.length)));
    setPopupIdx(0);
    setPopupDismissed(false);
    histIdx.current = null;
  }

  function applyInsert(c: CompleteResult): { value: string; cursor: number } {
    const [start, end] = c.range;
    const s = c.suggestions[idx];
    const nv = value.slice(0, start) + s.insert + value.slice(end);
    return { value: nv, cursor: start + s.insert.length };
  }

  function submit(text: string) {
    const t = text.trim();
    if (t) {
      history.current.push(t);
      histIdx.current = null;
    }
    setValue("");
    setCursor(0);
    setPopupIdx(0);
    setPopupDismissed(false);
    if (t) onSubmit(t);
  }

  useInput(
    (input, key) => {
      // Bracketed paste (ESC[200~ … ESC[201~). Ink strips the leading ESC, so
      // the start marker arrives as "[200~"; the end marker keeps its inner
      // ESC. Buffer chunks until the end marker, then INSERT the content —
      // never auto-submit a paste (matches Claude Code; prevents partial-paste
      // submits when the terminal splits a big paste across reads).
      const PASTE_START = /(?:\x1b)?\[200~/;
      const PASTE_END = /(?:\x1b)?\[201~/;
      if (pasteBuf.current !== null || (input && PASTE_START.test(input))) {
        const combined = (pasteBuf.current ?? "") + (input ?? "");
        if (!PASTE_END.test(combined)) {
          pasteBuf.current = combined;
          return;
        }
        pasteBuf.current = null;
        let content = combined.replace(PASTE_START, "").replace(PASTE_END, "");
        content = content.replace(/\r\n?/g, "\n").replace(/\x1b/g, "").replace(/\n$/, "");
        if (content) {
          edit(value.slice(0, cursor) + content + value.slice(cursor), cursor + content.length);
        }
        return;
      }

      // Shift+Tab → cycle agent mode
      if (key.tab && key.shift) {
        onCycleMode();
        return;
      }

      if (key.escape) {
        if (popupVisible) {
          setPopupDismissed(true);
          return;
        }
        onInterrupt();
        return;
      }

      if (key.ctrl && input === "c") {
        if (value) {
          setValue("");
          setCursor(0);
        } else {
          onInterrupt();
        }
        return;
      }
      if (key.ctrl && input === "d") {
        if (!value) onExit();
        return;
      }

      // Up/Down: navigate popup, else input history.
      if (key.upArrow) {
        if (popupVisible) {
          setPopupIdx((i) => (i + n - 1) % n);
          return;
        }
        const h = history.current;
        if (h.length === 0) return;
        histIdx.current = histIdx.current === null ? h.length - 1 : Math.max(0, histIdx.current - 1);
        const v = h[histIdx.current];
        setValue(v);
        setCursor(v.length);
        return;
      }
      if (key.downArrow) {
        if (popupVisible) {
          setPopupIdx((i) => (i + 1) % n);
          return;
        }
        if (histIdx.current === null) return;
        histIdx.current += 1;
        if (histIdx.current >= history.current.length) {
          histIdx.current = null;
          setValue("");
          setCursor(0);
        } else {
          const v = history.current[histIdx.current];
          setValue(v);
          setCursor(v.length);
        }
        return;
      }

      // Tab → accept highlighted suggestion.
      if (key.tab) {
        if (popupVisible && comp) {
          const { value: nv, cursor: nc } = applyInsert(comp);
          edit(nv, nc);
        }
        return;
      }

      // Enter → accept suggestion (and maybe submit) / newline / submit.
      if (key.return) {
        if (popupVisible && comp) {
          const s = comp.suggestions[idx];
          const { value: nv, cursor: nc } = applyInsert(comp);
          if (s.insert.endsWith(" ")) {
            edit(nv, nc); // command expects an argument — stay
            return;
          }
          submit(nv);
          return;
        }
        if (value.endsWith("\\")) {
          edit(value.slice(0, -1) + "\n", cursor);
          return;
        }
        submit(value);
        return;
      }

      // Cursor movement.
      if (key.leftArrow) {
        setCursor((c) => Math.max(0, c - 1));
        return;
      }
      if (key.rightArrow) {
        setCursor((c) => Math.min(value.length, c + 1));
        return;
      }
      if (key.ctrl && input === "a") {
        setCursor(0);
        return;
      }
      if (key.ctrl && input === "e") {
        setCursor(value.length);
        return;
      }

      // Backspace / delete.
      if (key.backspace || key.delete) {
        if (cursor > 0) {
          edit(value.slice(0, cursor - 1) + value.slice(cursor), cursor - 1);
        }
        return;
      }

      // Printable text (incl. multi-char paste). A pasted chunk can carry
      // newlines: a trailing newline means "submit"; internal newlines are
      // kept as a multiline prompt. (Interactive typing sends Enter as its own
      // key.return event, handled above — this path only matters for paste and
      // terminals that batch a line + Enter into one chunk.)
      if (input && !key.ctrl && !key.meta) {
        if (/[\r\n]/.test(input)) {
          const trailingEnter = /[\r\n]$/.test(input);
          let body = input.replace(/\r\n?/g, "\n");
          if (trailingEnter) body = body.replace(/\n$/, "");
          const nv = value.slice(0, cursor) + body + value.slice(cursor);
          if (trailingEnter) {
            submit(nv);
          } else {
            edit(nv, cursor + body.length);
          }
          return;
        }
        edit(value.slice(0, cursor) + input + value.slice(cursor), cursor + input.length);
      }
    },
    { isActive: active },
  );

  // ── render ──────────────────────────────────────────────────────────
  const ms = styleFor(mode);
  const inputMode = detectMode(value);
  const symbol = inputMode === "shell" ? "!" : inputMode === "command" ? "/" : "›";
  const symbolColor = inputMode === "shell" ? palette.toolErr : inputMode === "command" ? palette.accent : ms.color;
  const empty = value.length === 0;

  // Wrap the value to the available width (minus the 2-col prompt gutter), then
  // window it to MAX_INPUT_ROWS keeping the cursor row visible.
  const cols = process.stdout.columns || 80;
  const wrapWidth = Math.max(20, cols - 4);
  const { rows, cursorRow, cursorCol } = layout(value, cursor, wrapWidth);
  const total = rows.length;
  const maxStart = Math.max(0, total - MAX_INPUT_ROWS);
  let startRow = Math.min(cursorRow, maxStart);
  if (cursorRow >= startRow + MAX_INPUT_ROWS) startRow = cursorRow - MAX_INPUT_ROWS + 1;
  startRow = Math.max(0, Math.min(startRow, maxStart));
  const visibleCount = Math.min(MAX_INPUT_ROWS, total);
  const visible = rows.slice(startRow, startRow + visibleCount);
  const hiddenAbove = startRow;
  const hiddenBelow = total - (startRow + visibleCount);
  const borderColor = active ? symbolColor : palette.muted;

  return (
    <Box flexDirection="column">
      {popupVisible && comp ? (
        <Completion title={comp.title} suggestions={comp.suggestions} index={idx} palette={palette} />
      ) : null}
      {/* Top + bottom rules bracket the input so the typing area is always
          visible; the box grows with content up to MAX_INPUT_ROWS, then scrolls. */}
      <Box
        flexDirection="column"
        width="100%"
        borderStyle="round"
        borderColor={borderColor}
        borderTop
        borderBottom
        borderLeft={false}
        borderRight={false}
        paddingX={1}
      >
        {empty && !streaming ? (
          <Box>
            <Text color={symbolColor} bold>
              {symbol}{" "}
            </Text>
            <Text color={palette.muted}>Ask KODA anything…  (/ commands · @ files · ! shell)</Text>
          </Box>
        ) : (
          visible.map((rowText, i) => {
            const absRow = startRow + i;
            const first = absRow === 0;
            const onCursorRow = active && absRow === cursorRow;
            const b = rowText.slice(0, cursorCol);
            const atCh = rowText.slice(cursorCol, cursorCol + 1) || " ";
            const af = rowText.slice(cursorCol + 1);
            return (
              <Box key={i}>
                <Text color={first ? symbolColor : palette.muted} bold={first}>
                  {first ? `${symbol} ` : "  "}
                </Text>
                {onCursorRow ? (
                  <Text color={palette.assistant}>
                    {b}
                    <Text inverse>{atCh}</Text>
                    {af}
                  </Text>
                ) : (
                  <Text color={palette.assistant}>{rowText || " "}</Text>
                )}
              </Box>
            );
          })
        )}
      </Box>
      {total > MAX_INPUT_ROWS ? (
        <Text color={palette.muted}>
          {`  ${total} lines · line ${cursorRow + 1}`}
          {hiddenAbove > 0 ? `  ↑${hiddenAbove}` : ""}
          {hiddenBelow > 0 ? `  ↓${hiddenBelow}` : ""}
        </Text>
      ) : null}
    </Box>
  );
}
