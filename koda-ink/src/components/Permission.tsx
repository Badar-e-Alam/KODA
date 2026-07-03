import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import type { PermissionItem } from "../types.js";
import type { Palette } from "../theme.js";

interface Props {
  item: PermissionItem;
  index: number;
  total: number;
  palette: Palette;
  onDecide: (outcome: "allow" | "always" | "deny") => void;
}

const OPTIONS: Array<{ key: "allow" | "always" | "deny"; label: string }> = [
  { key: "allow", label: "Allow once" },
  { key: "always", label: "Always allow this tool" },
  { key: "deny", label: "Deny" },
];

export function Permission({ item, index, total, palette, onDecide }: Props) {
  const [sel, setSel] = useState(0);

  useInput((input, key) => {
    if (key.upArrow) setSel((s) => (s + OPTIONS.length - 1) % OPTIONS.length);
    else if (key.downArrow) setSel((s) => (s + 1) % OPTIONS.length);
    else if (key.return) onDecide(OPTIONS[sel].key);
    else if (input === "y" || input === "a") onDecide("allow");
    else if (input === "A") onDecide("always");
    else if (input === "n" || input === "d" || key.escape) onDecide("deny");
  });

  const cmd = (item.args?.command ?? item.args?.file_path ?? "") as string;
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={palette.accent} paddingX={1} marginTop={1}>
      <Text color={palette.accent} bold>
        Permission required {total > 1 ? `(${index + 1}/${total})` : ""}
      </Text>
      <Text color={palette.assistant}>
        Tool: <Text bold>{item.tool_name}</Text>
        {cmd ? <Text color={palette.muted}>  {String(cmd).slice(0, 120)}</Text> : null}
      </Text>
      {item.resolved_path ? (
        <Text color={palette.muted}>
          writes to: <Text color={palette.assistant}>{item.resolved_path}</Text> (workspace-jailed)
        </Text>
      ) : null}
      <Box flexDirection="column" marginTop={1}>
        {OPTIONS.map((o, i) => (
          <Text key={o.key} color={i === sel ? palette.accent : palette.assistant} inverse={i === sel}>
            {i === sel ? "❯ " : "  "}
            {o.label}
          </Text>
        ))}
      </Box>
      <Text color={palette.muted}>↑/↓ + Enter · or y=allow / A=always / n=deny</Text>
    </Box>
  );
}
