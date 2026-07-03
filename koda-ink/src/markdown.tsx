// Lightweight streaming-safe Markdown → Ink renderer.
//
// Deliberately dependency-free and tolerant of half-finished input (we render
// partial text every frame while the assistant streams). It handles the
// constructs that show up in coding-agent replies: headings, fenced code,
// lists, blockquotes, rules, and inline **bold** / *italic* / `code` / links.

import React from "react";
import { Text } from "ink";
import type { Palette } from "./theme.js";

interface Props {
  text: string;
  palette: Palette;
}

// ── inline spans ────────────────────────────────────────────────────────

type Span = { text: string; bold?: boolean; italic?: boolean; code?: boolean; url?: boolean };

function parseInline(line: string): Span[] {
  const spans: Span[] = [];
  let i = 0;
  let buf = "";
  const flush = () => {
    if (buf) {
      spans.push({ text: buf });
      buf = "";
    }
  };

  while (i < line.length) {
    const rest = line.slice(i);

    // `inline code`
    if (line[i] === "`") {
      const end = line.indexOf("`", i + 1);
      if (end > i) {
        flush();
        spans.push({ text: line.slice(i + 1, end), code: true });
        i = end + 1;
        continue;
      }
    }

    // **bold**
    if (rest.startsWith("**")) {
      const end = line.indexOf("**", i + 2);
      if (end > i + 1) {
        flush();
        spans.push({ text: line.slice(i + 2, end), bold: true });
        i = end + 2;
        continue;
      }
    }

    // *italic* or _italic_ — require the delimiters to sit on a word boundary
    // so snake_case identifiers (tool_start, file_name) and a*b are NOT eaten.
    if ((line[i] === "*" || line[i] === "_") && line[i + 1] !== line[i]) {
      const ch = line[i];
      const prev = i === 0 ? " " : line[i - 1];
      const openOk = /[\s([{"'*_>-]/.test(prev) || i === 0;
      if (openOk && line[i + 1] !== " ") {
        // find a closing delimiter that is itself followed by a boundary
        let end = -1;
        for (let j = i + 1; j < line.length; j++) {
          if (line[j] === ch && line[j - 1] !== " ") {
            const nxt = j + 1 >= line.length ? " " : line[j + 1];
            if (/[\s)\]}"'.,;:!?*_-]/.test(nxt)) {
              end = j;
              break;
            }
          }
        }
        if (end > i) {
          flush();
          spans.push({ text: line.slice(i + 1, end), italic: true });
          i = end + 1;
          continue;
        }
      }
    }

    // [label](url)
    if (line[i] === "[") {
      const close = line.indexOf("]", i + 1);
      if (close > i && line[close + 1] === "(") {
        const paren = line.indexOf(")", close + 2);
        if (paren > close) {
          flush();
          spans.push({ text: line.slice(i + 1, close), url: true });
          i = paren + 1;
          continue;
        }
      }
    }

    // bare URL
    const urlMatch = /^(https?:\/\/[^\s)<>\]]+)/.exec(rest);
    if (urlMatch) {
      flush();
      spans.push({ text: urlMatch[1], url: true });
      i += urlMatch[1].length;
      continue;
    }

    buf += line[i];
    i += 1;
  }
  flush();
  return spans;
}

function InlineText({ line, palette }: { line: string; palette: Palette }) {
  const spans = parseInline(line);
  return (
    <Text color={palette.assistant}>
      {spans.map((s, idx) => {
        if (s.code) return <Text key={idx} color={palette.toolOk}>{s.text}</Text>;
        if (s.url) return <Text key={idx} color={palette.accent} underline>{s.text}</Text>;
        return (
          <Text key={idx} bold={s.bold} italic={s.italic}>
            {s.text}
          </Text>
        );
      })}
    </Text>
  );
}

// ── tables ────────────────────────────────────────────────────────────────
// GitHub-style pipe tables are detected as a header row immediately followed by
// a separator row (|---|:--:|). Cells are word-wrapped to a per-column width so
// the whole grid fits the terminal instead of overflowing, and rendered with
// box-drawing rules between every row (matching a "grid" table look).

type Align = "left" | "center" | "right";

function isTableSeparator(line: string): boolean {
  // e.g.  | --- | :--: | ---: |   or   ---|---
  return /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(line) && line.includes("-");
}

