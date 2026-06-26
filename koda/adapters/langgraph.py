"""LangGraph → KodaAgent adapter.

Wraps any compiled LangGraph graph (anything with ``astream_events(v2)``)
and translates its event stream to KODA's typed `AgentEvent`s. All the
reusable plumbing (cancel, usage, error handling, final Done) lives in
``BaseAdapter``; this file just supplies:

  1. ``_native_stream`` — how to iterate the graph
  2. three tiny extractors — one per LangGraph event type we care about
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, AsyncIterator, Iterable

from langgraph.types import Command

from koda.adapters.base import (
    BaseAdapter,
    model_supports_thinking,
    model_supports_vision,
    tools_from_objects,
)
from koda.agent_api import (
    AgentDescription,
    AgentEvent,
    PermissionItem,
    PermissionRequest,
    TextDelta,
    ThinkingDelta,
    ToolDescription,
    ToolResult,
    ToolStart,
    Usage,
)
from koda.tools import permissions as _perms

_log = logging.getLogger("koda.adapters.langgraph")

# LangGraph defaults to 25 steps per turn, which runs out on multi-step
# research tasks (scrape → parse → convert → write PDF → verify). Bump
# to 100 by default; override with KODA_RECURSION_LIMIT.
_DEFAULT_RECURSION_LIMIT = int(os.environ.get("KODA_RECURSION_LIMIT", "1000"))

# Marker yielded by ``_native_stream`` to inject a ``PermissionRequest``
# into the typed event stream. It's a plain dict so the other extractors
# (which all start with ``event.get("event") != …``) skip it cleanly.
_PERM_KEY = "__koda_permission__"


class LangGraphAdapter(BaseAdapter):
    """Wrap a compiled LangGraph graph as a KodaAgent."""

    _backend = "langgraph"

    def __init__(self, graph: Any, model: str, thread_id: str | None = None) -> None:
        super().__init__(model=model, thread_id=thread_id)
        self._graph = graph
        # One-shot seed guard for graphs without a checkpointer — see
        # _native_stream for the rationale. Flipped to True after the
        # first turn so subsequent turns don't re-forward history.
        self._seeded: bool = False
        # Set while the graph is paused on a human-in-the-loop interrupt and
        # we're waiting for the TUI to deliver the user's decisions. Resolved
        # by ``provide_decisions`` (called on the UI loop from the prompt's
        # choice callback). ``None`` whenever no prompt is outstanding.
        self._pending_decision_future: asyncio.Future | None = None
        # Bind per-instance so subclasses (or tests) can swap them out.
        self._extractors = (
            _extract_permission,
            _extract_chat_stream,
            _extract_chat_model_end,
            _extract_tool_start,
            _extract_tool_end,
        )

    def mark_seeded(self) -> None:
        """Declare that the upstream graph already has this thread's history.

        Use after rebuilding the adapter for a thread that already has a
        checkpoint (model switch, session resume, memory reload). Without
        this, the next ``_native_stream`` would prepend the caller's
        ``history`` argument to the input messages and LangGraph's
        ``add_messages`` reducer would duplicate every prior turn on top
        of what the checkpointer replays.
        """
        self._seeded = True

    def describe(self) -> AgentDescription:
        return AgentDescription(
            name=self._model,
            backend=self._backend,
            supports_thinking=model_supports_thinking(self._model),
            supports_vision=model_supports_vision(self._model),
            tools=_introspect_graph_tools(self._graph),
            system_prompt_preview=None,
        )

    def _extra_callbacks(self) -> list[Any]:
        """Per-adapter callback hook injected into every ``astream_events`` call.

        Default is empty so the base adapter stays agent-agnostic. Subclasses
        override to attach tracing (e.g. ``CodingAgentAdapter`` returns
        ``coding_agent.tracing.langfuse_callbacks()`` so every turn lands in
        Langfuse without the TUI knowing).
        """
        return []

    async def _native_stream(
        self, message: str, history: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Drive the underlying LangGraph graph for one user turn.

        History is **not** forwarded: LangGraph's checkpointer already
        persists the thread's message state under ``thread_id`` and the
        default ``add_messages`` reducer would just append duplicates
        (no stable IDs on plain role/content dicts). We hand it only the
        new user message. Prior messages replay automatically from the
        checkpoint; prompt caches stay hot across turns.

        The ``history`` argument is kept in the signature for the
        stateless-adapter case (e.g. a graph built without a checkpointer
        by a user via ``--agent``). If the graph has no thread state yet
        and history is non-empty, we seed the first call with it.

        Pause/resume: if the graph hits a human-in-the-loop ``interrupt()``
        (a gated tool awaiting approval), the ``astream_events`` pass ends
        cleanly with the graph paused and its state checkpointed. We detect
        that via ``aget_state``, surface a ``PermissionRequest`` (emitting a
        marker the permission extractor turns into the typed event), await
        the user's decisions, then re-enter ``astream_events`` with
        ``Command(resume=…)`` to continue from the checkpoint. The loop
        repeats until the graph finishes with no pending interrupt.
        """
        await self._ensure_graph()

        config: dict[str, Any] = {
            "configurable": {"thread_id": self._thread_id},
            "recursion_limit": _DEFAULT_RECURSION_LIMIT,
        }
        extra_cbs = self._extra_callbacks()
        if extra_cbs:
            config["callbacks"] = extra_cbs

        input_messages: list[dict[str, Any]] = [
            {"role": "user", "content": message}
        ]
        if history and not self._seeded:
            # One-shot seed for graphs without a checkpointer (or when we
            # resumed a session from disk — state starts empty even though
            # our UI has history). Subsequent turns skip this path.
            input_messages = _history_to_langchain(history) + input_messages
        self._seeded = True

        # First pass takes the new message; resume passes take a Command.
        graph_input: Any = {"messages": input_messages}

        while True:
            async for event in self._graph.astream_events(
                graph_input, config=config, version="v2"
            ):
                # Closest-to-source cancel check. BaseAdapter.stream races
                # ``__anext__`` against ``self._cancel.wait()`` for the
                # primary cut-off; this extra check breaks the LangGraph
                # loop the moment the next event lands rather than waiting
                # for the race to be re-armed. Belt and suspenders.
                if self._cancel.is_set():
                    return
                yield event

            if self._cancel.is_set():
                return

            # The pass ended: the graph either finished or paused on an
            # interrupt. ``aget_state`` tells us which (and carries the
            # interrupt payload). Graphs without a checkpointer can't be
            # inspected — treat those as "done" so they keep working.
            state = await self._aget_state_safe(config)
            if state is None or not getattr(state, "next", None):
                return

            interrupts = _collect_hitl_interrupts(state)
            if not interrupts:
                # Paused, but not on a HITL approval request we understand.
                # Returning avoids an infinite resume loop / hang.
                _log.warning("graph paused with no HITL interrupt; ending turn")
                return

            # Plan decisions PER interrupt. Parallel subagents each pause on
            # their own interrupt, so there can be several pending at once;
            # we collect the items needing the user across all of them.
            #   per_interrupt: [(interrupt_id, [decision|None per action])]
            #   human:         [(interrupt_id, slot_index, PermissionItem)]
            per_interrupt, human = _plan_decisions(interrupts)

            if human:
                loop = asyncio.get_running_loop()
                self._pending_decision_future = loop.create_future()
                # Surface the human-needed items to the TUI via the typed
                # stream (the permission extractor turns this marker into a
                # PermissionRequest). Then await the user's answers, in order.
                yield {_PERM_KEY: [item for _iid, _i, item in human]}
                try:
                    answers = await self._pending_decision_future
                finally:
                    # Clear on every exit (normal, cancel, or abandon) so a
                    # stray late provide_decisions() can't resolve a stale
                    # future on the next pause.
                    self._pending_decision_future = None
                if answers is None:  # cancelled / abandoned
                    return
                slots_by_id = dict(per_interrupt)
                for (iid, slot_idx, _item), answer in zip(human, answers):
                    slots_by_id[iid][slot_idx] = answer

            # Every slot must be filled (one decision per gated tool call) or
            # the HITL middleware raises on the count mismatch.
            if any(d is None for _iid, slots in per_interrupt for d in slots):
                _log.error("incomplete permission decisions; ending turn")
                return

            # Resume. A single pending interrupt takes LangGraph's simple
            # ``resume=<value>`` form; multiple pending interrupts (parallel
            # subagents) MUST be resumed with a map keyed by interrupt id —
            # LangGraph raises "you must specify the interrupt id when
            # resuming" otherwise.
            if len(per_interrupt) == 1:
                _iid, slots = per_interrupt[0]
                graph_input = Command(resume={"decisions": slots})
            else:
                graph_input = Command(resume={
                    iid: {"decisions": slots} for iid, slots in per_interrupt
                })

    def provide_decisions(self, decisions: list[dict[str, Any]] | None) -> None:
        """Deliver the user's permission decisions back to a paused turn.

        Called on the UI event loop from the prompt's choice callback (see
        ``KodaApp._on_permission_choice``). Resolves the future that
        ``_native_stream`` is awaiting so the graph resumes. ``decisions``
        is one entry per *human-needed* item, in the order they were sent;
        ``None`` aborts (treated like a cancel). Safe to call when no prompt
        is outstanding — it's a no-op.
        """
        fut = self._pending_decision_future
        if fut is not None and not fut.done():
            fut.set_result(decisions)

    async def _aget_state_safe(self, config: dict[str, Any]) -> Any | None:
        """``aget_state`` that returns None instead of raising.

        Stateless graphs (no checkpointer, e.g. a user ``--agent`` with no
        saver) raise on ``aget_state``; those simply don't support pausing,
        so we report "no state" and let the turn end normally.
        """
        try:
            return await self._graph.aget_state(config)
        except Exception:
            _log.debug("aget_state unavailable (no checkpointer?)", exc_info=True)
            return None


