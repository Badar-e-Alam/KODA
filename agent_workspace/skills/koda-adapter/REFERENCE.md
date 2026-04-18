# KODA Adapter API Reference

Exact signatures, extension points, and implementation notes for KODA's
`KodaAgent` protocol.

## Event dataclasses

All defined in `koda/agent_api.py`. All are plain `@dataclass` — construct
with keyword args.

```python
@dataclass
class TextDelta:
    content: str

@dataclass
class ThinkingDelta:
    content: str

@dataclass
class ToolStart:
    tool_id: str               # stable id; must match ToolResult
    name: str                  # tool name shown in the widget header
    arguments: dict[str, Any]  # JSON-serializable

@dataclass
class ToolResult:
    tool_id: str               # must match a prior ToolStart.tool_id
    output: str                # stringified output (TUI strips ANSI escapes)
    is_error: bool = False     # toggles the red "-error" style

@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

@dataclass
class Done:
    usage: Usage | None = None

AgentEvent = TextDelta | ThinkingDelta | ToolStart | ToolResult | Usage | Done
```

## `KodaAgent` Protocol

```python
@runtime_checkable
class KodaAgent(Protocol):
    def model_name(self) -> str: ...
    def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]: ...
    async def interrupt(self) -> None: ...
```

- `model_name()` is synchronous. It feeds the status bar.
- `stream()` is an async generator. `history` is a list of
  `{"role": "user"|"assistant"|"system", "content": str}` dicts.
- `interrupt()` may be called at any time (including before `stream()`
  starts). Must be idempotent.

## Extending `BaseAdapter` (recommended)

`koda.adapters.base.BaseAdapter` handles cancellation, usage aggregation,
error events, and final `Done` emission. You implement:

```python
async def _native_stream(
    self, message: str, history: list[dict[str, Any]],
) -> AsyncIterator[BackendEvent]: ...
```

…plus a small list of extractor functions that map your backend's raw
events to KODA events. See `koda/adapters/langgraph.py` for the exact
pattern.

If your backend is simple (no tool calls, no complex state), override
`stream()` directly and yield events yourself. Use the snippet in
`SKILL.md` as your skeleton.

## Factory contract

KODA's `--agent` flag (`koda/__main__.py::_build_adapter_factory`) resolves:

- `"deep"` → built-in (`koda.adapters.deep.create_deep_adapter`).
- `"module.path.callable"` → `importlib.import_module("module.path")`,
  then calls `callable(model=<model>)`.

The returned object may be:
1. A `KodaAgent` → used directly.
2. Any object with `astream_events(..., version="v2")` (a LangGraph
   compiled graph) → wrapped in `LangGraphAdapter`.

Your factory signature must accept at least `model: str`. A second kwarg
`thread_id: str` is passed when it's present. Ignore what you don't need:

```python
def build(model: str = "...", **_: object) -> KodaAgent: ...
```

## `history` and `thread_id` — who owns state?

KODA is stateless from the adapter's view: on each `stream()` call you
get the new user message plus the full prior history as a list. How you
use it depends on your backend:

- **LangGraph with `checkpointer=MemorySaver()`** — LangGraph persists
  state per `thread_id`. Do **not** replay `history`; the graph will
  duplicate messages. Forward only the new user message. (See the
  `_seeded` guard in `koda/adapters/langgraph.py` for a fallback that
  covers graphs without a checkpointer.)

- **Anthropic / OpenAI SDK direct** — No server-side state. Send `history
  + [{"role":"user","content":message}]` on every call.

- **Your own HTTP service** — Your protocol. Use `thread_id` for
  continuity if you want.

## Interrupt semantics

`interrupt()` must stop the current `stream()` call promptly. Common
patterns:

- **Flag checked in loop body** — sufficient for Python-in-loop agents.
- **`asyncio.Event`** — set on interrupt, `await asyncio.wait()` races.
- **SDK cancellation** — `client.close()` or similar, wrapped in
  `asyncio.to_thread` if it's blocking.

The TUI calls `interrupt()` on Ctrl+C during a turn, then cancels the
asyncio task. If your stream doesn't cooperate, the task kill is the
fallback.

## Error handling

Three options, in order of cleanness:

1. **Yield a `ToolResult(tool_id="error", output=str(e), is_error=True)`**
   — shows the error inline as if a tool failed.
2. **Let the exception propagate** — `BaseAdapter` catches it and emits
   an error event automatically; then yields `Done`.
3. **Catch and swallow** — only if you've already yielded something
   sensible. Always still yield `Done`.

## Usage accounting

`Usage` fields are cumulative from the provider's perspective. Emit:

- **Once at the end**, attached to `Done(usage=...)`, OR
- **Mid-stream**, for backends that report per-chunk deltas.

If you emit mid-stream Usage events, the TUI sums them into the status
bar. Don't double-count; most providers only report the final totals
on the last chunk.

## Environment contract

KODA sets `KODA_WORKSPACE` before calling your factory. Read it like:

```python
import os
from pathlib import Path
ws = Path(os.environ.get("KODA_WORKSPACE", Path.cwd() / "agent_workspace"))
ws.mkdir(parents=True, exist_ok=True)
```

Your filesystem tools, if any, should be jailed to this root.

## Files you'll touch

| Want to… | Read | Model after |
|---|---|---|
| Write any adapter | `koda/agent_api.py` | `koda/adapters/langgraph.py` |
| Support Anthropic/OpenAI SDK | — | `EXAMPLES.md` |
| Wrap a LangGraph graph | — | `koda/adapters/deep.py` |
| Connect to a remote server | — | `examples/fastapi_agent.py` + `EXAMPLES.md` |
| Extend the default agent's tools | `koda/adapters/deep.py` | `koda/tools/fs.py`, `koda/tools/web.py` |
| Add a skill (not an adapter) | `agent_workspace/skills/pdf/SKILL.md` | any existing skill |
