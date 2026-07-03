# KODA inline UI redesign (TypeScript + Ink)

Status: **shipped as the DEFAULT frontend** — plain `koda` launches the inline
Ink UI; `koda --ui textual` (or `KODA_UI=textual`) keeps the legacy full-screen
TUI as an escape hatch. If Node/`node_modules` are missing, `koda` explains why
and falls back to Textual.

## Why

The full-screen **Textual** TUI runs in the terminal's *alternate screen* and
captures the mouse. That bundles two problems that a lighter design avoids:

1. **Mouse capture** → breaks click-drag text selection / copy.
2. **In-place full repaint** (not newline-append) → breaks native scrollback,
   clickable OSC-8 links, and leaves nothing after exit.

Releasing the mouse (KODA's `Ctrl+O`) only half-fixes (1). Only an **inline,
append-style** renderer fixes (2). That's the design the leaner agents use.

## Landscape (research summary)

| Tool | Stack | UI model | Native copy/scrollback |
|---|---|---|---|
| Amp | TS, bespoke double-buffered TUI | full-screen alt-screen | ✗ (re-implements scroll) |
| opencode | TS server + Go/Bubbletea client (HTTP+SSE) | full-screen | ✗ in TUI |
| **Pi** | TS, hand-rolled `pi-tui` | **inline REPL** (deliberate) | ✅ |
| **Claude Code / Gemini** | TS + React + **Ink** | **inline** | ✅ |
| Codex | Rust + Ratatui | full-screen | ✗ → retrofitted `--no-alt-screen` |
| KODA (was) | Python + Textual | full-screen | ✗ (the bug) |

The winning pattern (Pi, Claude Code, Aider): stream the *current* block in a
small in-place live region, then **commit it as immutable newline-terminated
text** to the primary buffer. KODA-ink implements exactly this via Ink's
`<Static>` (committed transcript) + a small dynamic region (streaming block,
input, status).

## Decision

The user chose **TypeScript + Ink** for the UI. Rather than a line-by-line port
of the Python agent stack (LangGraph + deepagents + providers + branchable
sessions — thousands of lines, no faithful JS equivalent), we use the
**opencode client/server split**:

- **UI:** 100% TypeScript + Ink (`koda-ink/`).
- **Agent brain:** the existing Python backend, **reused unchanged**, exposed
  over a thin `koda.bridge` stdio process (NDJSON — same event schema as KODA's
  SSE protocol).

This delivers the requested TS+Ink inline UX and full feature parity while
keeping the working, non-trivial agent. A full Node port of the agent core
remains a possible future effort if zero-Python is ever required.

## Architecture

```
koda --ui ink  ──execs──▶  node koda-ink/bin/koda-ink.mjs
                                    │  spawns (KODA_PYTHON pinned to the venv)
                                    ▼
                           python -m koda.bridge     ← reuses adapters, agent,
                             stdin:  commands (JSON)     providers, sessions,
                             stdout: events   (JSON)     permissions, tools
```

- **Reused unchanged:** `koda/agent_api.py` (the event seam), `koda/adapters/*`,
  `koda/agents/*`, `koda/tools/*`, `koda/session.py`, `koda/summarizer.py`,
  `koda/provider_models.py`, `koda/model_config.py`, permissions, modes.
- **New Python:** `koda/bridge.py` (~380 lines) — the stdio event bridge.
- **New TS (`koda-ink/`):** cli, App, bridge client, block-commit reducer,
  completer, markdown renderer, theme/mode/banner ports, and the
  Message/Input/Completion/Permission/StatusBar components.
- **Entry wiring:** `koda/__main__.py` gains `--ui {textual,ink}` (env `KODA_UI`).

## Parity

Streaming assistant text (inline Markdown, committed to scrollback), compact
tool calls (`● tool(args) ↳ preview (+N lines)`), inline todo checklists,
thinking spinner + clock, `@file` attachment (completion + inlined contents),
`!`/`!!` shell, all `/` slash commands, `Shift+Tab` mode cycling, inline HITL
permission prompts (approve / always / deny), four themes, and a
model·tokens·mode status line.

## Run

```bash
cd koda-ink && npm install      # one-time
koda --ui ink                   # or  KODA_UI=ink koda
```

Requires Node ≥ 18. The bridge runs under `KODA_PYTHON` (defaults to the
interpreter that launched `koda`).

## Known gaps / follow-ups

- **Session tree (`/tree`) branching** is not yet wired into the inline UI
  (`/clear` starts fresh); the Textual UI still has the full modal.
- **Onboarding (`/setup`)** shows guidance rather than the full interactive
  modal.
- **Image paste** is not supported inline (needs a platform clipboard read).
- Migration plan: keep `--ui textual` as the default for now; once the inline UI
  has soaked, flip the default and eventually retire Textual (which would drop
  `koda/tui/*`, `session_panel.py`, `tree_widget.py`, and the `textual` dep).
