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

// Small labelled key-hint chip, e.g. "← open". Keeps the footer legend visually
// consistent between the list and detail views.
function Hint({ k, label, palette }: { k: string; label: string; palette: Palette }) {
  return (
    <Text>
      <Text color={palette.accent}>{k}</Text>
      <Text color={palette.muted}> {label}  </Text>
    </Text>
  );
}

// On-demand near-full-screen task manager. Two views that share one selection:
//   • LIST   — every subagent as a compact row; ↑/↓ moves, ← opens the selected.
//   • DETAIL — exactly one subagent, fully expanded (one at a time); → closes it.
// This master/detail split keeps the list scannable while giving an opened agent
// the whole panel for its tool trail and output.
export function Dashboard({ tasks, palette, rows, onClose, onControl }: Props) {
  const [sel, setSel] = useState(0);
  const [open, setOpen] = useState(false);
  const idx = tasks.length ? Math.min(sel, tasks.length - 1) : 0;
  const opened = open && tasks.length > 0; // never "open" an empty list
  const t = tasks.length ? tasks[idx] : undefined;

  const move = (d: number) => setSel((s) => Math.max(0, Math.min(tasks.length - 1, s + d)));
  // State-aware controls: stop only while active; resume only once finished
  // (resuming a RUNNING task would restart its run); restart always.
  const control = (input: string) => {
    if (!t) return;
    if (input === "s" && isActive(t.state)) onControl("stop", t.id);
    else if (input === "r" && !isActive(t.state)) onControl("resume", t.id);
    else if (input === "R") onControl("restart", t.id);
  };

  useInput((input, key) => {
    if (opened) {
      // Detail view: → (or esc) closes back to the list; ↑/↓ still switches
      // which agent is open, so you can page through them one at a time.
      if (key.rightArrow || key.escape || input === "q") setOpen(false);
      else if (key.upArrow || input === "k") move(-1);
      else if (key.downArrow || input === "j") move(1);
      else control(input);
      return;
    }
    // List view: q/esc leaves the dashboard; ← (or enter) opens the selection.
    if (key.escape || input === "q") onClose();
    else if (key.upArrow || input === "k") move(-1);
    else if (key.downArrow || input === "j") move(1);
    else if (key.leftArrow || key.return) {
      if (tasks.length) setOpen(true);
    } else control(input);
  });

  // ── DETAIL VIEW ─────────────────────────────────────────────────────
  if (opened && t) {
    const col = STATE_COLOR[t.state](palette);
    const previewLines = Math.max(4, rows - 18);
    return (
      <Box flexDirection="column" borderStyle="round" borderColor={palette.accent} paddingX={1}>
        <Text>
          <Text color={palette.primary} bold>
            {STATE_ICON[t.state]} {t.id}
          </Text>
          <Text color={palette.muted}>
            {"  "}
            {idx + 1}/{tasks.length} · {t.subagent_type}
          </Text>
        </Text>
        <Text color={palette.muted}>
          <Hint k="→" label="close" palette={palette} />
          <Hint k="↑/↓" label="prev/next agent" palette={palette} />
          {isActive(t.state) ? <Hint k="s" label="stop" palette={palette} /> : <Hint k="r" label="resume" palette={palette} />}
          <Hint k="R" label="restart" palette={palette} />
          <Hint k="q" label="close" palette={palette} />
        </Text>

        <Box marginTop={1}>
          <Text color={col} bold>
            {STATE_LABEL[t.state]}
          </Text>
          <Text color={palette.muted}>
            {"   "}
            {t.tool_count} tools · {clock(t.elapsed)} elapsed
            {t.awaiting_permission ? <Text color={palette.accent}>  ⚠ needs approval</Text> : null}
          </Text>
        </Box>

        <Box marginTop={1}>
          <Text color={palette.muted}>task  </Text>
          <Box flexGrow={1}>
            <Text color={palette.assistant} wrap="wrap">
              {t.description}
            </Text>
          </Box>
        </Box>

        <Box marginTop={1}>
          <Text color={palette.muted}>now   </Text>
          <Text color={palette.assistant}>
            {t.current}
            {t.error ? <Text color={palette.toolErr}> — {t.error}</Text> : null}
          </Text>
        </Box>

        {t.recent_tools?.length ? (
          <Box>
            <Text color={palette.muted}>tools </Text>
            <Box flexGrow={1}>
              <Text color={palette.tool} wrap="truncate-end">
                {t.recent_tools.join(" → ")}
              </Text>
            </Box>
          </Box>
        ) : null}

        {t.preview ? (
          <Box flexDirection="column" marginTop={1} borderStyle="round" borderColor={palette.muted} paddingX={1}>
            <Text color={palette.muted}>output</Text>
            {t.preview
              .split("\n")
              .slice(-previewLines)
              .map((ln, li) => (
                <Text key={li} color={palette.assistant} wrap="truncate-end">
                  {ln || " "}
                </Text>
              ))}
          </Box>
        ) : (
          <Box marginTop={1}>
            <Text color={palette.muted}>(no output yet)</Text>
          </Box>
        )}
      </Box>
    );
  }

  // ── LIST VIEW ───────────────────────────────────────────────────────
  // Two lines per row (status + description). Scroll a window around the
  // selection so it stays visible however many tasks there are.
  const perRow = 2;
  const capacity = Math.max(1, Math.floor((rows - 6) / perRow));
  let start = 0;
  if (tasks.length > capacity) {
    start = Math.min(Math.max(0, idx - Math.floor(capacity / 2)), tasks.length - capacity);
  }
  const visible = tasks.slice(start, start + capacity);
  const above = start;
  const below = tasks.length - (start + visible.length);

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={palette.accent} paddingX={1}>
      <Text color={palette.primary} bold>
        KODA · Background subagents ({tasks.length})
      </Text>
      <Text color={palette.muted}>
        <Hint k="↑/↓" label="select agent" palette={palette} />
        <Hint k="←" label="open" palette={palette} />
        <Hint k="s" label="stop" palette={palette} />
        <Hint k="r" label="resume" palette={palette} />
        <Hint k="R" label="restart" palette={palette} />
        <Hint k="q" label="close" palette={palette} />
      </Text>

      <Box flexDirection="column" marginTop={1}>
        {tasks.length === 0 ? (
          <Text color={palette.muted}>No background tasks yet. The agent starts them with start_async_task.</Text>
        ) : (
          <>
            {above > 0 ? <Text color={palette.muted}>{`  ↑ ${above} more above`}</Text> : null}
            {visible.map((task, i) => {
              const active = start + i === idx;
              const col = STATE_COLOR[task.state](palette);
              return (
                <Box key={task.id} flexDirection="column">
                  <Text color={active ? palette.accent : palette.assistant} inverse={active}>
                    {active ? "❯ " : "  "}
                    <Text color={col}>
                      {STATE_ICON[task.state]} {STATE_LABEL[task.state].padEnd(8)}
                    </Text>{" "}
                    {task.id} · {task.subagent_type} · {task.tool_count} tools · {clock(task.elapsed)}
                    {task.awaiting_permission ? <Text color={palette.accent}>  ⚠ needs approval</Text> : null}
                    {active ? <Text color={palette.muted}>   ← open</Text> : null}
                  </Text>
                  <Text color={palette.muted}>
                    {"    "}
                    {task.description.slice(0, 74)}
                  </Text>
                </Box>
              );
            })}
            {below > 0 ? <Text color={palette.muted}>{`  ↓ ${below} more below`}</Text> : null}
          </>
        )}
      </Box>
    </Box>
  );
}
