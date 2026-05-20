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
koda/                    # Main package
  __main__.py            # CLI entry point, arg parsing, logging
  app.py                 # KodaApp (TUI, subclass of DeepAgentsApp)
  agents/deep.py         # LangGraph agent factory (create_koda_agent)
  session.py             # SessionTree (JSONL-based branching history)
  conversation_log.py    # Markdown session logs
  provider_models.py     # Model discovery + caching (Ollama, LM Studio, etc.)
  summarizer.py          # Branch summarization via LangChain
  tree_widget.py         # /tree command modal
  widgets.py             # KodaBanner ASCII art
tests/                   # pytest + Textual async UI tests
examples/                # fastapi_agent.py (HTTP/SSE backend example)
docs/prompts/            # System prompts and tool-loop docs
agent_workspace/skills/  # Custom tool skills
```

## Tech Stack

- **Python 3.13** (`.python-version`)
- **deepagents-cli** / **deepagents** - TUI framework + LangGraph agent orchestration
- **langchain-core** - LLM abstractions
- **textual** - TUI widgets
- **httpx** - Async HTTP client
- **hatchling** - Build system

## Running Tests

```bash
pytest tests/
```

Tests use Textual's `run_test()` pilot for async UI testing.

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
