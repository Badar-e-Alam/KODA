"""KODA adapter for the standalone ``CodingAgent`` class.

This adapter drives the hand-rolled think→act→observe loop in
``coding_agent/agent.py`` (via :meth:`CodingAgent.stream_events`) instead
of the OpenAI-Agents SDK ``Runner``. Concretely, that means the loop,
the tool dispatch, the streaming, and the langfuse tracing are all
under our control — and the same code path serves both the TUI and
the standalone CLI.

Wire-up details:

* ``--model openai:NAME`` and ``--model ollama:NAME[:tag]`` map to the
  right ``base_url``/``api_key``/model id.
* The system prompt (``SYSTEM_PROMPT`` + ``AGENTS.md`` + git context) is
  recomposed by ``CodingAgent`` on every turn, so long-running TUI
  sessions reflect the *current* repo state, not a stale snapshot.
* Cancellation is delegated: ``BaseAdapter._cancel`` is the same
  ``asyncio.Event`` ``CodingAgent.stream_events`` checks between chunks
  and tool calls.

Two surfaces hang off the same class:

* The TUI uses :meth:`KodaAgent.stream` (inherited from ``BaseAdapter``)
  to consume typed ``AgentEvent``\\s.
* The eval harness (and any sync caller) uses :meth:`KodaAgent.run`,
  which drives ``stream_events`` under ``asyncio.run`` and returns
  the final assistant text as a string.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

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

# Tunables (env-overridable).
_MAX_TURNS = int(os.getenv("KODA_CODING_AGENT_MAX_TURNS", "200"))
_TEMPERATURE = float(os.getenv("KODA_CODING_AGENT_TEMPERATURE", "0.7"))


def _ensure_agent_importable() -> None:
    """The ``coding_agent/`` directory uses script-style imports
    (``from system_prompt import ...``) and has no ``__init__.py``,
    so we add it to ``sys.path`` to make those resolve."""
    p = str(_AGENT_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _normalise_model_spec(model: str) -> str:
    """Add an ``ollama:`` prefix when the spec is provider-less.

    Phase 3 routes everything through ``coding_agent.clients.build_chat_model``
    which requires a ``"provider:model"`` shape. Bare strings (like just
    ``"kimi-k2.5"``) used to default to Ollama in the old _resolve_provider
    path; preserve that behaviour.
    """
    return model if ":" in model else f"ollama:{model}"


class KodaAgent(BaseAdapter):
    """KODA adapter that drives ``CodingAgent.stream_events``.

    Exposes two entry points:

    * :meth:`stream` — async generator of ``AgentEvent``\\s, used by
      the TUI (inherited from ``BaseAdapter``).
    * :meth:`run` — sync, returns the final assistant text. Used by the
      eval harness in ``koda-evals/`` and by any non-TUI caller that
      just wants one prompt → one string.
    """

    def __init__(self, model: str, thread_id: str | None = None) -> None:
        super().__init__(model=model, thread_id=thread_id)
        _ensure_agent_importable()

        from agent import CodingAgent, _TOOLS  # type: ignore
        from system_prompt import SYSTEM_PROMPT  # type: ignore

        # Forward the model spec to ``CodingAgent`` unchanged — provider
        # routing now lives in ``coding_agent.clients.build_chat_model``.
        # Bootstrap is always off through the adapter (TUI can't block
        # startup on a model-driven AGENTS.md write; eval harness sets
        # ``KODA_DISABLE_BOOTSTRAP=1`` for the same reason).
        self._coding_agent = CodingAgent(
            model=_normalise_model_spec(model),
            tools=_TOOLS,
            system_prompt=SYSTEM_PROMPT,
            project_root=os.getcwd(),
            auto_create_agents_md=False,
            temperature=_TEMPERATURE,
        )
        self._reported_model = model
        self._extractors = (_extract_event,)

    def model_name(self) -> str:
        return self._reported_model

    # ── streaming surface (TUI) ─────────────────────────────────────────

    async def _native_stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[Any]:
        """Forward ``CodingAgent`` events to ``BaseAdapter.stream``.

        ``self._cancel`` is shared with ``CodingAgent.stream_events``, so
        ``await self.interrupt()`` from the TUI cleanly stops the loop
        between chunks, between tool calls, and between steps.
        """
        async for ev in self._coding_agent.stream_events(
            message=message,
            history=history,
            max_steps=_MAX_TURNS,
            cancel_event=self._cancel,
            session_id=self._thread_id,
        ):
            if self._cancel.is_set():
                break
            yield ev

    # ── sync surface (evals / one-shot CLI callers) ─────────────────────

    def run(
        self,
        prompt: str,
        *,
        cwd: str | os.PathLike | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        max_steps: int = _MAX_TURNS,
    ) -> str:
        """Run a single prompt to completion, return the final assistant text.

        Drives the same ``stream_events`` loop the TUI uses, but blocks
        until the agent emits ``done`` (or hits ``max_steps``). All
        ``text_delta`` events are accumulated; the ``done.content``
        from the final step is preferred when present.

        ``cwd`` resets the underlying ``CodingAgent.project_root`` so
        that AGENTS.md / git-context lookup uses the eval workdir
        rather than the directory where the adapter was constructed.
        Tools that resolve relative paths (read_file, run_shell, …)
        still operate on ``os.getcwd()`` — callers are responsible for
        ``os.chdir(workdir)`` before calling ``run``.
        """
        if cwd is not None:
            new_root = Path(cwd).resolve()
            self._coding_agent.project_root = new_root
            self._coding_agent.agents_md_path = new_root / "AGENTS.md"

        async def _drive() -> str:
            self._cancel.clear()
            text_parts: list[str] = []
            final_text = ""
            async for ev in self._coding_agent.stream_events(
                message=prompt,
                history=[],
                max_steps=max_steps,
                cancel_event=self._cancel,
                session_id=session_id or self._thread_id,
                user_id=user_id,
            ):
                t = ev.get("type") if isinstance(ev, dict) else None
                if t == "text_delta":
                    text_parts.append(ev.get("content") or "")
                elif t == "done":
                    final_text = ev.get("content") or ""
                    break
            return final_text or "".join(text_parts)

        return asyncio.run(_drive())


# Back-compat alias — older imports (and the factory below) reference
# ``CodingAgentAdapter``. The class was renamed to ``KodaAgent`` to match
# the eval harness contract (``from koda.adapters.coding_agent import
# KodaAgent``).
CodingAgentAdapter = KodaAgent


# ── Extractor ───────────────────────────────────────────────────────────


def _extract_event(ev: Any) -> Iterable[AgentEvent] | None:
    """Map one ``CodingAgent`` dict event to KODA event dataclass(es)."""
    if not isinstance(ev, dict):
        return None
    t = ev.get("type")

    if t == "text_delta":
        content = ev.get("content") or ""
        return (TextDelta(content=content),) if content else None

    if t == "tool_start":
        args = ev.get("arguments")
        if not isinstance(args, dict):
            args = {}
        return (
            ToolStart(
                tool_id=str(ev.get("tool_id") or ""),
                name=str(ev.get("name") or "tool"),
                arguments=args,
            ),
        )

    if t == "tool_result":
        return (
            ToolResult(
                tool_id=str(ev.get("tool_id") or ""),
                output=str(ev.get("output") or ""),
                is_error=bool(ev.get("is_error", False)),
            ),
        )

    if t == "usage":
        # We forward STEP usage as a delta-shaped Usage event. BaseAdapter's
        # merge_usage takes max-ish semantics, so for cumulative behaviour
        # we send the running total instead of the per-step delta.
        run_total = ev.get("run_total") or {}
        return (
            Usage(
                input_tokens=int(run_total.get("prompt_tokens", 0) or 0),
                output_tokens=int(run_total.get("completion_tokens", 0) or 0),
            ),
        )

    # `done` and `cancelled` produce no extra events — BaseAdapter
    # already emits a final Done with the accumulated Usage when the
    # stream ends.
    return None


# ── Factory ─────────────────────────────────────────────────────────────


def create_coding_agent_adapter(
    model: str = "ollama:qwen3-coder:480b",
    thread_id: str | None = None,
) -> KodaAgent:
    """Build the coding-agent adapter. Used by ``koda --agent coding_agent``."""
    return KodaAgent(model=model, thread_id=thread_id)


__all__ = ["KodaAgent", "CodingAgentAdapter", "create_coding_agent_adapter"]
