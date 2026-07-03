import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import type { SessionInfo } from "../types.js";
import type { Palette } from "../theme.js";

interface Props {
  sessions: SessionInfo[];
  palette: Palette;
  onPick: (id: string) => void;
  onClose: () => void;
}

// Claude-Code-style /resume picker: ↑/↓ to select an old session, Enter to
// resume it (appends to the same session file), Esc to cancel.
export function SessionPicker({ sessions, palette, onPick, onClose }: Props) {
  const [sel, setSel] = useState(0);
  const idx = sessions.length ? Math.min(sel, sessions.length - 1) : 0;

  useInput((input, key) => {
    if (key.escape || input === "q") onClose();
    else if (key.upArrow) setSel((s) => Math.max(0, s - 1));
    else if (key.downArrow) setSel((s) => Math.min(sessions.length - 1, s + 1));
    else if (key.return && sessions.length) onPick(sessions[idx].id);
  });

  const shown = sessions.slice(0, 10);
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={palette.accent} paddingX={1} marginTop={1}>
      <Text color={palette.primary} bold>
        Resume a session ({sessions.length})
      </Text>
      <Text color={palette.muted}>↑/↓ select · Enter resume · Esc cancel</Text>
      {shown.length === 0 ? (
        <Text color={palette.muted}>No previous sessions for this project yet.</Text>
      ) : (
        shown.map((s, i) => {
          const active = i === idx;
          return (
            <Box key={s.id} flexDirection="column">
              <Text color={active ? palette.accent : palette.assistant} inverse={active}>
                {active ? "❯ " : "  "}
                <Text color={palette.toolOk}>{s.id.slice(0, 8)}</Text>
                <Text color={palette.muted}>
                  {"  "}
                  {s.started.replace("T", " ")} · {s.messages} msg
                </Text>
              </Text>
              <Text color={palette.muted}>
                {"    "}
                {s.preview || "(no preview)"}
              </Text>
            </Box>
          );
        })
      )}
      {sessions.length > shown.length ? (
        <Text color={palette.muted}>{`  …+${sessions.length - shown.length} more — /resume <id> directly`}</Text>
      ) : null}
    </Box>
  );
}
