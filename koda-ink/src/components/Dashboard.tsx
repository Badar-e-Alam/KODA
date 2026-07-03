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

// Compact token count: 0, 940, 12.3k, 128k.
function fmtTok(n: number): string {
  if (!n) return "0";
  if (n < 1000) return String(n);
  const k = n / 1000;
  return (k >= 10 ? Math.round(k) : k.toFixed(1)) + "k";
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

// One subagent, fully expanded. Stable, non-streaming status (state · what it's
// doing as text · tools/tokens/seconds) above a scrollable activity log — the
// record of everything the agent has done — with the result shown only once it
// has finished (never streamed live).
export function TaskDetail({
  task: t,
  idx,
  count,
  palette,
  rows,
  logScroll,
}: {
  task: TaskSummary;
  idx: number;
  count: number;
  palette: Palette;
  rows: number;
  logScroll: number;
}) {
  const col = STATE_COLOR[t.state](palette);
  const running = isActive(t.state);
  const activity = t.activity ?? [];

  // Result (only shown once finished) gets a few lines; the rest of the
  // vertical space is the scrollable activity log, anchored to the tail.
  const resultLines = running ? 0 : 4;
  const logRows = Math.max(3, rows - 15 - resultLines);
  const total = activity.length;
  const maxScroll = Math.max(0, total - logRows);
  const scroll = Math.min(logScroll, maxScroll);
  const end = total - scroll;
  const startL = Math.max(0, end - logRows);
  const shown = activity.slice(startL, end);

  return (
    <Box flexDirection="column" borderStyle="round" borderColor={palette.accent} paddingX={1}>
      <Text>
        <Text color={palette.primary} bold>
          {STATE_ICON[t.state]} {t.id}
        </Text>
        <Text color={palette.muted}>
          {"  "}
          {idx + 1}/{count} · {t.subagent_type}
        </Text>
      </Text>
      <Text color={palette.muted}>
        <Hint k="→" label="close" palette={palette} />
        <Hint k="↑/↓" label="scroll" palette={palette} />
        {running ? <Hint k="s" label="stop" palette={palette} /> : <Hint k="r" label="resume" palette={palette} />}
        <Hint k="R" label="restart" palette={palette} />
        <Hint k="q" label="close" palette={palette} />
      </Text>

      {/* Stable status — NOT streaming: state, what it's doing (as text), and
          the numbers (tools · tokens · seconds). */}
      <Box marginTop={1}>
        <Text color={col} bold>
          {STATE_LABEL[t.state]}
        </Text>
        {t.awaiting_permission ? <Text color={palette.accent}>  ⚠ needs approval</Text> : null}
      </Box>
      <Box>
        <Text color={palette.muted}>now    </Text>
        <Text color={palette.assistant} wrap="truncate-end">
          {t.error ? <Text color={palette.toolErr}>{t.error}</Text> : running ? t.current : "finished"}
        </Text>
      </Box>
      <Box>
        <Text color={palette.muted}>stats  </Text>
        <Text color={palette.assistant}>
          {t.tool_count} tools <Text color={palette.muted}>·</Text> {fmtTok(t.input_tokens ?? 0)}/
          {fmtTok(t.output_tokens ?? 0)} tok <Text color={palette.muted}>·</Text> {Math.round(t.elapsed)}s
        </Text>
      </Box>

      <Box marginTop={1}>
        <Text color={palette.muted}>task   </Text>
        <Box flexGrow={1}>
          <Text color={palette.assistant} wrap="wrap">
            {t.description}
          </Text>
        </Box>
      </Box>

      {/* Activity — the scrollable record of everything the agent has done. */}
      <Box marginTop={1}>
        <Text color={palette.muted}>activity</Text>
        {total > logRows ? (
          <Text color={palette.muted}>
            {`  ${startL + 1}–${end}/${total}`}
            {startL > 0 ? "  ↑more" : ""}
            {end < total ? "  ↓more" : ""}
          </Text>
        ) : null}
      </Box>
      <Box flexDirection="column" borderStyle="round" borderColor={palette.muted} paddingX={1}>
        {total === 0 ? (
          <Text color={palette.muted}>{running ? "starting…" : "(no tool activity)"}</Text>
        ) : (
          shown.map((ln, i) => (
            <Text
              key={startL + i}
              color={ln.startsWith("✗") ? palette.toolErr : palette.assistant}
              wrap="truncate-end"
            >
              {ln}
            </Text>
          ))
        )}
      </Box>

      {/* Result — only once finished; never streamed while running. */}
      {!running && t.preview ? (
        <Box flexDirection="column" marginTop={1}>
          <Text color={palette.muted}>result</Text>
          {t.preview
            .split("\n")
            .slice(-resultLines)
            .map((ln, li) => (
              <Text key={li} color={palette.assistant} wrap="truncate-end">
                {ln || " "}
              </Text>
            ))}
        </Box>
      ) : null}
    </Box>
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
  const [logScroll, setLogScroll] = useState(0); // lines scrolled UP from the tail
  const idx = tasks.length ? Math.min(sel, tasks.length - 1) : 0;
  const opened = open && tasks.length > 0; // never "open" an empty list
  const t = tasks.length ? tasks[idx] : undefined;

  const move = (d: number) => {
    setSel((s) => Math.max(0, Math.min(tasks.length - 1, s + d)));
    setLogScroll(0);
  };
  const openTask = () => {
    if (!tasks.length) return;
    setLogScroll(0); // enter at the tail — the latest activity
    setOpen(true);
  };
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
      // Detail view: → / esc / q closes; ↑/↓ SCROLL the activity log (older ↑,
      // newer ↓) so you can read through everything the agent has done.
      if (key.rightArrow || key.escape || input === "q") setOpen(false);
      else if (key.upArrow || input === "k") setLogScroll((s) => s + 1);
      else if (key.downArrow || input === "j") setLogScroll((s) => Math.max(0, s - 1));
      else control(input);
      return;
    }
    // List view: q/esc leaves the dashboard; ← (or enter) opens the selection.
    if (key.escape || input === "q") onClose();
    else if (key.upArrow || input === "k") move(-1);
    else if (key.downArrow || input === "j") move(1);
    else if (key.leftArrow || key.return) openTask();
    else control(input);
  });

  // ── DETAIL VIEW ─────────────────────────────────────────────────────
  if (opened && t) {
    return (
      <TaskDetail task={t} idx={idx} count={tasks.length} palette={palette} rows={rows} logScroll={logScroll} />
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
