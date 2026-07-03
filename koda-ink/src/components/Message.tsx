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

// The two subagent-spawning tools. Both take {description, subagent_type} and
// dispatch a specialist agent — the only difference is whether they block the
// main agent (inline `task`) or run detached (`start_async_task`, which also
// shows up in /dashboard). We render both with ONE card style so they read as
// the same kind of thing in the transcript.
const SUBAGENT_TOOLS: Record<string, "inline" | "background"> = {
  task: "inline",
  start_async_task: "background",
};

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function clip(s: string, n: number): string {
  s = s.replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// Shared subagent block for both the blocking `task` tool and the async
// `start_async_task` tool. Same glyph, label, and layout; a trailing badge is
// the only tell for inline vs background.
function SubagentCard({
  item,
  kind,
  palette,
}: {
  item: Extract<Item, { kind: "tool" }>;
  kind: "inline" | "background";
  palette: Palette;
}) {
  const type = str(item.args?.subagent_type) || "general-purpose";
  const desc = clip(str(item.args?.description), 74);
  const headColor = item.isError ? palette.toolErr : item.running ? palette.tool : palette.toolOk;
  const glyph = item.running ? "◐" : item.isError ? "✗" : "◇";
  const taskId = /task_id:\s*(\S+)/.exec(item.output ?? "")?.[1];

  // Second line: what happened. Background never blocks, so it reports that the
  // main agent stays free; inline shows the subagent's returned report.
  let status: React.ReactNode;
  if (item.isError) {
    status = <Text color={palette.toolErr}> ↳ {preview(item.output ?? "")}</Text>;
  } else if (kind === "background") {
    status = (
      <Text color={palette.muted}>
        {" ↳ running in background — main agent is free"}
        {taskId ? <Text color={palette.accent}> · {taskId}</Text> : null}
        <Text color={palette.muted}> · /dashboard</Text>
      </Text>
    );
  } else if (item.running) {
    status = <Text color={palette.muted}> ↳ working…</Text>;
  } else {
    status = <Text color={palette.muted}> ↳ {preview(item.output ?? "")}</Text>;
  }

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text color={headColor}>
        {glyph} subagent <Text color={palette.muted}>·</Text> {type}
        <Text color={kind === "background" ? palette.accent : palette.muted}>
          {kind === "background" ? "  [background → /dashboard]" : "  [inline]"}
        </Text>
      </Text>
      {desc ? <Text color={palette.assistant}> ↳ {desc}</Text> : null}
      {status}
    </Box>
  );
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
      // Blocking `task` and async `start_async_task` share one subagent card so
      // they read identically in the transcript (async also lives in /dashboard).
      const subKind = SUBAGENT_TOOLS[item.name];
      if (subKind) return <SubagentCard item={item} kind={subKind} palette={palette} />;

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
