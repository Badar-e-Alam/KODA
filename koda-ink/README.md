# koda-ink — KODA's inline terminal UI (TypeScript + Ink)

A lightweight, **inline** terminal frontend for the KODA coding agent — the
alternative to the full-screen Textual TUI. Inspired by the inline REPL design
of Pi, Claude Code, and Codex CLI (as opposed to the full-screen alt-screen
approach of Amp / opencode).

Because it renders **into the normal terminal scrollback** (no alternate
screen, no mouse capture), you get for free the things the Textual UI broke:

- **native text selection & copy** (click-drag like any terminal)
- **clickable links** (OSC-8, via your terminal)
- **native scrollback & search** (`Cmd/Ctrl+F`), and output that **persists after exit**

## Architecture

```
┌─────────────────────────┐   NDJSON over stdio    ┌────────────────────────┐
│  koda-ink  (TypeScript)  │  ───── commands ─────▶ │  python -m koda.bridge  │
│  Ink / React inline REPL │  ◀──── events ──────── │  (reuses ALL of KODA:   │
│  banner · stream · tools │                        │   LangGraph agent,      │
│  input · slash · @ · !   │                        │   providers, sessions,  │
└─────────────────────────┘                        │   permissions, tools)   │
                                                    └────────────────────────┘
```

The **entire Python agent backend is reused unchanged.** `koda-ink` is purely a
presentation layer; the agent brain stays in Python (the opencode client/server
pattern). The bridge (`koda/bridge.py`) translates KODA's `AgentEvent` stream
to newline-delimited JSON — the same event schema as KODA's SSE protocol.

## Run

```bash
# one-time
cd koda-ink && npm install

# then, from anywhere — the inline UI is the DEFAULT frontend:
koda                                   # launches this UI
koda --ui textual                      # legacy full-screen TUI (escape hatch)

# or directly:
node koda-ink/bin/koda-ink.mjs --agent coding_agent --model anthropic:claude-sonnet-4-6
npm start -- --model ollama:llama3.1   # dev
```

`koda` execs Node and pins `KODA_PYTHON` to the current interpreter, so the
bridge runs in the same venv where `koda` is installed. Override the interpreter
with `KODA_PYTHON=/path/to/python`. If Node or `node_modules` are missing, koda
prints why and falls back to the Textual TUI.

## Features (parity with the Textual UI)

| Feature | Notes |
|---|---|
| Streaming assistant text | inline Markdown, block-committed to scrollback |
| Compact tool calls | `● tool(args)  ↳ preview (+N lines)`, green/red |
| Todo checklists | `write_todos` snapshots render inline |
| Thinking indicator | sparkle + elapsed clock during a turn |
| `@file` attachment | completion from `git ls-files`; contents inlined on send |
| `!` / `!!` shell | run a shell command inline (`!!` = local-only) |
| `/` slash commands | clear, model, theme, plan/edits/default, compact, copy, usage, agents, tools, tree, resume, tasks, dashboard, help, quit … |
| **Sessions (Claude-style)** | stored at `~/.koda/projects/<project-slug>/<session-id>.jsonl` (filename = session id); `/resume` opens a picker of past sessions and continues one — transcript replays and the agent's memory is re-seeded; `/tree` branches within a session |
| **Background subagents** | the agent launches subagents that run in the background (it stays free); live count under the input; `/tasks` and the `/dashboard` panel to inspect & control |
| Modes | `Shift+Tab` cycles default → accept-edits → plan |
| Permissions | inline approve / always / deny prompt (HITL) |
| Themes | koda, tokyo-night, dracula, solarized-dark |
| Status line | model · tokens · mode |
| History & completion | Up/Down history, `Ctrl+R`-style recall, Tab to accept |

## Async (background) subagents

Implements the same five-tool API as deepagents' official async subagents
(https://docs.langchain.com/oss/python/deepagents/async-subagents), executed
in-process: the agent launches a subagent and keeps working instead of blocking
on it. Each task is a full agent run on its **own LangGraph checkpoint thread** —
that persisted thread is the "memory" that makes the controls free:

- **stop** (dashboard `[s]`, or the agent's `cancel_async_task`) — cancel the
  run; its checkpoint stays put.
- **resume** (dashboard `[r]`, or the agent's `update_async_task`) —
  continue on the same thread, memory intact (interrupts a running task first,
  exactly like the official `update_async_task`).
- **restart** (dashboard `[R]`) — re-run the brief on a fresh thread.

Agent-facing tools (official names + schemas): `start_async_task(description,
subagent_type)` → task_id immediately, `check_async_task(task_id)` → JSON
status/result, `update_async_task(task_id, message)`, `cancel_async_task(task_id)`,
`list_async_tasks(status_filter)`. Statuses: `running`/`success`/`error`/`cancelled`.
When a task finishes, a `<task-notification>` is injected into the agent's next
turn. You watch/control tasks from the inline task bar, `/tasks`, or the
`/dashboard` panel (↑/↓ select · s/r/R stop/resume/restart · q close); the
selected task shows what's happening *inside* the agent — its recent tool trail
and an output preview.

**Remote (true official transport):** drop a `.koda/async_subagents.json` in
your project with `[{"name", "description", "graph_id", "url"?}]` entries and
KODA passes them to `create_deep_agent` — deepagents' own
`AsyncSubAgentMiddleware` then serves these tool names against your LangGraph
Platform / self-hosted Agent Protocol server (the in-process variants step
aside automatically).

Implementation: `koda/subagent_tasks.py` (registry + lifecycle),
`koda/subagent_tools.py` (agent tools), bridge protocol
(`task_update`/`task_done`/`task_permission` events, `task_*` commands).

## Keys

- `Enter` submit · trailing `\` then `Enter` inserts a newline
- `Up`/`Down` history (or navigate the completion popup) · `Tab` accept suggestion
- `Shift+Tab` cycle mode · `Esc` dismiss popup / interrupt the turn
- `Ctrl+C` clear line / interrupt · `Ctrl+D` (on empty line) quit

## Layout

```
src/
  cli.tsx          entry: args, prints banner once, mounts <App/>
  App.tsx          orchestration: bridge wiring, events, slash/shell dispatch
  bridge.ts        spawns python -m koda.bridge, NDJSON read/write
  transcript.ts    block-commit reducer (live → committed → <Static>)
  completer.ts     / @ /model /theme completion (ported from completers.py)
  markdown.tsx     streaming-safe Markdown → Ink renderer
  theme.ts modes.ts banner.ts   palettes / modes / ASCII banner (ported)
  components/       Banner-less; Message, Input, Completion, Permission, StatusBar
```
