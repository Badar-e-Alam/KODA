// Ported from koda/tui/completers.py — suggestions for the input line.
//   /          → slash commands
//   /model ... → available models (from the bridge's model list)
//   /theme ... → theme names
//   @...       → files in the project (git ls-files, walk fallback)

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { THEMES } from "./theme.js";

export interface Suggestion {
  insert: string; // replaces the trigger fragment
  label: string;
  description: string;
}

export interface CompleteResult {
  suggestions: Suggestion[];
  range: [number, number]; // [start, end) in the input value to replace
  title: string;
}

export const COMMANDS: Array<[string, string]> = [
  ["clear", "start a new chat session"],
  ["model", "[provider:model] — switch model or show current"],
  ["setup", "add/change API keys and pick a client"],
  ["tree", "[id] — show the session tree, or jump to a node to branch"],
  ["resume", "[id] — pick a past session and continue it"],
  ["skill", "[new <name>: <what>] — list skills, or author one with the model"],
  ["compact", "summarize older messages to free up context"],
  ["copy", "copy the last assistant response"],
  ["theme", "[name] — switch color theme (or list)"],
  ["usage", "show cumulative token usage"],
  ["agents", "describe the active agent"],
  ["tools", "list the active agent's tools"],
  ["tasks", "list background subagent tasks"],
  ["dashboard", "open the full-screen subagent dashboard"],
  ["task", "<id> stop|resume|restart|result — control a subagent"],
  ["plan", "switch to plan mode (advisory, no writes/shell)"],
  ["edits", "switch to accept-edits mode"],
  ["default", "switch back to default mode"],
  ["help", "list all slash commands"],
  ["quit", "exit KODA"],
  ["exit", "exit KODA"],
];

// The bridge sends the model list on `ready`; we cache it here for completion.
let MODELS: string[] = [];
export function setModels(models: string[]): void {
  MODELS = models;
}

let FILES: string[] | null = null;
const IGNORE_DIRS = new Set([
  ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
  ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
  ".idea", ".vscode", "target", ".next", ".cache",
]);
const MAX_WALK = 5000;

function gitFiles(): string[] {
  try {
    const out = execFileSync(
      "git",
      ["ls-files", "--cached", "--others", "--exclude-standard"],
      { encoding: "utf8", timeout: 5000, maxBuffer: 16 * 1024 * 1024 },
    );
    return out.split("\n").filter(Boolean);
  } catch {
    return [];
  }
}

function walkFiles(): string[] {
  const root = process.cwd();
  const out: string[] = [];
  const stack: string[] = [root];
  while (stack.length && out.length < MAX_WALK) {
    const dir = stack.pop()!;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      if (e.isDirectory()) {
        if (!IGNORE_DIRS.has(e.name)) stack.push(path.join(dir, e.name));
      } else {
        out.push(path.relative(root, path.join(dir, e.name)).split(path.sep).join("/"));
        if (out.length >= MAX_WALK) break;
      }
    }
  }
  return out;
}

function allFiles(): string[] {
  if (FILES) return FILES;
  FILES = gitFiles();
  if (FILES.length === 0) FILES = walkFiles();
  return FILES;
}

export function invalidateFiles(): void {
  FILES = null;
}

function completeCommands(frag: string): Suggestion[] {
  const f = frag.toLowerCase();
  const out: Suggestion[] = [];
  for (const [name, desc] of COMMANDS) {
    if (name === f) continue;
    if (name.startsWith(f)) {
      const trailing = name === "model" || name === "theme" ? " " : "";
      out.push({ insert: `/${name}${trailing}`, label: `/${name}`, description: desc });
    }
  }
  return out;
}

function completeModels(frag: string): Suggestion[] {
  const f = frag.toLowerCase();
  return MODELS.filter((m) => m.toLowerCase().includes(f))
    .slice(0, 60)
    .map((m) => ({ insert: m, label: m, description: m.split(":")[0] }));
}

function completeThemes(frag: string): Suggestion[] {
  const f = frag.toLowerCase();
  return Object.keys(THEMES)
    .sort()
    .filter((n) => n.toLowerCase().includes(f))
    .map((n) => ({ insert: n, label: n, description: "theme" }));
}

function completeFiles(frag: string): Suggestion[] {
  const files = allFiles();
  const f = frag.toLowerCase();
  const scored: Array<[number, string]> = [];
  for (const file of files) {
    const name = file.split("/").pop()!.toLowerCase();
    const p = file.toLowerCase();
    if (!f) scored.push([0, file]);
    else if (name.startsWith(f)) scored.push([0, file]);
    else if (name.includes(f)) scored.push([1, file]);
    else if (p.includes(f)) scored.push([2, file]);
  }
  scored.sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]));
  return scored.slice(0, 40).map(([, p]) => ({ insert: `@${p}`, label: p, description: "" }));
}

function findAtToken(value: string, cursor: number): [number, number] | null {
  if (!value) return null;
  const c = Math.max(0, Math.min(cursor, value.length));
  let start = c;
  while (start > 0 && !" \t\n".includes(value[start - 1])) {
    start -= 1;
    if (value[start] === "@") break;
  }
  if (value[start] !== "@") return null;
  let end = c;
  while (end < value.length && !" \t\n".includes(value[end])) end += 1;
  return [start, end];
}

export function complete(value: string, cursor: number): CompleteResult | null {
  if (value.startsWith("/model ")) {
    return { suggestions: completeModels(value.slice(7).trim()), range: [7, value.length], title: "Models" };
  }
  if (value.startsWith("/theme ")) {
    return { suggestions: completeThemes(value.slice(7).trim()), range: [7, value.length], title: "Themes" };
  }
  if (value.startsWith("/")) {
    const frag = value.slice(1).split(" ")[0];
    return { suggestions: completeCommands(frag), range: [0, 1 + frag.length], title: "Commands" };
  }
  const at = findAtToken(value, cursor);
  if (at) {
    const frag = value.slice(at[0] + 1, at[1]);
    return { suggestions: completeFiles(frag), range: at, title: "Files" };
  }
  return null;
}