# ── Extractors ──────────────────────────────────────────────────────
#
# Each takes one raw LangGraph event dict and yields zero or more
# AgentEvents. They are stateless — any accumulation (Usage, tool
# pairing) is handled by BaseAdapter or downstream in the TUI.


def _extract_permission(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    """Turn the ``_PERM_KEY`` marker (yielded by ``_native_stream`` when the
    graph pauses on a gated tool) into a typed ``PermissionRequest``."""
    items = event.get(_PERM_KEY)
    if items is None:
        return None
    return (PermissionRequest(items=list(items)),)


def _extract_chat_stream(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    if event.get("event") != "on_chat_model_stream":
        return None
    chunk = (event.get("data") or {}).get("chunk")
    if chunk is None:
        return None
    return _chat_chunk_events(chunk)


def _extract_chat_model_end(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    """Capture token usage from the final ``AIMessage``.

    Many local / OpenAI-compatible providers (Ollama, vLLM, LM Studio, …)
    do **not** populate ``usage_metadata`` on streaming ``AIMessageChunk``s
    — the field only lands on the complete ``AIMessage`` delivered by the
    ``on_chat_model_end`` event. Without this extractor the status bar's
    token counters stay at zero for every non-Anthropic backend, even
    though the provider reports usage perfectly well.

    For backends that also emit usage on stream chunks (Anthropic), this is
    a harmless no-op: ``merge_usage`` uses max-ish (non-zero-overrides)
    semantics, so re-reporting the same cumulative totals just re-sets them
    to the same value — no double counting.
    """
    if event.get("event") != "on_chat_model_end":
        return None
    output = (event.get("data") or {}).get("output")
    if output is None:
        return None
    meta = getattr(output, "usage_metadata", None)
    if not meta:
        return None
    details_in = meta.get("input_token_details") or {}
    return (
        Usage(
            input_tokens=meta.get("input_tokens", 0) or 0,
            output_tokens=meta.get("output_tokens", 0) or 0,
            cache_read_tokens=details_in.get("cache_read", 0) or 0,
            cache_write_tokens=details_in.get("cache_creation", 0) or 0,
        ),
    )


def _extract_tool_start(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    if event.get("event") != "on_tool_start":
        return None
    tool_id = event.get("run_id") or uuid.uuid4().hex
    name = event.get("name") or "tool"
    args = (event.get("data") or {}).get("input") or {}
    if not isinstance(args, dict):
        args = {"input": args}
    return (ToolStart(tool_id=tool_id, name=name, arguments=args),)


def _extract_tool_end(event: dict[str, Any]) -> Iterable[AgentEvent] | None:
    if event.get("event") != "on_tool_end":
        return None
    tool_id = event.get("run_id") or uuid.uuid4().hex
    output = (event.get("data") or {}).get("output")
    text, is_error = _stringify_tool_output(output)
    return (ToolResult(tool_id=tool_id, output=text, is_error=is_error),)


# ── Helpers ─────────────────────────────────────────────────────────

def _collect_hitl_interrupts(state: Any) -> list[tuple[str, dict[str, Any]]]:
    """``[(interrupt_id, HITLRequest)]`` for each pending HITL interrupt.

    ``HumanInTheLoopMiddleware`` interrupts with a ``HITLRequest`` dict —
    ``{"action_requests": [...], "review_configs": [...]}`` — stored on the
    pending tasks. We keep only dicts that look like that (so an unrelated
    ``interrupt()`` from a custom tool isn't mistaken for an approval batch),
    **and** keep each interrupt's id: parallel subagents each pause on their
    own interrupt, and LangGraph requires resuming by id when more than one
    is pending.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for task in getattr(state, "tasks", ()) or ():
        for it in getattr(task, "interrupts", ()) or ():
            val = getattr(it, "value", None)
            iid = getattr(it, "id", None)
            if iid is not None and isinstance(val, dict) and "action_requests" in val:
                out.append((iid, val))
    return out


def _plan_decisions(
    interrupts: list[tuple[str, dict[str, Any]]],
) -> tuple[
    list[tuple[str, list[dict[str, Any] | None]]],
    list[tuple[str, int, PermissionItem]],
]:
    """Plan resume decisions across every pending interrupt.

    Returns ``(per_interrupt, human)`` where:
      * ``per_interrupt`` is ``[(interrupt_id, slots)]`` and ``slots`` has one
        entry per ``action_request`` (``None`` for slots awaiting the user),
      * ``human`` is ``[(interrupt_id, slot_index, PermissionItem)]`` for the
        slots whose policy verdict is ``"ask"``.

    Auto-resolves approve/reject via ``koda.tools.permissions.decide`` (mode +
    session-allow); only the ``"ask"`` slots are surfaced to the user.
    """
    per_interrupt: list[tuple[str, list[dict[str, Any] | None]]] = []
    human: list[tuple[str, int, PermissionItem]] = []

    for iid, hitl in interrupts:
        action_requests = list(hitl.get("action_requests") or [])
        review_configs = list(hitl.get("review_configs") or [])
        slots: list[dict[str, Any] | None] = [None] * len(action_requests)
        for i, ar in enumerate(action_requests):
            name = ar.get("name") or "tool"
            args = ar.get("args") or {}
            allowed: tuple[str, ...] = ("approve", "reject")
            if i < len(review_configs):
                allowed = tuple(review_configs[i].get("allowed_decisions") or allowed)
            verdict = _perms.decide(name, args)
            if verdict == "approve":
                slots[i] = {"type": "approve"}
            elif verdict == "reject":
                slots[i] = {"type": "reject", "message": _perms.reject_message(name)}
            else:  # "ask" — needs the human
                human.append((iid, i, PermissionItem(
                    tool_name=name,
                    args=args if isinstance(args, dict) else {"input": args},
                    allowed_decisions=allowed,
                    description=str(ar.get("description") or ""),
                )))
        per_interrupt.append((iid, slots))
    return per_interrupt, human


def _history_to_langchain(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pass through {role, content} dicts — LangGraph accepts them directly."""
    return [h for h in history if h.get("role") in ("user", "assistant", "system")]


def _chat_chunk_events(chunk: Any) -> list[AgentEvent]:
    """Translate one AIMessageChunk into zero or more AgentEvents."""
    out: list[AgentEvent] = []

    # Usage metadata (appears on the final chunk with most providers)
    meta = getattr(chunk, "usage_metadata", None)
    if meta:
        details_in = meta.get("input_token_details") or {}
        out.append(
            Usage(
                input_tokens=meta.get("input_tokens", 0) or 0,
                output_tokens=meta.get("output_tokens", 0) or 0,
                cache_read_tokens=details_in.get("cache_read", 0) or 0,
                cache_write_tokens=details_in.get("cache_creation", 0) or 0,
            )
        )

    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        # Anthropic-style multimodal: list of typed blocks
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if text:
                    out.append(TextDelta(content=text))
            elif btype in ("thinking", "reasoning"):
                text = block.get("thinking") or block.get("text") or ""
                if text:
                    out.append(ThinkingDelta(content=text))
    elif isinstance(content, str) and content:
        out.append(TextDelta(content=content))

    extra = getattr(chunk, "additional_kwargs", None) or {}
    reasoning = extra.get("reasoning_content") or extra.get("thinking")
    if isinstance(reasoning, str) and reasoning:
        out.append(ThinkingDelta(content=reasoning))

    return out


def _stringify_tool_output(output: Any) -> tuple[str, bool]:
    """Return (text, is_error) for a LangGraph tool end event's output."""
    if output is None:
        return "", False
    for attr in ("content", "text"):
        val = getattr(output, attr, None)
        if val is not None:
            status = getattr(output, "status", None)
            return str(val), status == "error"
    if isinstance(output, dict):
        if "content" in output:
            return str(output["content"]), bool(output.get("is_error", False))
        return repr(output), False
    return str(output), False


def _introspect_graph_tools(graph: Any) -> tuple[ToolDescription, ...]:
    """Best-effort extraction of the tools wired into a compiled LangGraph graph.

    ``create_react_agent`` exposes its tool node at a few different paths
    depending on the LangGraph version. We try each in turn and silently
    return an empty tuple if nothing matches — the TUI's ``/tools`` command
    will just report "no tool surface" instead of crashing.
    """
    if graph is None:
        return ()

    # Common attribute paths across LangGraph versions and graph styles.
    # The deepagents path (StateNodeSpec.runnable: ToolNode) is tried first
    # because that's the layout KODA's coding-agent uses; older
    # ``create_react_agent`` layouts (``.data.tools_by_name``) follow.
    candidates: list[Any] = []
    for getter in (
        lambda g: g.builder.nodes["tools"].runnable.tools_by_name.values(),
        lambda g: g.nodes["tools"].runnable.tools_by_name.values(),
        lambda g: g.nodes["tools"].data.tools_by_name.values(),
        lambda g: g.nodes["tools"].data.tools,
        lambda g: g.builder.nodes["tools"].data.tools_by_name.values(),
        lambda g: g.builder.nodes["tools"].data.tools,
        lambda g: g.get_graph().nodes["tools"].data.tools,
    ):
        try:
            tools = list(getter(graph))
        except Exception:
            continue
        if tools:
            candidates = tools
            break

    if not candidates:
        return ()
    return tools_from_objects(candidates)


__all__ = ["LangGraphAdapter"]
