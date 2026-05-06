"""KODA adapter for the LangGraph-based ``coding_agent``.

The agent itself is a compiled LangGraph graph (see ``coding_agent/agent.py``).
All event translation, cancellation, and usage accounting is handled by
``LangGraphAdapter`` — this file is just the factory that resolves the
graph for a given ``provider:model`` spec and hands it off.

Usage::

    koda --agent coding_agent
    koda --agent coding_agent --model anthropic:claude-sonnet-4-6
    koda --agent coding_agent --model ollama:llama3.1
"""

from __future__ import annotations

import sys
from pathlib import Path

from koda.adapters.langgraph import LangGraphAdapter

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AGENT_DIR = _PROJECT_ROOT / "coding_agent"


def _ensure_agent_importable() -> None:
    """``coding_agent/`` uses script-style imports (no ``__init__.py``),
    so we add it to ``sys.path`` before importing the agent module."""
    p = str(_AGENT_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def create_coding_agent_adapter(
    model: str = "openai:gpt-4o",
    thread_id: str | None = None,
) -> LangGraphAdapter:
    """Build the coding agent's LangGraph graph and wrap it as a KodaAgent."""
    _ensure_agent_importable()
    from agent import build_coding_agent  # type: ignore

    graph = build_coding_agent(model)
    return LangGraphAdapter(graph=graph, model=model, thread_id=thread_id)


__all__ = ["create_coding_agent_adapter"]
