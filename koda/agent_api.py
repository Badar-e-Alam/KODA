"""
KODA agent contract.

Any agent that implements the `KodaAgent` Protocol can plug into the KODA TUI.

The TUI consumes a stream of typed `AgentEvent`s; adapters translate native
agent formats (LangGraph, Anthropic SDK, OpenAI, HTTP/SSE, ...) into this
stream. See `koda/adapters/` for reference implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, Union, runtime_checkable


@dataclass(frozen=True)
class ToolDescription:
    """Public-facing description of a single tool the adapter exposes."""

    name: str
    description: str = ""


@dataclass(frozen=True)
class AgentDescription:
    """Capability + tool metadata an adapter reports to the TUI.

    Returned by the optional :meth:`KodaAgent.describe` method and rendered
    by ``/agents``, ``/tools``, and the status-bar badges. Adapters that
    don't implement ``describe`` get a synthesized default via
    :func:`describe_agent` — so every field has a safe fallback value.
    """

    name: str
    backend: str = "unknown"
    supports_thinking: bool = False
    supports_vision: bool = False
    tools: tuple[ToolDescription, ...] = field(default_factory=tuple)
    system_prompt_preview: str | None = None


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


@dataclass
class PermissionItem:
    """A single tool call awaiting the user's approval.

    Emitted (wrapped in a :class:`PermissionRequest`) when a checkpointed
    LangGraph graph hits a human-in-the-loop ``interrupt()`` before running
    a gated tool. ``allowed_decisions`` mirrors the graph's review config
    (typically ``["approve", "reject"]``); the TUI maps its allow / always /
    deny buttons onto those.
    """

    tool_name: str
    args: dict[str, Any]
    allowed_decisions: tuple[str, ...] = ("approve", "reject")
    description: str = ""


@dataclass
class PermissionRequest:
    """The agent has paused on one or more gated tool calls.

    The graph's state is checkpointed at this point — nothing has run and
    nothing is blocked. The TUI renders a prompt per item, collects the
    user's choices, and hands them back via ``adapter.provide_decisions``,
    which resumes the graph from the checkpoint with ``Command(resume=…)``.
    """

    items: list[PermissionItem]


AgentEvent = Union[
    TextDelta, ThinkingDelta, ToolStart, ToolResult, Usage, Done, PermissionRequest
]


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

    # NOTE: Adapters MAY also implement::
    #
    #     def describe(self) -> AgentDescription: ...
    #
    # to surface backend/capabilities/tools to the TUI (rendered by
    # ``/agents``, ``/tools``, and the status-bar capability badges).
    # It is deliberately NOT part of the runtime-checkable Protocol so
    # adapters predating the extension continue to satisfy
    # ``isinstance(x, KodaAgent)`` — callers should go through
    # :func:`describe_agent` which handles the absent / failing cases.


def describe_agent(agent: "KodaAgent") -> AgentDescription:
    """Safely call ``agent.describe()`` with a minimal fallback.

    Adapters predating the ``describe`` extension — or implementations
    that fail mid-introspection — fall back to a description containing
    just the model name. This keeps the TUI's adapter-aware paths
    forward-compatible without forcing every implementation to override.
    """
    describe = getattr(agent, "describe", None)
    if describe is None:
        return AgentDescription(name=agent.model_name())
    try:
        result = describe()
    except Exception:
        return AgentDescription(name=agent.model_name())
    if not isinstance(result, AgentDescription):
        return AgentDescription(name=agent.model_name())
    return result
