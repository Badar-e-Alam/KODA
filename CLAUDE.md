# KODA

AI-powered terminal coding agent (TUI) built on deepagents-cli and LangGraph.

## Quick Start

```bash
# Install (editable, with a provider)
pip install -e ".[anthropic]"

# Run
koda                                    # auto-detect model from API keys
koda --model anthropic:claude-sonnet-4-6  # specify model
koda --model ollama:llama3.1            # local model
```

## Project Structure

```
koda/                    # Main Python package (agent backend)
  __main__.py            # CLI entry point: launches the inline UI, or one-shot
  bridge.py              # NDJSON/stdio backend that drives the inline (Ink) UI
  modes.py               # Permission modes (default/accept-edits/plan) — shared
  subagent_tasks.py      # Background async-subagent registry (dashboard)
  subagent_tools.py      # Agent-facing async-subagent tools
  session.py             # SessionTree (JSONL-based branching history)
  conversation_log.py    # Markdown session logs
  provider_models.py     # Model discovery + caching (Ollama, LM Studio, etc.)
  summarizer.py          # Branch summarization via LangChain
koda-ink/                # Inline UI — TypeScript + Ink (the interactive frontend)
  src/cli.tsx            # Ink entry; bin/koda-ink.mjs launches it via tsx
  src/components/        # Dashboard, Input, etc.
tests/                   # pytest (bridge, sessions, subagent tasks)
examples/                # fastapi_agent.py (HTTP/SSE backend example)
docs/prompts/            # System prompts and tool-loop docs
agent_workspace/skills/  # Custom tool skills
```

The **only** interactive frontend is the inline Ink UI (`koda-ink/`), which
needs Node ≥18. Python's `koda/__main__.py` execs the Node launcher for
interactive sessions; `koda --prompt "…"` runs fully in-process (no Node).

## Tech Stack

- **Python 3.13** (`.python-version`) — agent backend
- **Node ≥18** + **TypeScript** + **Ink** — the inline terminal UI (`koda-ink/`)
- **deepagents-cli** / **deepagents** - LangGraph agent orchestration
- **langchain-core** - LLM abstractions
- **httpx** - Async HTTP client
- **hatchling** - Build system

## Running Tests

```bash
pytest tests/                       # Python backend
cd koda-ink && npm run typecheck    # Inline UI type check
```

## Environment

Copy `.env.example` to `.env` and set API keys:
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `JINA_API_KEY`
- `OLLAMA_HOST`, `OLLAMA_API_KEY`

## Architecture

- **Agent backends** are pluggable: deep (LangGraph, default), HTTP/SSE remote, or custom
- **Event protocol** uses SSE with types: `thinking_delta`, `text_delta`, `tool_start`, `tool_result`
- **Session management** uses JSONL trees stored at `~/.koda/sessions/`
- **Model discovery** caches provider model lists (24h TTL) at `~/.koda/models/`
- Backend switching via `--agent` flag or `/model` TUI command

## Claude Code Skills

### `/attach-agent <path>`

Attaches any LangGraph agent (or custom agent class) to KODA's TUI frontend.
Point it at an agent file and it will:

1. Analyze the agent (LangGraph graph, custom class, raw SDK, or HTTP)
2. Generate the adapter in `koda/adapters/`
3. Register it in `koda/__main__.py`
4. Validate with `agent_workspace/skills/koda-adapter/scripts/validate.py`

Key files for the adapter contract:
- `koda/agent_api.py` — `KodaAgent` Protocol + 6 event dataclasses
- `koda/adapters/base.py` — `BaseAdapter` (cancel, usage, error, Done)
- `koda/adapters/langgraph.py` — `LangGraphAdapter` (wraps compiled graphs)
- `koda/__main__.py` — `_build_adapter_factory()` resolves `--agent` specs
