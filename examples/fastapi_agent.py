"""
Example FastAPI server that works with KODA's HTTP/SSE agent.

This shows how to build a streaming agent backend that KODA can connect to.
Replace the placeholder logic with your actual agent (LangChain, Anthropic, etc).

Run:
    pip install fastapi uvicorn
    uvicorn examples.fastapi_agent:app --port 8000

Connect KODA:
    python -m koda --agent http://localhost:8000/stream
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="KODA Agent Backend")


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


def _event(type: str, **kwargs) -> str:
    """Format a single SSE event line."""
    data = {"type": type, **kwargs}
    return f"data: {json.dumps(data)}\n\n"


async def _generate(request: ChatRequest) -> AsyncIterator[str]:
    """
    Stream events to KODA.

    Replace this with your actual agent logic. For example:

        # Anthropic
        import anthropic
        client = anthropic.AsyncAnthropic()
        async with client.messages.stream(...) as stream:
            async for text in stream.text_stream:
                yield _event("text_delta", content=text)

        # LangChain
        from langchain.agents import AgentExecutor
        async for event in agent.astream_events(input, version="v2"):
            if event["event"] == "on_chat_model_stream":
                yield _event("text_delta", content=event["data"]["chunk"].content)
    """
    message = request.message

    # ── Thinking ──
    yield _event("thinking_delta", content=f"Processing: {message}")
    await asyncio.sleep(0.3)

    # ── Simulated tool call ──
    yield _event(
        "tool_start",
        tool_id="t1",
        name="process_request",
        arguments={"input": message[:50]},
    )
    await asyncio.sleep(0.5)
    yield _event(
        "tool_result",
        tool_id="t1",
        output=f"Processed successfully. Input length: {len(message)} chars.",
        is_error=False,
    )
    await asyncio.sleep(0.2)

    # ── Stream response text ──
    response = (
        f"Hello from the FastAPI agent backend!\n\n"
        f"You said: **{message}**\n\n"
        f"This is a demo server. Replace `_generate()` with your real agent logic.\n\n"
        f"History length: {len(request.history)} messages."
    )

    # Stream word by word
    for word in response.split():
        yield _event("text_delta", content=word + " ")
        await asyncio.sleep(0.03)

    # ── Done ──
    yield _event(
        "done",
        usage={
            "input_tokens": len(message.split()) * 4,
            "output_tokens": len(response.split()) * 4,
        },
    )
    yield "data: [DONE]\n\n"


@app.post("/stream")
async def stream_chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _generate(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "fastapi-demo"}
