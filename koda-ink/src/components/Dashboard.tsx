import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import type { TaskSummary } from "../types.js";
import type { Palette } from "../theme.js";

interface Props {
  tasks: TaskSummary[];
  palette: Palette;
  rows: number;
  onClose: () => void;
  onControl: (action: "stop" | "resume" | "restart", taskId: string) => void;
}

const STATE_COLOR: Record<TaskSummary["state"], (p: Palette) => string> = {
  queued: (p) => p.tool,
  running: (p) => p.tool,
  paused: (p) => p.accent,
  success: (p) => p.toolOk,
  error: (p) => p.toolErr,
  cancelled: (p) => p.muted,
};

// Icon + user-facing label per state. "cancelled" (wire status) reads as
// STOPPED here since the user stopped it and can resume it.
const STATE_ICON: Record<TaskSummary["state"], string> = {
  queued: "⚡",
  running: "⚡",
  paused: "⏸",
  success: "✓",
  error: "✗",
  cancelled: "■",
};

const STATE_LABEL: Record<TaskSummary["state"], string> = {
  queued: "QUEUED",
  running: "RUNNING",
  paused: "PAUSED",
  success: "DONE",
  error: "FAILED",
  cancelled: "STOPPED",
};

function isActive(s: TaskSummary["state"]): boolean {
  return s === "running" || s === "queued" || s === "paused";
}

function clock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// On-demand near-full-screen task manager. Grows to fill the terminal so Ink
// renders it as a takeover view; closing it returns to the inline transcript.
export function Dashboard({ tasks, palette, rows, onClose, onControl }: Props) {
  const [sel, setSel] = useState(0);
  const idx = tasks.length ? Math.min(sel, tasks.length - 1) : 0;

  useInput((input, key) => {
    if (key.escape || input === "q") onClose();
    else if (key.upArrow || input === "k") setSel((s) => Math.max(0, s - 1));
    else if (key.downArrow || input === "j") setSel((s) => Math.min(tasks.length - 1, s + 1));
    else if (tasks.length) {
      const t = tasks[idx];
      // State-aware: stop only makes sense while active; resume only once
      // stopped/finished (resuming a RUNNING task would restart its run).
      if (input === "s" && isActive(t.state)) onControl("stop", t.id);
      else if (input === "r" && !isActive(t.state)) onControl("resume", t.id);
      else if (input === "R") onControl("restart", t.id);
    }
  });

  // Size to content but never taller than the terminal, so the whole panel
  // (including task rows) stays on screen — a bounded on-demand manager. The
  // selected task expands (tool trail + output preview ≈ 10 lines), so budget
  // for one expanded row plus compact rows for the rest.
  const maxTasks = Math.max(1, Math.floor((rows - 15) / 3));
  const visible = tasks.slice(0, maxTasks);
  const hidden = tasks.length - visible.length;
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={palette.accent} paddingX={1}>
      <Text color={palette.primary} bold>
        KODA · Background subagents ({tasks.length})
      </Text>
      <Text color={palette.muted}>
        ↑/↓ select · ⚡ running → [s] stop · ■ stopped → [r] resume · [R] restart · q/esc close
      </Text>
      <Box flexDirection="column" marginTop={1}>
        {tasks.length === 0 ? (
          <Text color={palette.muted}>No background tasks yet. The agent starts them with start_async_task.</Text>
        ) : (
          visible.map((t, i) => {
            const active = i === idx;
            const col = STATE_COLOR[t.state](palette);
            return (
              <Box key={t.id} flexDirection="column">
                <Text color={active ? palette.accent : palette.assistant} inverse={active}>
                  {active ? "❯ " : "  "}
                  <Text color={col}>
                    {STATE_ICON[t.state]} {STATE_LABEL[t.state].padEnd(8)}
                  </Text>{" "}
                  {t.id} · {t.subagent_type} · {t.tool_count} tools · {clock(t.elapsed)}
                  {t.awaiting_permission ? <Text color={palette.accent}>  ⚠ needs approval</Text> : null}
                  {active ? (
                    <Text color={palette.muted}>
                      {isActive(t.state) ? "   [s] stop" : "   [r] ▶ resume · [R] restart"}
                    </Text>
                  ) : null}
                </Text>
                <Text color={palette.muted}>
                  {"    "}
                  {t.description.slice(0, 74)}
                </Text>
                {active ? (
                  <Box flexDirection="column">
                    <Text color={palette.muted}>
                      {"    ↳ "}
                      {t.current}
                      {t.error ? <Text color={palette.toolErr}> — {t.error}</Text> : null}
                    </Text>
                    {t.recent_tools?.length ? (
                      <Text color={palette.muted}>
                        {"    tools: "}
                        <Text color={palette.tool}>{t.recent_tools.join(" → ")}</Text>
                      </Text>
                    ) : null}
                    {t.preview ? (
                      <Box flexDirection="column" marginLeft={4} borderStyle="round" borderColor={palette.muted} paddingX={1}>
                        {t.preview
                          .split("\n")
                          .slice(-6)
                          .map((ln, li) => (
                            <Text key={li} color={palette.assistant} wrap="truncate-end">
                              {ln || " "}
                            </Text>
                          ))}
                      </Box>
                    ) : null}
                  </Box>
                ) : null}
              </Box>
            );
          })
        )}
        {hidden > 0 ? <Text color={palette.muted}>{`  …+${hidden} more`}</Text> : null}
      </Box>
    </Box>
  );
}
