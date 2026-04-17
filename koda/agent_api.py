"""
KODA agent contract.

Any agent that implements the `KodaAgent` Protocol can plug into the KODA TUI.

The TUI consumes a stream of typed `AgentEvent`s; adapters translate native
agent formats (LangGraph, Anthropic SDK, OpenAI, HTTP/SSE, ...) into this
stream. See `koda/adapters/` for reference implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol, Union, runtime_checkable


@dataclass
class TextDelta:
    """Incremental assistant text."""

    content: str


@dataclass
class ThinkingDelta:
    """Incremental reasoning (extended thinking / chain-of-thought)."""

    content: str


@dataclass
class ToolStart:
    """A tool invocation has begun."""

    tool_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result returned by a tool."""

    tool_id: str
    output: str
    is_error: bool = False


@dataclass
class Usage:
    """Token usage snapshot. May arrive mid-stream (cumulative) or in Done."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Done:
    """Stream complete. Final usage attached if the backend reports it."""

    usage: Usage | None = None


AgentEvent = Union[TextDelta, ThinkingDelta, ToolStart, ToolResult, Usage, Done]


@runtime_checkable
class KodaAgent(Protocol):
    """Every KODA-compatible agent must implement this Protocol.

    Adapters wrap backend-specific agents (LangGraph graphs, Anthropic SDK
    clients, HTTP/SSE services, ...) and expose this interface to the TUI.
    """

    def model_name(self) -> str:
        """Human-readable model identifier shown in the status bar."""
        ...

    def stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[AgentEvent]:
        """Yield events for a single user turn.

        `history` is a list of `{role, content}` dicts (OpenAI/Anthropic
        compatible). The adapter is responsible for any format translation.
        """
        ...

    async def interrupt(self) -> None:
        """Cancel the current stream. Idempotent; safe to call anytime."""
        ...
