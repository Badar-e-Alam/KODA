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
