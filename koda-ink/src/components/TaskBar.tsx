import React from "react";
import { Box, Text } from "ink";
import type { TaskSummary } from "../types.js";
import type { Palette } from "../theme.js";

// One-line subagent indicator rendered UNDER the input: how many are running
// (⚡), paused for approval (⏸), stopped (■), finished (✓/✗) — and how to open
// the dashboard. Per-task detail lives in /dashboard.
export function TaskBar({ tasks, palette }: { tasks: TaskSummary[]; palette: Palette }) {
  if (tasks.length === 0) return null;
  const n = (pred: (t: TaskSummary) => boolean) => tasks.filter(pred).length;
  const running = n((t) => t.state === "running" || t.state === "queued");
  const paused = n((t) => t.state === "paused");
  const stopped = n((t) => t.state === "cancelled");
  const done = n((t) => t.state === "success");
  const failed = n((t) => t.state === "error");

  return (
    <Box>
      <Text color={palette.muted}>
        {running > 0 ? (
          <Text color={palette.tool}>⚡ {running} subagent{running === 1 ? "" : "s"} running</Text>
        ) : null}
        {paused > 0 ? (
          <Text color={palette.accent}>
            {running > 0 ? "  ·  " : ""}⏸ {paused} awaiting approval
          </Text>
        ) : null}
        {stopped > 0 ? (
          <Text color={palette.muted}>
            {running + paused > 0 ? "  ·  " : ""}■ {stopped} stopped
          </Text>
        ) : null}
        {done > 0 ? (
          <Text color={palette.toolOk}>
            {running + paused + stopped > 0 ? "  ·  " : ""}✓ {done} done
          </Text>
        ) : null}
        {failed > 0 ? (
          <Text color={palette.toolErr}>
            {running + paused + stopped + done > 0 ? "  ·  " : ""}✗ {failed} failed
          </Text>
        ) : null}
        <Text color={palette.muted}>  —  /dashboard to open & manage</Text>
      </Text>
    </Box>
  );
}
