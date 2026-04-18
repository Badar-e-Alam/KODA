#!/usr/bin/env python
"""Smoke-test a KODA adapter factory.

Usage:
    python validate.py <module.path.factory> [--model MODEL] [--prompt PROMPT]

Example:
    python validate.py examples.koda_agent.build \\
        --model openai:gpt-5-nano \\
        --prompt "say hi in 2 words"

Prints every event the adapter yields and returns a non-zero exit code
if any of the KODA contract invariants are violated:

  - at least one visible event (TextDelta/ThinkingDelta/ToolStart)
  - exactly one Done at the end
  - every ToolStart has a matching ToolResult (same tool_id)
  - model_name() returns a non-empty string
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
import time
from typing import Any

from koda.agent_api import (
    Done,
    KodaAgent,
    TextDelta,
    ThinkingDelta,
    ToolResult,
    ToolStart,
    Usage,
)


def _resolve_factory(spec: str):
    module_path, _, name = spec.rpartition(".")
    if not module_path:
        print(f"[validate] invalid spec: {spec!r} (expected module.path.factory)")
        sys.exit(2)
    mod = importlib.import_module(module_path)
    if not hasattr(mod, name):
        print(f"[validate] module {module_path!r} has no attribute {name!r}")
        sys.exit(2)
    return getattr(mod, name)


async def run(factory, model: str, prompt: str) -> int:
    print(f"[validate] building adapter via {factory.__module__}.{factory.__name__}(model={model!r})")
    t0 = time.perf_counter()
    result = factory(model=model)
    t1 = time.perf_counter()
    print(f"[validate] factory returned {type(result).__name__} in {(t1-t0)*1000:.0f}ms")

    # Auto-wrap a raw LangGraph graph (same as KODA does internally)
    if not isinstance(result, KodaAgent):
        from koda.adapters.langgraph import LangGraphAdapter
        result = LangGraphAdapter(graph=result, model=model)
        print("[validate] wrapped in LangGraphAdapter")

    adapter: KodaAgent = result
    mname = adapter.model_name()
    assert isinstance(mname, str) and mname, f"model_name() returned {mname!r}"
    print(f"[validate] model_name() -> {mname!r}")

    print(f"[validate] streaming prompt: {prompt!r}")
    visible = 0
    done_count = 0
    tool_starts: dict[str, str] = {}
    tool_results: set[str] = set()
    total_usage: dict[str, int] = {"in": 0, "out": 0, "cache_r": 0, "cache_w": 0}

    t_start = time.perf_counter()
    first_event_t: float | None = None

    async for ev in adapter.stream(prompt, []):
        now = time.perf_counter()
        if first_event_t is None:
            first_event_t = now
            print(f"[validate]   first event after {(now - t_start)*1000:.0f}ms")

        if isinstance(ev, TextDelta):
            visible += 1
            print(f"  TextDelta: {ev.content!r}")
        elif isinstance(ev, ThinkingDelta):
            visible += 1
            print(f"  ThinkingDelta: {ev.content[:60]!r}{'…' if len(ev.content) > 60 else ''}")
        elif isinstance(ev, ToolStart):
            visible += 1
            tool_starts[ev.tool_id] = ev.name
            print(f"  ToolStart: {ev.name}({ev.arguments})  id={ev.tool_id[:8]}")
        elif isinstance(ev, ToolResult):
            tool_results.add(ev.tool_id)
            print(f"  ToolResult[{ev.tool_id[:8]}] err={ev.is_error}: {ev.output[:60]!r}")
        elif isinstance(ev, Usage):
            total_usage["in"] += ev.input_tokens
            total_usage["out"] += ev.output_tokens
            total_usage["cache_r"] += ev.cache_read_tokens
            total_usage["cache_w"] += ev.cache_write_tokens
            print(f"  Usage: ↑{ev.input_tokens} ↓{ev.output_tokens} cache r={ev.cache_read_tokens}")
        elif isinstance(ev, Done):
            done_count += 1
            print(f"  Done: usage={ev.usage}")
            if ev.usage:
                total_usage["in"] += ev.usage.input_tokens
                total_usage["out"] += ev.usage.output_tokens

    t_end = time.perf_counter()
    print(f"[validate] stream finished in {(t_end - t_start)*1000:.0f}ms")

    # ─ Contract checks ─────────────────────────────────────────
    errors: list[str] = []
    if visible == 0:
        errors.append("no visible events — TUI would get stuck on 'Thinking…'")
    if done_count == 0:
        errors.append("no Done event — TUI would never dismiss thinking indicator")
    if done_count > 1:
        errors.append(f"{done_count} Done events — exactly one required")

    orphan_starts = set(tool_starts) - tool_results
    if orphan_starts:
        errors.append(f"{len(orphan_starts)} unmatched ToolStart(s): {[tool_starts[t] for t in orphan_starts]}")
    orphan_results = tool_results - set(tool_starts)
    if orphan_results:
        errors.append(f"{len(orphan_results)} orphan ToolResult(s) with no matching start")

    if errors:
        print("\n[validate] ✗ CONTRACT VIOLATIONS:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"\n[validate] ✓ OK — visible={visible}, tools={len(tool_starts)}, usage={total_usage}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("factory", help="module.path.factory — e.g. examples.koda_agent.build")
    ap.add_argument("--model", default="openai:gpt-5-nano", help="model id to pass to factory(model=...)")
    ap.add_argument("--prompt", default="Say hi in exactly three words.", help="prompt to stream")
    args = ap.parse_args()

    factory = _resolve_factory(args.factory)
    return asyncio.run(run(factory, args.model, args.prompt))


if __name__ == "__main__":
    sys.exit(main())
