---
name: koda-adapter
description: Use this skill whenever the user wants to plug a new agent, model, or framework into the KODA TUI — i.e. "bring my own agent", "connect X to KODA", "implement a KodaAgent", "write a KODA adapter", "make KODA talk to Anthropic SDK / OpenAI / LangGraph / a remote HTTP agent / Ollama / some other backend". Use it when extending KODA's frontend with new agent backends or when debugging why an adapter's events aren't streaming correctly. Use it for launching an adapter via `koda --agent <module.path>`.
license: Part of the KODA project. See repository root LICENSE.
---

# KODA Adapter Skill

## What KODA is

KODA is an **agent-agnostic TUI frontend**. The TUI owns chat, session tree,
model switching, slash commands, sidebar, token counters, and theme — it
does **not** own the agent. Any program that implements the `KodaAgent`
Protocol can be attached at launch with `--agent module.path.factory`.

## When this skill kicks in

Trigger this skill whenever a user is:

- Building their own agent to plug into KODA.
- Wrapping an SDK they already use (Anthropic, OpenAI, LangGraph, LlamaIndex,
  a remote HTTP/SSE service, Ollama, Bedrock, etc.).
- Asking why events aren't streaming / tool calls aren't showing / the
  thinking indicator never goes away.
- Switching between "raw LangGraph graph" and "proper KodaAgent adapter".

## The contract (one Protocol, six event types)

A KODA-compatible agent implements exactly this Protocol
(`koda/agent_api.py`):

```python
class KodaAgent(Protocol):
    def model_name(self) -> str: ...
    def stream(self, message: str, history: list[dict]) -> AsyncIterator[AgentEvent]: ...
    async def interrupt(self) -> None: ...
```

`AgentEvent` is one of six dataclasses the adapter yields from `stream()`:

| Event          | Fields                                       | When to emit                          |
|----------------|----------------------------------------------|---------------------------------------|
| `TextDelta`    | `content: str`                               | Each chunk of assistant text          |
| `ThinkingDelta`| `content: str`                               | Chain-of-thought / reasoning token    |
| `ToolStart`    | `tool_id, name, arguments`                   | The model starts a tool call          |
| `ToolResult`   | `tool_id, output, is_error`                  | The tool returns (match by `tool_id`) |
| `Usage`        | `input_tokens, output_tokens, cache_read_*`  | Token counts — mid-stream or on done  |
| `Done`         | `usage: Usage \| None`                       | One per turn. Always emit exactly one |

**Rules:**
- Emit events in arrival order; don't buffer them.
- Pair every `ToolStart` with a matching `ToolResult` by `tool_id`.
- Always yield `Done` last — the TUI uses it to dismiss the thinking indicator and update the status bar.
- `interrupt()` should cancel in-flight work; safe to call any time.

See `REFERENCE.md` for the exact dataclass definitions, including any
field defaults.

## Two-minute decision tree

1. **You already have a compiled LangGraph graph**
   → Don't write an adapter. Return the graph from your factory; KODA
   auto-wraps it in `LangGraphAdapter`. Done.

2. **You have an Anthropic / OpenAI / Ollama SDK client**
   → Write a direct adapter (subclass `koda.adapters.base.BaseAdapter`,
   implement `_native_stream` or override `stream` entirely). See
   `EXAMPLES.md` → "Direct SDK Adapter".

3. **Your agent lives behind HTTP/SSE**
   → Write a remote adapter that fetches from your endpoint and yields
   events. See `EXAMPLES.md` → "Remote HTTP/SSE Adapter" plus
   `examples/fastapi_agent.py` for a matching server.

4. **Something exotic (gRPC, WebSocket, subprocess)**
   → Same pattern as #3 — whatever produces your events goes into
   `stream()`. Everything else is the same.

## Minimum viable adapter (copy-paste)

```python
# my_agent.py
from typing import Any, AsyncIterator
from koda.agent_api import (
    KodaAgent, AgentEvent, TextDelta, ToolStart, ToolResult, Usage, Done,
)

class MyAgent(KodaAgent):
    def __init__(self, model: str) -> None:
        self._model = model
        self._cancel = False

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        self._cancel = True

    async def stream(
        self, message: str, history: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        self._cancel = False
        # ... your model call here ...
        for chunk in ("Hello ", "from ", "my ", "agent!"):
            if self._cancel:
                break
            yield TextDelta(content=chunk)
        yield Done(usage=Usage(input_tokens=10, output_tokens=4))


def build(model: str = "my:default") -> MyAgent:
    """Factory consumed by `koda --agent my_agent.build`."""
    return MyAgent(model=model)
```

Launch it:

```bash
koda --agent my_agent.build --model my:default
```

## Launching

`koda --agent <spec>` accepts:

- **`deep`** — KODA's built-in agent (`koda.adapters.deep.create_deep_adapter`).
- **`module.path.factory`** — any importable callable returning either:
  - A `KodaAgent` (used as-is), OR
  - A compiled LangGraph graph with `astream_events` (auto-wrapped
    in `LangGraphAdapter`).

The factory is called as `factory(model=<model>)`. If your factory needs
a workspace path, read `$KODA_WORKSPACE` (it's set by KODA before the
factory runs; falls back to `./agent_workspace`).

The factory can also accept `thread_id` as a second kwarg — KODA passes
it when switching models mid-session so the adapter can preserve or
reset conversation state.

## Common pitfalls

1. **Popup "Thinking ···" never disappears** → Your `stream()` never
   yields a visible event. Emit at least a `TextDelta` or a `ToolStart`
   before `Done`.

2. **Tool calls show `…` forever** → A `ToolStart` was emitted without
   a matching `ToolResult` with the same `tool_id`. Match them exactly.

3. **Status bar stays at `↑0 ↓0`** → No `Usage` event emitted. You can
   emit one inside `Done(usage=Usage(...))` or as its own event mid-stream.

4. **`history` list looks empty on the first call** → KODA does not
   replay past turns through `stream()`. If your backend needs them
   (no internal checkpointer), forward `history` on the first call
   only, then track state yourself.

5. **`ctrl+c` doesn't stop your agent** → `interrupt()` must actually
   halt the work. A flag checked inside the stream loop is the minimum;
   for SDK clients, cancel the underlying request.

6. **Windows workspace path jumps to `C:\`** → When using
   `deepagents.FilesystemBackend`, pass `virtual_mode=True`. `False`
   means absolute paths like `/skills` resolve to the real OS root,
   not the workspace root.

## Verify your adapter works

Run the validator script — it builds your factory and streams a single
prompt, printing every event:

```bash
python agent_workspace/skills/koda-adapter/scripts/validate.py \
    my_agent.build --model my:default --prompt "hi"
```

A healthy run shows at least one `TextDelta` (or `ToolStart` +
`ToolResult`) followed by exactly one `Done`.

## What to read next

- `REFERENCE.md` — exact dataclass signatures, extending `BaseAdapter`,
  `LangGraphAdapter` internals, the `thread_id` / `history` contract.
- `EXAMPLES.md` — three working adapters: Anthropic SDK direct,
  LangGraph passthrough, HTTP/SSE remote.
- `scripts/validate.py` — the smoke-test runner mentioned above.
- Repo files to open when implementing:
  - `koda/agent_api.py` — the Protocol + event dataclasses.
  - `koda/adapters/base.py` — `BaseAdapter` (cancel, usage, error plumbing).
  - `koda/adapters/langgraph.py` — a real reference implementation.
  - `koda/__main__.py::_build_adapter_factory` — how `--agent` resolves your module.