function isTableRow(line: string): boolean {
  return line.includes("|") && line.trim() !== "";
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

function wrapCell(text: string, width: number): string[] {
  const s = text.replace(/\s+/g, " ").trim();
  if (s === "") return [""];
  const words = s.split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const word of words) {
    if (word.length > width) {
      if (cur) {
        lines.push(cur);
        cur = "";
      }
      let rest = word;
      while (rest.length > width) {
        lines.push(rest.slice(0, width));
        rest = rest.slice(width);
      }
      cur = rest;
    } else if ((cur ? cur.length + 1 : 0) + word.length <= width) {
      cur = cur ? cur + " " + word : word;
    } else {
      lines.push(cur);
      cur = word;
    }
  }
  if (cur) lines.push(cur);
  return lines.length ? lines : [""];
}

// Fit natural column widths into `avail` using max-min (water-filling)
// allocation: columns narrower than their fair share keep their full width, and
// only the genuinely wide columns get shrunk to share the leftover space. This
// keeps fixed-width columns (ids, hashes, numbers) intact and wraps the prose.
function fitWidths(natural: number[], avail: number): number[] {
  const n = natural.length;
  if (natural.reduce((a, b) => a + b, 0) <= avail) return natural.slice();
  const MINW = 3;
  const widths = natural.slice();
  const fixed = new Array<boolean>(n).fill(false);
  let remaining = avail;
  while (true) {
    const flex: number[] = [];
    for (let i = 0; i < n; i++) if (!fixed[i]) flex.push(i);
    if (flex.length === 0) break;
    const fair = Math.max(MINW, Math.floor(remaining / flex.length));
    const small = flex.filter((i) => natural[i] <= fair);
    if (small.length === 0) {
      // Every remaining column is wider than its fair share — split evenly.
      let r = remaining;
      for (let j = 0; j < flex.length; j++) {
        const w = Math.max(MINW, Math.floor(r / (flex.length - j)));
        widths[flex[j]] = w;
        r -= w;
      }
      break;
    }
    for (const i of small) {
      widths[i] = natural[i];
      fixed[i] = true;
      remaining -= natural[i];
    }
    if (remaining <= MINW * (n - small.length)) {
      for (let i = 0; i < n; i++) if (!fixed[i]) widths[i] = MINW;
      break;
    }
  }
  return widths;
}

function pad(text: string, width: number, align: Align): string {
  if (text.length >= width) return text.slice(0, width);
  const space = width - text.length;
  if (align === "right") return " ".repeat(space) + text;
  if (align === "center") {
    const l = Math.floor(space / 2);
    return " ".repeat(l) + text + " ".repeat(space - l);
  }
  return text + " ".repeat(space);
}

// Turn a contiguous block of table lines (header, separator, …body) into an
// array of Ink line nodes.
function renderTable(block: string[], palette: Palette, keyBase: number): React.ReactNode[] {
  const header = splitRow(block[0]);
  const sepCells = splitRow(block[1]);
  const nCol = header.length;
  const aligns: Align[] = Array.from({ length: nCol }, (_, c) => {
    const cell = (sepCells[c] ?? "").trim();
    const l = cell.startsWith(":");
    const r = cell.endsWith(":");
    return l && r ? "center" : r ? "right" : "left";
  });

  const bodyRows = block.slice(2).map((l) => {
    const cells = splitRow(l);
    return Array.from({ length: nCol }, (_, c) => cells[c] ?? "");
  });
  const allRows = [header, ...bodyRows];

  // Natural (unwrapped) column widths, then shrink to the terminal if needed.
  const natural = Array.from({ length: nCol }, (_, c) =>
    Math.max(1, ...allRows.map((r) => (r[c] ?? "").length)),
  );
  const cols = process.stdout.columns || 80;
  const budget = Math.max(20, cols - 1);
  const overhead = 3 * nCol + 1; // "│" + per-col " x " + inner/outer "│"
  const avail = Math.max(nCol * 3, budget - overhead);
  const widths = fitWidths(natural, avail);

  const muted = palette.muted;
  const rule = (l: string, m: string, r: string): string =>
    l + widths.map((w) => "─".repeat(w + 2)).join(m) + r;

  const nodes: React.ReactNode[] = [];
  let k = keyBase;

  // One visual line of a row (cells already sliced to their wrapped line).
  const contentLine = (cells: string[], header: boolean) => {
    const parts: React.ReactNode[] = [<Text key="b0" color={muted}>│</Text>];
    for (let c = 0; c < nCol; c++) {
      const padded = pad(cells[c] ?? "", widths[c], aligns[c]);
      parts.push(
        <Text key={`c${c}`} color={header ? palette.primary : palette.assistant} bold={header}>
          {" " + padded + " "}
        </Text>,
      );
      parts.push(<Text key={`b${c + 1}`} color={muted}>│</Text>);
    }
    return <Text key={k++}>{parts}</Text>;
  };

  const emitRow = (row: string[], isHeader: boolean) => {
    const wrapped = row.map((cell, c) => wrapCell(cell, widths[c]));
    const height = Math.max(1, ...wrapped.map((w) => w.length));
    for (let li = 0; li < height; li++) {
      nodes.push(contentLine(wrapped.map((w) => w[li] ?? ""), isHeader));
    }
  };

  nodes.push(<Text key={k++} color={muted}>{rule("┌", "┬", "┐")}</Text>);
  emitRow(header, true);
  nodes.push(<Text key={k++} color={muted}>{rule("├", "┼", "┤")}</Text>);
  bodyRows.forEach((row, i) => {
    emitRow(row, false);
    nodes.push(
      <Text key={k++} color={muted}>
        {rule(i === bodyRows.length - 1 ? "└" : "├", i === bodyRows.length - 1 ? "┴" : "┼", i === bodyRows.length - 1 ? "┘" : "┤")}
      </Text>,
    );
  });
  if (bodyRows.length === 0) nodes.push(<Text key={k++} color={muted}>{rule("└", "┴", "┘")}</Text>);
  return nodes;
}

