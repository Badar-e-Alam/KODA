"""One-shot run of the coding_agent against a single user prompt.

Drives the same graph the KODA TUI uses (``coding_agent.agent.build_agent``)
but skips the TUI entirely so the output is just streamed to stdout.
Used to validate the agent + model wiring without launching the
interactive terminal app.

    .venv/bin/python scripts/run_coding_agent_oneshot.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE importing the agent so the model factory sees the
# Ollama Cloud key when it resolves ``ollama:`` specs.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from langchain_core.messages import HumanMessage  # noqa: E402

from coding_agent.agent import build_agent, invocation_config  # noqa: E402


MODEL = "ollama:glm-5.1"
PROMPT = (
    "Read the file `coding_agent/backend.py` and give me a concise summary: "
    "what the module does, which classes it defines, and how the KODA "
    "permission gate is integrated. Keep the answer under 200 words and "
    "cite specific line ranges (e.g. `coding_agent/backend.py:120-140`)."
)


async def main() -> int:
    print(f"[harness] model = {MODEL}")
    print(f"[harness] cwd   = {Path.cwd()}")
    print(f"[harness] prompt:\n  {PROMPT}\n", flush=True)

    graph = await build_agent(model=MODEL, cwd=str(Path.cwd()))

    saw_text = False
    async for event in graph.astream_events(
        {"messages": [HumanMessage(content=PROMPT)]},
        version="v2",
        # Fresh thread_id per run so the LangGraph checkpointer can't
        # short-circuit the read with cached conversation state from a
        # previous invocation. Use a uuid prefix for human readability.
        # ``invocation_config`` merges in ``langfuse_callbacks()`` so the
        # run also gets traced when LANGFUSE_PUBLIC_KEY is set in env.
        config=invocation_config(thread_id=f"smoke-{uuid.uuid4().hex[:8]}"),
    ):
        et = event["event"]
        if et == "on_chat_model_stream":
            chunk = event["data"].get("chunk")
            text = getattr(chunk, "content", "") if chunk is not None else ""
            if isinstance(text, list):
                # Some providers stream content as a list of parts.
                text = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in text
                )
            if text:
                if not saw_text:
                    print("[agent]")
                    saw_text = True
                print(text, end="", flush=True)
        elif et == "on_tool_start":
            name = event.get("name", "?")
            args = event.get("data", {}).get("input", {})
            print(f"\n[tool-start] {name}({args})", flush=True)
        elif et == "on_tool_end":
            name = event.get("name", "?")
            out = event.get("data", {}).get("output")
            preview = str(out)[:200].replace("\n", " ")
            print(f"[tool-end]   {name} → {preview}…", flush=True)

    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
