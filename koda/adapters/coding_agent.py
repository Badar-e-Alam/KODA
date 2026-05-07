"""KODA adapter for the OpenAI-Agents-SDK ``coding_agent``.

Wraps the `` from agent import coding_agent`` defined in ``coding_agent/agent.py`` and exposes it
as a :class:`KodaAgent` that the KODA TUI can drive.

Usage::

    koda --agent coding_agent
    koda --agent coding_agent --model openai:gpt-4o
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from agent import coding_agent  # type: ignore
from tools import set_approval_mode  # type: ignore
from koda.adapters.base import BaseAdapter
from koda.agent_api import (
    AgentEvent,
    TextDelta,
    ToolResult,
    ToolStart,
    Usage,
)

_log = logging.getLogger("koda.adapters.coding_agent")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENT_DIR = _PROJECT_ROOT / "coding_agent"


def _ensure_agent_importable() -> None:
    """The user's ``coding_agent/`` folder uses script-style imports
    (``from system_prompt import ...``) and has no ``__init__.py``,
    so we add it to ``sys.path`` to make those resolve."""
    p = str(_AGENT_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


class CodingAgentAdapter(BaseAdapter):
    """Wraps the OpenAI-Agents-SDK ``coding_agent`` for KODA."""

    def __init__(self, model: str, thread_id: str | None = None) -> None:
        super().__init__(model=model, thread_id=thread_id)
        _ensure_agent_importable()

      

        # Under KODA the TUI owns stdin, so the agent's interactive approval
        # prompt (asyncio.to_thread(input, ...)) would hang the run. KODA is
        # already the trust boundary for this session, so auto-approve here.
        set_approval_mode("yolo")

        # The agent is built on the OpenAI Agents SDK and its model field expects
        # a bare OpenAI model name (e.g. "gpt-4o"). If the user passed an OpenAI
        # spec via --model we honor it; for non-OpenAI specs we leave the agent's
        # original model alone and report that name through model_name() so the
        # status bar still reflects what's actually running.
        provider, _, bare = model.partition(":")
        if provider == "openai" and bare:
            try:
                coding_agent.model = bare
            except Exception:
                _log.warning("Could not override coding_agent.model to %s", bare)
            self._reported_model = model
        else:
            current = getattr(coding_agent, "model", None) or "gpt-4o"
            self._reported_model = f"openai:{current}" if ":" not in str(current) else str(current)

        self._agent = coding_agent
        self._extractors = (_extract_event,)
        self._stream_handle: Any = None  # RunResultStreaming, for cancellation

    def model_name(self) -> str:
        return self._reported_model

    async def _native_stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[Any]:
        from agents import Runner

        # Build the SDK input: the user's prior history (role/content dicts) +
        # the new user message. The OpenAI Agents SDK accepts that shape directly.
        sdk_input: list[dict[str, Any]] = []
        for h in history:
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant", "system") and isinstance(content, str):
                sdk_input.append({"role": role, "content": content})
        sdk_input.append({"role": "user", "content": message})

        result = Runner.run_streamed(self._agent, sdk_input, max_turns=50)
        self._stream_handle = result

        try:
            async for ev in result.stream_events():
                if self._cancel.is_set():
                    try:
                        result.cancel()
                    except Exception:
                        pass
                    break
                yield ev
        finally:
            self._stream_handle = None

    async def interrupt(self) -> None:
        await super().interrupt()
        handle = self._stream_handle
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass


# ── Extractor ───────────────────────────────────────────────────────────


def _extract_event(ev: Any) -> Iterable[AgentEvent] | None:
    """Map an openai-agents StreamEvent to KODA events."""
    etype = getattr(ev, "type", None)

    # 1) Streaming text deltas from the underlying LLM.
    if etype == "raw_response_event":
        data = getattr(ev, "data", None)
        data_type = getattr(data, "type", None)
        if data_type == "response.output_text.delta":
            delta = getattr(data, "delta", "") or ""
            if delta:
                return (TextDelta(content=delta),)
        return None

    # 2) Run-item events: tool calls, tool outputs.
    if etype == "run_item_stream_event":
        name = getattr(ev, "name", None)
        item = getattr(ev, "item", None)

        if name == "tool_called" and item is not None:
            return _tool_start_from_item(item)

        if name == "tool_output" and item is not None:
            return _tool_result_from_item(item)

    return None


def _tool_start_from_item(item: Any) -> Iterable[AgentEvent] | None:
    raw = getattr(item, "raw_item", None)
    tool_name = getattr(item, "tool_name", None)
    call_id = getattr(item, "call_id", None)

    if tool_name is None and isinstance(raw, dict):
        tool_name = raw.get("name")
    if call_id is None and isinstance(raw, dict):
        call_id = raw.get("call_id") or raw.get("id")

    if not call_id:
        call_id = uuid.uuid4().hex
    if not tool_name:
        tool_name = "tool"

    arguments: dict[str, Any] = {}
    raw_args: Any = None
    if isinstance(raw, dict):
        raw_args = raw.get("arguments")
    else:
        raw_args = getattr(raw, "arguments", None)
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                arguments = parsed
            else:
                arguments = {"value": parsed}
        except json.JSONDecodeError:
            arguments = {"raw": raw_args}
    elif isinstance(raw_args, dict):
        arguments = raw_args

    return (ToolStart(tool_id=str(call_id), name=str(tool_name), arguments=arguments),)


def _tool_result_from_item(item: Any) -> Iterable[AgentEvent] | None:
    raw = getattr(item, "raw_item", None)
    output = getattr(item, "output", None)
    call_id = getattr(item, "call_id", None)

    if call_id is None and isinstance(raw, dict):
        call_id = raw.get("call_id") or raw.get("id")
    if not call_id:
        call_id = uuid.uuid4().hex

    if isinstance(output, str):
        text = output
    elif output is None:
        text = ""
        if isinstance(raw, dict):
            text = str(raw.get("output", ""))
    else:
        try:
            text = json.dumps(output, default=str)
        except Exception:
            text = str(output)

    is_error = isinstance(text, str) and text.lstrip().startswith("[error]")
    return (ToolResult(tool_id=str(call_id), output=text, is_error=is_error),)


# ── Factory ─────────────────────────────────────────────────────────────


def create_coding_agent_adapter(
    model: str = "openai:gpt-4o",
    thread_id: str | None = None,
) -> CodingAgentAdapter:
    """Build the coding-agent adapter. Used by ``koda --agent coding_agent``."""
    return CodingAgentAdapter(model=model, thread_id=thread_id)


__all__ = ["CodingAgentAdapter", "create_coding_agent_adapter"]
