# /attach-agent — Wire a LangGraph (or any) agent into KODA's TUI

Use this command when the user says things like:
- "attach this agent to KODA"
- "plug my coding agent into KODA"
- "connect agent.py to the KODA frontend"
- "make KODA use my custom agent"
- "integrate my LangGraph agent with KODA"

## Arguments

$ARGUMENTS — Path to the agent file (e.g. `coding_agent.py`, `agents/my_agent.py`).
If omitted, ask the user which file contains their agent.

---

## Step 1: Read and Analyze the Agent File

Read the file the user points to. Classify it into one of four patterns:

### Pattern A: LangGraph Graph Factory
The file builds and returns a **compiled LangGraph graph** (e.g. via `create_react_agent`, `StateGraph(...).compile()`, or similar). Look for:
- Imports from `langgraph.prebuilt` (`create_react_agent`)
- Imports from `langgraph.graph` (`StateGraph`, `END`)
- A `.compile()` call
- `MemorySaver` or other checkpointer
- A function that returns the compiled graph

### Pattern B: Custom Agent Class
The file defines a class with methods like `run()`, `invoke()`, `chat()`, `generate()` — but does NOT already implement `KodaAgent`. Look for:
- A class with an LLM client and tools
- Methods that accept a message/prompt and return a response
- Streaming support (async generators, callbacks)

### Pattern C: Raw SDK Client
The file uses Anthropic/OpenAI/Google SDK directly (not through LangChain). Look for:
- `from anthropic import ...` or `from openai import ...`
- Direct API calls like `client.messages.create()` or `client.chat.completions.create()`

### Pattern D: Remote/HTTP Agent
The file defines or connects to an HTTP service. Look for:
- FastAPI/Flask endpoints
- `httpx` or `requests` calls
- SSE streaming

---

## Step 2: Generate the Adapter

Based on the pattern, generate the integration code. Place it in `koda/adapters/` as a new module.

### For Pattern A (LangGraph Graph) — SIMPLEST

If the user's file has a function that returns a compiled graph, KODA can auto-wrap it. Just create a thin factory module:

```python
# koda/adapters/{agent_name}.py
"""KODA adapter for {agent_name} — auto-wrapped LangGraph graph."""

from __future__ import annotations

import os
from pathlib import Path

from koda.adapters.langgraph import LangGraphAdapter


def create_{agent_name}_adapter(
    model: str = "anthropic:claude-sonnet-4-6",
    thread_id: str | None = None,
) -> LangGraphAdapter:
    """Build {agent_name} and return it as a KodaAgent."""
    # Import the user's graph factory
    from {user_module} import {user_factory_function}

    graph = {user_factory_function}(model=model)  # adjust args as needed
    return LangGraphAdapter(graph=graph, model=model, thread_id=thread_id)
```

Key rules:
- If the user's factory already accepts `model` as a param, pass it through.
- If the graph uses `MemorySaver()` or any checkpointer, history is handled automatically — do NOT replay history.
- If the graph has NO checkpointer, `LangGraphAdapter` handles the one-shot history seed via its `_seeded` guard.
- Set `KODA_RECURSION_LIMIT` env var if the agent does many tool steps (default 100).

### For Pattern B (Custom Agent Class) — ADAPTER NEEDED

Create a proper `KodaAgent` adapter that wraps the user's class:

```python
# koda/adapters/{agent_name}.py
"""KODA adapter for {agent_name}."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator

from koda.agent_api import (
    AgentEvent, Done, TextDelta, ThinkingDelta,
    ToolStart, ToolResult, Usage,
)
from koda.adapters.base import BaseAdapter


class {AgentName}Adapter(BaseAdapter):
    """Wraps {AgentName} as a KodaAgent for the KODA TUI."""

    def __init__(self, model: str, thread_id: str | None = None) -> None:
        super().__init__(model=model, thread_id=thread_id)
        # Import and instantiate the user's agent
        from {user_module} import {UserAgentClass}
        self._agent = {UserAgentClass}(model=model)  # adjust constructor
        self._extractors = (_extract_events,)

    async def _native_stream(
        self, message: str, history: list[dict[str, Any]],
    ) -> AsyncIterator[Any]:
        """Drive the user's agent and yield its native events."""
        # Map to whatever the user's agent expects:
        # Option 1: If agent has an async stream method
        async for chunk in self._agent.stream(message):
            yield chunk
        # Option 2: If agent is sync, run in thread
        # result = await asyncio.to_thread(self._agent.run, message)
        # yield result


def _extract_events(chunk: Any):
    """Map the user's agent output to KODA events.

    Adapt this based on what the user's agent actually yields/returns.
    """
    # If chunk is a string (simple text response):
    if isinstance(chunk, str):
        return (TextDelta(content=chunk),)
    # If chunk is a dict with 'content':
    if isinstance(chunk, dict):
        if "content" in chunk:
            return (TextDelta(content=chunk["content"]),)
    return None


def create_{agent_name}_adapter(
    model: str = "anthropic:claude-sonnet-4-6",
    thread_id: str | None = None,
) -> {AgentName}Adapter:
    return {AgentName}Adapter(model=model, thread_id=thread_id)
```

