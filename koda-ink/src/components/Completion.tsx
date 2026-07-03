import React from "react";
import { Box, Text } from "ink";
import type { Suggestion } from "../completer.js";
import type { Palette } from "../theme.js";

interface Props {
  title: string;
  suggestions: Suggestion[];
  index: number;
  palette: Palette;
  max?: number;
}

export function Completion({ title, suggestions, index, palette, max = 8 }: Props) {
  if (suggestions.length === 0) return null;
  // Window the list around the highlighted item.
  const start = Math.max(0, Math.min(index - Math.floor(max / 2), suggestions.length - max));
  const shown = suggestions.slice(start, start + max);
  return (
    <Box flexDirection="column" marginLeft={2}>
      <Text color={palette.muted}>
        {title} ({index + 1}/{suggestions.length})
      </Text>
      {shown.map((s, i) => {
        const real = start + i;
        const active = real === index;
        return (
          <Text key={real} color={active ? palette.accent : palette.assistant} inverse={active}>
            {active ? "❯ " : "  "}
            {s.label}
            {s.description ? <Text color={palette.muted}>  {s.description}</Text> : null}
          </Text>
        );
      })}
    </Box>
  );
}
