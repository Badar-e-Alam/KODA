#!/usr/bin/env node
// Launcher: run the TypeScript entry through tsx (no build step). stdio is
// inherited so Ink gets the real TTY (raw mode / colors / resize).
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const dir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(dir, "..");
const cli = path.join(root, "src", "cli.tsx");
const tsxBin = path.join(root, "node_modules", ".bin", "tsx");

if (!fs.existsSync(tsxBin)) {
  process.stderr.write(
    "koda-ink: dependencies not installed. Run `npm install` in koda-ink/ first.\n",
  );
  process.exit(1);
}

const res = spawnSync(tsxBin, [cli, ...process.argv.slice(2)], { stdio: "inherit" });
process.exit(res.status ?? 0);