// ── block renderer ──────────────────────────────────────────────────────

export function Markdown({ text, palette }: Props) {
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  let inFence = false;
  let key = 0;

  for (let n = 0; n < lines.length; n++) {
    const line = lines[n];

    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      continue; // hide the fence markers themselves
    }
    if (inFence) {
      out.push(
        <Text key={key++} color={palette.toolOk}>
          {"  │ " + line}
        </Text>,
      );
      continue;
    }

    // Pipe table: a header row directly followed by a |---|--- separator. Only
    // treated as a table once the separator has streamed in — until then the
    // header renders as an ordinary line, then snaps into the grid.
    if (isTableRow(line) && n + 1 < lines.length && isTableSeparator(lines[n + 1])) {
      const block = [line, lines[n + 1]];
      let m = n + 2;
      while (m < lines.length && isTableRow(lines[m]) && !isTableSeparator(lines[m])) {
        block.push(lines[m]);
        m++;
      }
      for (const node of renderTable(block, palette, key)) out.push(node);
      key += block.length * 4 + 8; // keep keys unique past the block's line nodes
      n = m - 1;
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      out.push(
        <Text key={key++} bold color={palette.primary}>
          {heading[2]}
        </Text>,
      );
      continue;
    }

    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      out.push(
        <Text key={key++} color={palette.muted}>
          {"─".repeat(40)}
        </Text>,
      );
      continue;
    }

    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      out.push(
        <Text key={key++} italic color={palette.muted}>
          {"  ▏ " + quote[1]}
        </Text>,
      );
      continue;
    }

    const bullet = /^(\s*)([-*+])\s+(.*)$/.exec(line);
    if (bullet) {
      out.push(
        <Text key={key++}>
          <Text color={palette.accent}>{bullet[1] + "• "}</Text>
          <InlineTextInline line={bullet[3]} palette={palette} />
        </Text>,
      );
      continue;
    }

    const numbered = /^(\s*)(\d+)\.\s+(.*)$/.exec(line);
    if (numbered) {
      out.push(
        <Text key={key++}>
          <Text color={palette.accent}>{numbered[1] + numbered[2] + ". "}</Text>
          <InlineTextInline line={numbered[3]} palette={palette} />
        </Text>,
      );
      continue;
    }

    out.push(<InlineText key={key++} line={line} palette={palette} />);
  }

  return <>{out}</>;
}

// Inline variant that does not wrap in its own outer <Text color> so it can be
// nested inside a bullet line's <Text>.
function InlineTextInline({ line, palette }: { line: string; palette: Palette }) {
  const spans = parseInline(line);
  return (
    <>
      {spans.map((s, idx) => {
        if (s.code) return <Text key={idx} color={palette.toolOk}>{s.text}</Text>;
        if (s.url) return <Text key={idx} color={palette.accent} underline>{s.text}</Text>;
        return (
          <Text key={idx} color={palette.assistant} bold={s.bold} italic={s.italic}>
            {s.text}
          </Text>
        );
      })}
    </>
  );
}
