# How to connect a model or agent to KODA

KODA separates **UI** (the TUI, session tree, slash-commands) from **agent**
(the thing that turns a user message into tool calls and text). You control
the agent side; the TUI stays the same.

This guide covers three ways to plug an agent in, from easiest to most
flexible:

1. [Use a built-in provider model](#1-use-a-built-in-provider-model) (no code)
2. [Wrap a LangGraph graph](#2-wrap-a-langgraph-graph) (a few lines)
3. [Implement the `KodaAgent` protocol](#3-implement-the-kodaagent-protocol) (full control)

Plus recipes for common frontier-agent patterns at the end.

---

## Core concepts

**`KodaAgent` protocol** (see `koda/agent_api.py`) — 3 methods:

```python
class KodaAgent(Protocol):
    def model_name(self) -> str: ...
    def stream(self, message: str, history: list[dict]) -> AsyncIterator[AgentEvent]: ...
    async def interrupt(self) -> None: ...
```

**`AgentEvent`** — a typed union the TUI knows how to render:

| Event | Meaning |
|-------|---------|
| `TextDelta(content)` | Incremental assistant text |
| `ThinkingDelta(content)` | Incremental reasoning / chain-of-thought |
| `ToolStart(tool_id, name, arguments)` | A tool invocation has begun |
| `ToolResult(tool_id, output, is_error)` | Result returned by a tool |
| `Usage(input_tokens, output_tokens, ...)` | Mid-stream cumulative usage |
| `Done(usage=...)` | Stream complete |

**`--agent` spec resolution** (see `koda/__main__.py`):

- `--agent deep` → built-in deepagents/LangGraph (default)
- `--agent module.path.factory` → imports `module.path`, calls
  `factory(model=...)`, auto-wraps the result

The factory can return:

- A `KodaAgent` — used directly.
- A compiled **LangGraph graph** — auto-wrapped by `LangGraphAdapter`.

---

## 1. Use a built-in provider model

No code needed. Set one or more API keys in `.env`, then run:

```bash
koda --model anthropic:claude-sonnet-4-6
koda --model openai:gpt-4o
koda --model google:gemini-2.5-flash
koda --model ollama:llama3.1            # local; needs `ollama serve`
koda --model ollama:glm-5.1:cloud       # Ollama Cloud; needs OLLAMA_API_KEY
```

Spec format: `provider:model_name`. The built-in `deep` agent handles
tool-calling, filesystem tools, web search, and compaction for you.

Switch models mid-session with `/model <spec>`.

---

## 2. Wrap a LangGraph graph

If you already have a LangGraph agent (from `deepagents`, `langgraph`
directly, or a LangChain `AgentExecutor`), wrapping it is one function.

### Minimal example

```python
# myagent.py
from __future__ import annotations

def build(model: str = "anthropic:claude-sonnet-4-6"):
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend
    from langgraph.checkpoint.memory import MemorySaver

    return create_deep_agent(
        model=model,
        tools=[],                       # add your LangChain @tool functions here
        backend=FilesystemBackend(root_dir="./agent_workspace", virtual_mode=True),
        system_prompt="You are my custom agent.",
        checkpointer=MemorySaver(),
    )
```

Run it:

```bash
koda --agent myagent.build --model anthropic:claude-sonnet-4-6
```

KODA imports `myagent`, calls `build(model="anthropic:claude-sonnet-4-6")`,
sees a LangGraph graph, and wraps it with `LangGraphAdapter`. Tool calls
and text deltas are mapped automatically.

### What the adapter handles

| LangGraph event | → | `AgentEvent` |
|-----------------|---|--------------|
| `on_chat_model_stream` (text) | → | `TextDelta` |
| `on_chat_model_stream` (thinking) | → | `ThinkingDelta` |
| `on_tool_start` | → | `ToolStart` |
| `on_tool_end` | → | `ToolResult` |
| final usage snapshot | → | `Done(usage=...)` |

**Requirements for your graph:**

- Must implement `astream_events(version="v2")`.
- Input shape: `{"messages": [...]}` (LangChain `BaseMessage` list).
- Any `@tool`-decorated LangChain tool works — arguments and output are
  rendered faithfully by the TUI.

See `examples/deepagents_backend.py` for a full working copy.

---

## 3. Implement the `KodaAgent` protocol

For non-LangGraph agents (raw Anthropic SDK, OpenAI SDK, a remote service,
custom planner, etc.), implement `KodaAgent` directly.

### Example: raw Anthropic SDK with streaming

```python
# anthropic_agent.py
from __future__ import annotations

import os
from typing import Any, AsyncIterator

import anthropic
from koda.agent_api import AgentEvent, Done, TextDelta, ThinkingDelta, Usage


class AnthropicAgent:
    def __init__(self, model: str = "claude-sonnet-4-5"):
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._stream = None

    def model_name(self) -> str:
        return f"anthropic:{self._model}"

    async def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]:
        messages = [*history, {"role": "user", "content": message}]
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=4096,
            messages=messages,
            thinking={"type": "enabled", "budget_tokens": 2048},
        ) as stream:
            self._stream = stream
            async for event in stream:
                if event.type == "content_block_delta":
                    d = event.delta
                    if d.type == "text_delta":
                        yield TextDelta(content=d.text)
                    elif d.type == "thinking_delta":
                        yield ThinkingDelta(content=d.thinking)
            final = await stream.get_final_message()
            yield Done(usage=Usage(
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
            ))
            self._stream = None

    async def interrupt(self) -> None:
        if self._stream is not None:
            await self._stream.close()


def build(model: str = "claude-sonnet-4-5") -> AnthropicAgent:
    model = model.split(":", 1)[-1]  # strip provider prefix if present
    return AnthropicAgent(model=model)
```

Run:

```bash
koda --agent anthropic_agent.build --model anthropic:claude-sonnet-4-5
```

### Example: HTTP/SSE remote backend

If your agent runs as a separate service, implement a thin KODA-side client:

```python
# http_agent.py
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
from koda.agent_api import (
    AgentEvent, Done, TextDelta, ThinkingDelta, ToolResult, ToolStart, Usage,
)


class HttpAgent:
    def __init__(self, url: str, model: str):
        self._url = url
        self._model = model
        self._client = httpx.AsyncClient(timeout=None)

    def model_name(self) -> str:
        return f"http:{self._model}"

    async def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]:
        payload = {"message": message, "history": history, "model": self._model}
        async with self._client.stream("POST", self._url, json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    return
                evt = json.loads(data)
                t = evt.get("type")
                if t == "text_delta":
                    yield TextDelta(content=evt["content"])
                elif t == "thinking_delta":
                    yield ThinkingDelta(content=evt["content"])
                elif t == "tool_start":
                    yield ToolStart(
                        tool_id=evt["tool_id"],
                        name=evt["name"],
                        arguments=evt.get("arguments", {}),
                    )
                elif t == "tool_result":
                    yield ToolResult(
                        tool_id=evt["tool_id"],
                        output=evt["output"],
                        is_error=evt.get("is_error", False),
                    )
                elif t == "done":
                    u = evt.get("usage") or {}
                    yield Done(usage=Usage(**u))
                    return

    async def interrupt(self) -> None:
        await self._client.aclose()


def build(model: str = "my-model") -> HttpAgent:
    return HttpAgent(url="http://localhost:8000/stream", model=model)
```

Pair this with the sample FastAPI server in `examples/fastapi_agent.py`
(event format matches 1:1), run both:

```bash
uvicorn examples.fastapi_agent:app --port 8000
koda --agent http_agent.build
```

---

## Frontier-pattern recipes

These are sketches — adapt to your agent framework.

### Planner/executor split

Two LangGraph subgraphs, one wrapper:

```python
def build(model: str):
    from langgraph.graph import StateGraph, END
    planner = create_planner_graph(model)        # returns a plan (list of steps)
    executor = create_executor_graph(model)      # executes one step at a time

    graph = StateGraph(State)
    graph.add_node("plan", planner)
    graph.add_node("exec", executor)
    graph.add_edge("plan", "exec")
    graph.add_conditional_edges("exec", lambda s: "exec" if s.more else END)
    graph.set_entry_point("plan")
    return graph.compile()
```

Each node's text/tool events stream through KODA's UI — the user sees
the plan, then the execution, in order.

### Subagents (parallel tool calls)

Implement a `spawn_subagent` tool in your agent. When the agent calls it,
emit a `ToolStart` with a unique `tool_id`, run the subagent in a task,
then emit `ToolResult` when it completes. Multiple `ToolStart`s can be
open concurrently — the TUI shows them all.

```python
async def stream(self, message, history):
    ...
    if tool_call.name == "spawn_subagent":
        yield ToolStart(tool_id=tc.id, name="spawn_subagent", arguments=tc.args)
        result = await self._run_subagent(tc.args)      # own nested stream here
        yield ToolResult(tool_id=tc.id, output=result)
```

### Interleaved thinking

Just yield `ThinkingDelta` between `TextDelta`s. The TUI renders thinking
in a dimmed panel — the user can follow the reasoning trace alongside the
answer. Anthropic extended-thinking and Gemini 2.5 thinking both work
out of the box with the wrapped-LangGraph path.

### Reflection / self-critique loop

Implement it as a graph edge that feeds the assistant's output back as a
"critique this" user message, then takes the critique's revised answer.
The user sees two turns; the agent sees both.

### Context compaction

The built-in `deep` adapter already exposes a `compact_conversation` tool.
For custom agents, add a tool that:

1. Summarizes all but the last N messages into a single system note.
2. Replaces the history with `[summary, ...last_N]`.
3. Returns the compacted count to the model.

KODA displays it as a normal tool call — transparent to the user.

---

## Debugging

- Logs go to `logs/session_<timestamp>.log` (DEBUG level for `koda`, WARNING
  elsewhere).
- Add `_log = logging.getLogger("koda")` in your agent and call
  `_log.debug(...)` — your output shows up in the session log.
- `koda -y` auto-approves tool calls so you don't get stuck on prompts
  while iterating.

## Where to look in the code

| What | Where |
|------|-------|
| Protocol definition | `koda/agent_api.py` |
| Built-in deep agent factory | `koda/adapters/deep.py` |
| LangGraph → `KodaAgent` wrapper | `koda/adapters/langgraph.py` |
| CLI entry + `--agent` resolution | `koda/__main__.py` |
| Event stream rendering | `koda/tui/stream.py` |
| Minimal BYOA example | `examples/deepagents_backend.py` |
| HTTP/SSE service example | `examples/fastapi_agent.py` |

## Getting help

Open an issue with a minimal reproducer and the relevant section of
`logs/session_*.log`. For security-sensitive reports, see
[SECURITY.md](SECURITY.md).
