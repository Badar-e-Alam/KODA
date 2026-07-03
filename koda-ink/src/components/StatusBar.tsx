import React from "react";
import { Box, Text } from "ink";
import type { Palette } from "../theme.js";
import { styleFor, type Mode } from "../modes.js";

interface Props {
  model: string;
  mode: Mode;
  inputTokens: number;
  outputTokens: number;
  palette: Palette;
}

function fmt(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

export function StatusBar({ model, mode, inputTokens, outputTokens, palette }: Props) {
  const ms = styleFor(mode);
  return (
    <Box>
      <Text color={palette.muted}>
        {model}
        {"  ·  "}
        in {fmt(inputTokens)} out {fmt(outputTokens)}
        {"  ·  "}
      </Text>
      <Text color={ms.color} bold>
        {ms.label}
      </Text>
    </Box>
  );
}
