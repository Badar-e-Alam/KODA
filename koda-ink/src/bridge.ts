// Spawns `python -m koda.bridge` and speaks newline-delimited JSON over stdio.
// This is the only place that knows about the child process; the UI consumes a
// clean typed event stream.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import readline from "node:readline";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { BridgeCommand, BridgeEvent } from "./types.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// koda-ink/src → repo root is two levels up.
const REPO_ROOT = path.resolve(__dirname, "..", "..");

function resolvePython(): string {
  if (process.env.KODA_PYTHON && fs.existsSync(process.env.KODA_PYTHON)) {
    return process.env.KODA_PYTHON;
  }
  const candidates = [
    path.join(REPO_ROOT, ".venv", "bin", "python"),
    path.join(process.env.HOME ?? "", ".koda", "venv", "bin", "python"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return "python3";
}

export interface BridgeOptions {
  agent?: string;
  model?: string;
  cwd?: string;
  autoApprove?: boolean;
}

export class Bridge {
  private proc: ChildProcessWithoutNullStreams;
  private listeners: Array<(ev: BridgeEvent) => void> = [];
  private exitListeners: Array<(code: number | null) => void> = [];
  public stderrTail: string[] = [];

  constructor(opts: BridgeOptions = {}) {
    const python = resolvePython();
    const args = ["-m", "koda.bridge"];
    if (opts.agent) args.push("--agent", opts.agent);
    if (opts.model) args.push("--model", opts.model);
    if (opts.autoApprove) args.push("--auto-approve");
    args.push("--cwd", opts.cwd ?? process.cwd());

    this.proc = spawn(python, args, {
      cwd: REPO_ROOT, // import koda from the repo/venv
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    });

    const rl = readline.createInterface({ input: this.proc.stdout });
    rl.on("line", (line) => {
      const s = line.trim();
      if (!s) return;
      let ev: BridgeEvent;
      try {
        ev = JSON.parse(s) as BridgeEvent;
      } catch {
        return; // ignore any non-JSON noise
      }
      for (const l of this.listeners) l(ev);
    });

    const errRl = readline.createInterface({ input: this.proc.stderr });
    errRl.on("line", (line) => {
      this.stderrTail.push(line);
      if (this.stderrTail.length > 50) this.stderrTail.shift();
    });

    this.proc.on("exit", (code) => {
      for (const l of this.exitListeners) l(code);
    });

    // spawn failures (ENOENT python, non-executable KODA_PYTHON, …) surface as
    // an async 'error' event with no default listener → would crash the whole
    // UI with a raw stack. Route it into the friendly exit path instead.
    this.proc.on("error", (err) => {
      this.stderrTail.push(`spawn failed: ${err.message}`);
      for (const l of this.exitListeners) l(-1);
    });
    // stdin pipe errors (EPIPE if the bridge closes its reader) are delivered
    // as an 'error' event on the socket; swallow rather than crash.
    this.proc.stdin.on("error", () => {});
  }

  onEvent(cb: (ev: BridgeEvent) => void): void {
    this.listeners.push(cb);
  }

  onExit(cb: (code: number | null) => void): void {
    this.exitListeners.push(cb);
  }

  send(cmd: BridgeCommand): void {
    try {
      this.proc.stdin.write(JSON.stringify(cmd) + "\n");
    } catch {
      /* child gone */
    }
  }

  close(): void {
    this.send({ type: "quit" });
    setTimeout(() => {
      if (!this.proc.killed) this.proc.kill();
    }, 500);
  }
}
