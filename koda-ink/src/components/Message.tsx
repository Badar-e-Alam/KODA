import React from "react";
import { Box, Text } from "ink";
import type { Item, Todo } from "../types.js";
import type { Palette } from "../theme.js";
import { Markdown } from "../markdown.js";

const TODO_GLYPH: Record<Todo["status"], string> = {
  pending: "○",
  in_progress: "◐",
  completed: "✓",
};

function formatArgs(args: Record<string, unknown>): string {
  const keys = Object.keys(args ?? {});
  if (keys.length === 0) return "";
  const pairs: string[] = [];
  for (const k of keys) {
    let v = args[k];
    if (typeof v === "string" && v.length > 40) v = v.slice(0, 37) + "…";
    let s: string;
    try {
      s = typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v);
    } catch {
      s = String(v);
    }
    pairs.push(`${k}=${s}`);
  }
  const joined = pairs.join(", ");
  return joined.length <= 80 ? joined : joined.slice(0, 79) + "…";
}

function preview(text: string): string {
  const clean = (text ?? "").trim();
  if (!clean) return "(empty)";
  const lines = clean.split("\n");
  let p = lines[0];
  if (p.length > 80) p = p.slice(0, 79) + "…";
  if (lines.length > 1) p += `  (+${lines.length - 1} lines)`;
  return p;
}

export function MessageView({ item, palette }: { item: Item; palette: Palette }) {
  switch (item.kind) {
    case "user":
      return (
        <Box marginTop={1}>
          <Text color={palette.user} bold>
            {"› "}
          </Text>
          <Text color={palette.assistant}>{item.text}</Text>
        </Box>
      );

    case "assistant":
      return (
        <Box marginTop={1} flexDirection="column">
          <Markdown text={item.text} palette={palette} />
        </Box>
      );

    case "tool": {
      const headColor = item.isError ? palette.toolErr : item.running ? palette.tool : palette.toolOk;
      const glyph = item.running ? "◐" : item.isError ? "✗" : "●";
      const args = formatArgs(item.args);
      return (
        <Box flexDirection="column" marginTop={1}>
          <Text color={headColor}>
            {glyph} {item.name}
            {args ? `(${args})` : ""}
          </Text>
          {item.running ? (
            <Text color={palette.muted}> ↳ …</Text>
          ) : (
            <Text color={item.isError ? palette.toolErr : palette.muted}> ↳ {preview(item.output ?? "")}</Text>
          )}
        </Box>
      );
    }

    case "todos": {
      const done = item.todos.filter((t) => t.status === "completed").length;
      return (
        <Box flexDirection="column" marginTop={1}>
          <Text bold color={palette.primary}>
            Tasks{" "}
            <Text color={palette.muted}>
              ({done}/{item.todos.length})
            </Text>
          </Text>
          {item.todos.map((t, i) => {
            const g = TODO_GLYPH[t.status] ?? "○";
            if (t.status === "completed")
              return (
                <Text key={i} color={palette.muted} strikethrough>
                  {"  "}
                  {g} {t.content}
                </Text>
              );
            if (t.status === "in_progress")
              return (
                <Text key={i} bold color={palette.accent}>
                  {"  "}
                  {g} {t.content}
                </Text>
              );
            return (
              <Text key={i} color={palette.assistant}>
                {"  "}
                {g} {t.content}
              </Text>
            );
          })}
        </Box>
      );
    }

    case "info":
      return (
        <Text color={palette.muted}>· {item.text}</Text>
      );

    case "error":
      return (
        <Text color={palette.error}>⚠ {item.text}</Text>
      );
  }
}
