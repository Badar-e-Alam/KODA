import React from "react";
import { render } from "ink";
import { App } from "./App.js";
import { bannerString } from "./banner.js";
import { getTheme, DEFAULT_THEME } from "./theme.js";

const VERSION = "0.4.0";

interface Args {
  model?: string;
  agent: string;
  cwd?: string;
  autoApprove?: boolean;
  // Resume a past session at launch: "pick" opens the session picker,
  // "latest" continues the most recent session automatically.
  resume?: "pick" | "latest";
}

function parseArgs(argv: string[]): Args {
  const args: Args = { agent: "coding_agent" };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if ((a === "--model" || a === "-m") && argv[i + 1]) args.model = argv[++i];
    else if ((a === "--agent" || a === "-a") && argv[i + 1]) args.agent = argv[++i];
    else if ((a === "--cwd" || a === "-C") && argv[i + 1]) args.cwd = argv[++i];
    else if (a === "--auto-approve" || a === "-y") args.autoApprove = true;
    else if (a === "--resume" || a === "-r") args.resume = "pick";
    else if (a === "--continue" || a === "-c") args.resume = "latest";
    else if (a === "--help" || a === "-h") {
      process.stdout.write(
        "koda-ink — inline terminal UI for KODA\n\n" +
          "  --model, -m        provider:model (e.g. anthropic:claude-sonnet-4-6)\n" +
          "  --agent, -a        agent backend (default: coding_agent)\n" +
          "  --cwd,   -C        project directory to operate on\n" +
          "  --auto-approve, -y approve all gated tool calls without prompting\n" +
          "  --resume, -r       pick a past session to resume at launch\n" +
          "  --continue, -c     resume the most recent session automatically\n",
      );
      process.exit(0);
    }
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.cwd) {
    try {
      process.chdir(args.cwd);
    } catch {
      /* ignore */
    }
  }

  const color = process.stdout.isTTY && process.env.NO_COLOR === undefined;
  const palette = getTheme(DEFAULT_THEME);

  // Print the banner ONCE straight to stdout so it lands at the very top of
  // scrollback (permanent + selectable), then mount the live UI below it.
  process.stdout.write(
    bannerString({
      version: VERSION,
      model: args.model ?? "auto-detecting…",
      cwd: process.cwd(),
      mode: "default",
      palette,
      color,
    }) + "\n",
  );

  // Bracketed paste: terminals wrap pastes in ESC[200~/ESC[201~ so the input
  // can buffer them (large pastes split across reads must not auto-submit).
  if (process.stdout.isTTY) {
    process.stdout.write("\x1b[?2004h");
    process.on("exit", () => {
      try {
        process.stdout.write("\x1b[?2004l");
      } catch {
        /* stream gone */
      }
    });
  }

  render(
    <App
      bridgeOptions={{
        agent: args.agent,
        model: args.model,
        cwd: process.cwd(),
        autoApprove: args.autoApprove,
      }}
      initialModel={args.model ?? "…"}
      startupResume={args.resume}
    />,
    { exitOnCtrlC: false },
  );
}

main();
