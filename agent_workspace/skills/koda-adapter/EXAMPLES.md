# KODA Adapter Examples

Three complete, runnable adapter implementations. Each is self-contained
— copy into your own module and launch with
`koda --agent your_module.build --model <...>`.

## 1. Direct Anthropic SDK Adapter

Streams directly from Anthropic's SDK without going through LangChain or
LangGraph. Shows text streaming, thinking blocks, tool calls, and usage.

```python
# anthropic_adapter.py
from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from koda.agent_api import (
    AgentEvent, Done, KodaAgent, TextDelta, ThinkingDelta,
    ToolResult, ToolStart, Usage,
)


class AnthropicAdapter(KodaAgent):
    def __init__(self, model: str) -> None:
        self._model = model
        self._client = AsyncAnthropic()
        self._cancel = asyncio.Event()

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        self._cancel.set()

    async def stream(
        self, message: str, history: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        self._cancel.clear()

        messages = list(history) + [{"role": "user", "content": message}]

        async with self._client.messages.stream(
            model=self._model,
            max_tokens=4096,
            messages=messages,
            # tools=...   # add your tool schemas here
        ) as stream:
            async for event in stream:
                if self._cancel.is_set():
                    break

                # Text deltas
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield TextDelta(content=event.delta.text)
                    elif event.delta.type == "thinking_delta":
                        yield ThinkingDelta(content=event.delta.thinking)

                # Tool calls
                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        yield ToolStart(
                            tool_id=block.id,
                            name=block.name,
                            arguments=block.input or {},
                        )

                elif event.type == "message_stop":
                    usage = event.message.usage
                    yield Usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    )

            final = await stream.get_final_message()
            yield Done(usage=Usage(
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
            ))


def build(model: str = "claude-sonnet-4-6") -> AnthropicAdapter:
    """Factory for `koda --agent anthropic_adapter.build`."""
    return AnthropicAdapter(model=model)
```

## 2. LangGraph Passthrough (no custom adapter class)

When you already have a compiled LangGraph graph, don't write a
`KodaAgent` class — just return the graph. KODA auto-wraps it.

```python
# my_graph.py
import os
from pathlib import Path

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from my_tools import my_custom_tools  # your tools


def build(model: str = "openai:gpt-4o"):
    """Returns a compiled LangGraph graph — KODA wraps it in LangGraphAdapter."""
    ws = Path(os.environ.get("KODA_WORKSPACE", "./agent_workspace"))
    ws.mkdir(parents=True, exist_ok=True)

    return create_react_agent(
        model=init_chat_model(model),
        tools=my_custom_tools,
        prompt="You are a helpful assistant.",
        checkpointer=MemorySaver(),
    )
```

Launch with `koda --agent my_graph.build --model openai:gpt-4o`. No
other changes needed.

## 3. Remote HTTP/SSE Adapter

Talk to an agent running as an HTTP service (could be FastAPI, LangServe,
or anything that speaks SSE). The server contract is whatever you want —
this adapter translates.

See `examples/fastapi_agent.py` in the KODA repo for a working reference
server that matches this adapter.

```python
# http_adapter.py
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

import httpx

from koda.agent_api import (
    AgentEvent, Done, KodaAgent, TextDelta,
    ToolResult, ToolStart, Usage,
)


class HttpSSEAdapter(KodaAgent):
    def __init__(self, model: str, endpoint: str) -> None:
        self._model = model
        self._endpoint = endpoint.rstrip("/")
        self._cancel = asyncio.Event()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300))

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        self._cancel.set()

    async def stream(
        self, message: str, history: list[dict[str, Any]],
    ) -> AsyncIterator[AgentEvent]:
        self._cancel.clear()

        payload = {"message": message, "history": history, "model": self._model}

        async with self._client.stream(
            "POST", f"{self._endpoint}/stream", json=payload,
        ) as r:
            async for line in r.aiter_lines():
                if self._cancel.is_set():
                    break
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                kind = event.get("type")
                if kind == "text_delta":
                    yield TextDelta(content=event["content"])
                elif kind == "tool_start":
                    yield ToolStart(
                        tool_id=event["tool_id"],
                        name=event["name"],
                        arguments=event.get("arguments", {}),
                    )
                elif kind == "tool_result":
                    yield ToolResult(
                        tool_id=event["tool_id"],
                        output=event["output"],
                        is_error=event.get("is_error", False),
                    )
                elif kind == "usage":
                    yield Usage(**event["usage"])

        yield Done()


def build(model: str = "custom:my-model") -> HttpSSEAdapter:
    endpoint = os.environ.get("KODA_REMOTE_URL", "http://localhost:8000")
    return HttpSSEAdapter(model=model, endpoint=endpoint)
```

## Testing your adapter

Every adapter above ships as a single importable module with a `build(model=...)`
factory. Point KODA at it:

```bash
koda --agent anthropic_adapter.build --model claude-sonnet-4-6
koda --agent my_graph.build --model openai:gpt-4o
KODA_REMOTE_URL=http://localhost:8000 koda --agent http_adapter.build
```

For a programmatic smoke test that prints every event, use
`scripts/validate.py` shipped with this skill.

## Cursor / Claude Code users

Drop one of the examples into your project root as `koda_agent.py`,
then invoke KODA from Cursor's / Claude Code's terminal panel:

```bash
koda --agent koda_agent.build --model <your_model>
```

That's the whole integration story — no IDE-specific plugins needed.
KODA is a plain CLI.
