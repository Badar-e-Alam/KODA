"""Anthropic SDK → KodaAgent adapter.

A second reference built directly on ``BaseAdapter`` to demonstrate the
pattern without LangGraph in the middle. Talks to
``anthropic.AsyncAnthropic().messages.stream(...)`` and translates its
native event stream into KODA events.

Requires the ``anthropic`` SDK (pip install anthropic). Activate with::

    koda --agent koda.adapters.anthropic.build --model claude-sonnet-4-6
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Iterable

from koda.adapters.base import (
    BaseAdapter,
    model_supports_thinking,
    model_supports_vision,
)
from koda.agent_api import (
    AgentDescription,
    AgentEvent,
    TextDelta,
    ThinkingDelta,
    Usage,
)

_DEFAULT_MAX_TOKENS = 4096


class AnthropicAdapter(BaseAdapter):
    """Thin async-streaming adapter over the Anthropic Messages API."""

    _backend = "anthropic-sdk"

    def __init__(
        self,
        model: str,
        thread_id: str | None = None,
        *,
        api_key: str | None = None,
        system: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        super().__init__(model=model, thread_id=thread_id)
        try:
            import anthropic  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "AnthropicAdapter requires `pip install anthropic`"
            ) from e
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._system = system
        self._max_tokens = max_tokens
        self._extractors = (_extract_delta, _extract_usage)

    def describe(self) -> AgentDescription:
        bare_model = self._model.split(":", 1)[-1]
        preview: str | None = None
        if self._system:
            preview = self._system.strip().split("\n", 1)[0][:200] or None
        return AgentDescription(
            name=bare_model,
            backend=self._backend,
            supports_thinking=model_supports_thinking(bare_model),
            supports_vision=model_supports_vision(bare_model),
            tools=(),  # this adapter doesn't wire tools
            system_prompt_preview=preview,
        )

    async def _native_stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[Any]:
        msgs = [
            {"role": h["role"], "content": h["content"]}
            for h in history
            if h.get("role") in ("user", "assistant")
        ]
        msgs.append({"role": "user", "content": message})

        kwargs: dict[str, Any] = {
            "model": self._model.split(":", 1)[-1],  # strip "anthropic:" prefix if present
            "max_tokens": self._max_tokens,
            "messages": msgs,
        }
        if self._system:
            kwargs["system"] = self._system

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                yield event


# ── Extractors ──────────────────────────────────────────────────────

def _extract_delta(event: Any) -> Iterable[AgentEvent] | None:
    """Anthropic emits ``content_block_delta`` for both text and thinking
    streams. Map them to the matching KODA event."""
    if getattr(event, "type", None) != "content_block_delta":
        return None
    delta = getattr(event, "delta", None)
    dtype = getattr(delta, "type", None)
    if dtype == "text_delta":
        return (TextDelta(content=getattr(delta, "text", "") or ""),)
    if dtype in ("thinking_delta", "input_json_delta"):
        text = getattr(delta, "thinking", None) or getattr(delta, "partial_json", "") or ""
        if text:
            return (ThinkingDelta(content=text),)
    return None


def _extract_usage(event: Any) -> Iterable[AgentEvent] | None:
    """Final usage totals come on ``message_delta`` / ``message_stop`` frames."""
    t = getattr(event, "type", None)
    if t not in ("message_delta", "message_stop"):
        return None
    usage = getattr(event, "usage", None) or getattr(getattr(event, "message", None), "usage", None)
    if usage is None:
        return None
    return (
        Usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        ),
    )


# ── Factory (for `koda --agent koda.adapters.anthropic.build`) ───────

def build(model: str = "claude-sonnet-4-6"):
    """Factory consumed by KODA's ``--agent`` resolver."""
    return AnthropicAdapter(model=model)


__all__ = ["AnthropicAdapter", "build"]