Key rules:
- Study the user's agent class carefully — find its streaming method signature.
- Map every output type to KODA events: text -> TextDelta, tool calls -> ToolStart/ToolResult.
- If the agent is sync-only, use `asyncio.to_thread()` in `_native_stream`.
- `BaseAdapter` handles cancel, usage accumulation, error wrapping, and final `Done` automatically.

### For Pattern C (Raw SDK) — DIRECT ADAPTER

Follow the pattern in `koda/adapters/anthropic.py`. Create a `BaseAdapter` subclass that calls the SDK directly and extracts events from the SDK's native streaming format.

### For Pattern D (Remote HTTP) — SSE ADAPTER

Create an `httpx`-based adapter that streams from the remote endpoint and maps SSE events to KODA's event types.

---

## Step 3: Register in __main__.py

Add a named shortcut so the user can launch with `koda --agent {name}` (not just the dotted path).

Edit `koda/__main__.py` in `_build_adapter_factory()`:

```python
if spec == "{agent_name}":
    from koda.adapters.{agent_name} import create_{agent_name}_adapter
    return lambda model, thread_id: create_{agent_name}_adapter(
        model=model, thread_id=thread_id
    )
```

Add this BEFORE the `if "." in spec:` block, alongside the existing `"deep"` entry.

Also update the error message's options list to include the new agent name.

---

## Step 4: Validate

Run the smoke-test validator:

```bash
python agent_workspace/skills/koda-adapter/scripts/validate.py \
    koda.adapters.{agent_name}.create_{agent_name}_adapter \
    --model {model} \
    --prompt "Say hello"
```

Check for:
- At least one visible event (TextDelta or ToolStart)
- Exactly one Done at the end
- Every ToolStart has a matching ToolResult
- model_name() returns a non-empty string

If validation passes, tell the user:

```
koda --agent {agent_name} --model {model}
```

---

## Step 5: Summary

After completing the integration, report:

1. **What was created**: the adapter file path
2. **Integration pattern**: which of the 4 patterns was used
3. **How to launch**: the exact `koda --agent ...` command
4. **How to validate**: the validate.py command
5. **Any caveats**: e.g. missing tools, sync-only limitations, no streaming support

---

## Critical Rules

1. **Always read the user's agent file first** before generating anything. Understand its interface.
2. **Prefer Pattern A** (LangGraph auto-wrap) whenever possible — it's the least code.
3. **Never break existing adapters**. The `deep` agent must continue to work.
4. **Match tool_ids exactly** between ToolStart and ToolResult events.
5. **Always emit Done last** — the TUI depends on it to dismiss the thinking indicator.
6. **Handle interrupts** — set a cancel flag in `interrupt()` and check it in the stream loop.
7. **Don't replay history** for LangGraph graphs with checkpointers — they persist state internally.
8. **Import the user's module lazily** inside the factory/adapter, not at module top level, to avoid circular imports and keep KODA startup fast.
9. **Preserve the user's tools** — if their agent has custom tools, make sure they're wired through.
10. **Use `asyncio.to_thread()`** for sync agents to avoid blocking the TUI event loop.

---

## File Reference

| File | Purpose |
|---|---|
| `koda/agent_api.py` | KodaAgent Protocol + 6 event dataclasses |
| `koda/adapters/base.py` | BaseAdapter (cancel, usage, error, Done plumbing) |
| `koda/adapters/langgraph.py` | LangGraphAdapter (wraps compiled graphs) |
| `koda/adapters/deep.py` | KODA's default agent (reference implementation) |
| `koda/__main__.py` | CLI entry + `_build_adapter_factory()` resolution |
| `agent_workspace/skills/koda-adapter/scripts/validate.py` | Smoke-test runner |
