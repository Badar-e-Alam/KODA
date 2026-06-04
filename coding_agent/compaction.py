"""Conversation compaction for the coding agent.

Two complementary layers keep long sessions inside the model's context
window, both built on the summarization engine that ships with
``deepagents``:

1. **Automatic trimming (always on).** ``create_deep_agent`` already wires
   an auto-summarization middleware that compacts older turns and truncates
   oversized ``write_file`` / ``edit_file`` arguments once the conversation
   nears the model's limit. On top of that we add
   :class:`~langchain.agents.middleware.ContextEditingMiddleware` with a
   :class:`~langchain.agents.middleware.ClearToolUsesEdit` rule — the same
   strategy Claude Code uses: when the transcript crosses a token threshold,
   stale tool *results* are replaced by a ``[cleared]`` placeholder while the
   most recent few are kept verbatim. Cheap, lossless for recent context, and
   it reclaims the bytes that dominate a coding session (big file reads / shell
   output). See :func:`build_context_editing_middleware`.

2. **Manual ``/compact`` (user-triggered).** :func:`compact_thread` summarizes
   everything except the recent tail *right now*, regardless of how full the
   window is. It reuses deepagents' summarization engine but bypasses the
   model-facing eligibility gate so the command always does something useful.
   The result is written to the same ``_summarization_event`` state key the
   auto-engine reads, so the two interoperate: nothing is lost — the raw
   messages stay in checkpointer state, only the *effective* view the model
   sees is condensed.

The engine's compaction helpers (``_determine_cutoff_index`` etc.) are
"private" in deepagents but stable across the 0.x line; we keep every use of
them inside this one module so a future deepagents change is a single-file fix.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import ClearToolUsesEdit, ContextEditingMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from deepagents.middleware.summarization import SummarizationMiddleware

_log = logging.getLogger("coding_agent.compaction")

# How many of the most recent messages a manual ``/compact`` leaves untouched.
# Smaller = more aggressive compaction. Override with KODA_COMPACT_KEEP_MESSAGES.
_DEFAULT_KEEP_MESSAGES = 6

# Context-editing (Claude-Code-style tool-result clearing) defaults. The
# trigger is an absolute token count because not every provider exposes a
# model profile for fraction-based limits. Override / disable via env.
_DEFAULT_CONTEXT_EDIT_TRIGGER_TOKENS = 100_000
_DEFAULT_CONTEXT_EDIT_KEEP = 3


def _as_chat_model(model: str | BaseChatModel) -> BaseChatModel:
    """Coerce a model spec or instance into a ``BaseChatModel``.

    ``coding_agent.model.resolve_model`` returns a string for cloud providers
    (e.g. ``"anthropic:claude-sonnet-4-6"``) and an instance for Ollama-family
    specs. deepagents' summarization engine needs a concrete instance, so we
    resolve strings here via ``init_chat_model``.
    """
    if isinstance(model, BaseChatModel):
        return model
    return init_chat_model(model)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("Ignoring non-integer %s=%r; using %d", name, raw, default)
        return default


# ── Layer 1: automatic context editing (Claude-Code-style) ─────────────


def build_context_editing_middleware() -> ContextEditingMiddleware | None:
    """Build the always-on tool-result trimmer, or ``None`` if disabled.

    Mirrors Claude Code's context editing: once the transcript exceeds
    ``KODA_CONTEXT_EDIT_TRIGGER_TOKENS`` (default 100k), tool *results* older
    than the most recent ``KODA_CONTEXT_EDIT_KEEP`` (default 3) are swapped for
    a ``[cleared]`` placeholder. Tool *inputs* are left intact so the model
    still sees what it asked for, just not the bulky output.

    Set ``KODA_CONTEXT_EDIT_TRIGGER_TOKENS=0`` (or ``off``) to disable.
    """
    raw = os.environ.get("KODA_CONTEXT_EDIT_TRIGGER_TOKENS", "")
    if raw.strip().lower() in {"0", "off", "false", "none"}:
        _log.debug("Context-editing middleware disabled via env")
        return None

    trigger = _env_int(
        "KODA_CONTEXT_EDIT_TRIGGER_TOKENS", _DEFAULT_CONTEXT_EDIT_TRIGGER_TOKENS
    )
    keep = _env_int("KODA_CONTEXT_EDIT_KEEP", _DEFAULT_CONTEXT_EDIT_KEEP)
    return ContextEditingMiddleware(
        edits=[
            ClearToolUsesEdit(
                trigger=trigger,
                keep=keep,
                # Keep the call arguments; only the (often huge) results go.
                clear_tool_inputs=False,
                placeholder="[cleared]",
            )
        ]
    )


# ── Layer 2: manual /compact ───────────────────────────────────────────


def build_manual_compaction_engine(
    model: str | BaseChatModel, backend: Any
) -> SummarizationMiddleware:
    """Build the summarization engine that powers the ``/compact`` command.

    This engine is *not* added to the agent's middleware chain — it's a
    standalone utility :func:`compact_thread` drives on demand. (deepagents
    already wires its own auto-summarization middleware into the graph; both
    read/write the shared ``_summarization_event`` state key, so a manual
    compaction is honored by the auto-engine on the next turn.)

    ``keep`` defaults to the most recent ``KODA_COMPACT_KEEP_MESSAGES``
    messages; the ``trigger`` is irrelevant here because :func:`compact_thread`
    never consults the auto-trigger — a user asking to compact means compact.
    """
    keep_messages = _env_int("KODA_COMPACT_KEEP_MESSAGES", _DEFAULT_KEEP_MESSAGES)
    return SummarizationMiddleware(
        model=_as_chat_model(model),
        backend=backend,
        keep=("messages", keep_messages),
        # Don't trim the messages we feed to the summarizer — we want the
        # fullest possible context in the summary itself.
        trim_tokens_to_summarize=None,
    )


# deepagents runs ``after_model`` middleware hooks in reverse registration
# order, and ``TodoListMiddleware`` is always registered first — so its
# ``.after_model`` node runs *last* and is the one whose conditional edge
# reaches END. Attributing an ``aupdate_state`` to it (rather than "model")
# leaves the graph idle (``next == ()``) when the last message is a final
# assistant turn, instead of parked on a phantom pending node that would
# corrupt the following turn.
_IDLE_NODE = "TodoListMiddleware.after_model"
_FALLBACK_NODE = "model"


def _idle_as_node(graph: Any) -> str:
    """Pick the ``as_node`` that leaves the graph idle after a state write."""
    try:
        nodes = graph.get_graph().nodes
    except Exception:  # pragma: no cover - introspection is best-effort
        return _FALLBACK_NODE
    return _IDLE_NODE if _IDLE_NODE in nodes else _FALLBACK_NODE


@dataclass
class CompactionResult:
    """Outcome of a manual ``/compact`` invocation."""

    compacted: bool
    summarized_messages: int
    summary: str
    reason: str


async def compact_thread(
    graph: Any, engine: SummarizationMiddleware, config: dict[str, Any]
) -> CompactionResult:
    """Compact the conversation for ``config``'s thread, right now.

    Summarizes every message except the recent tail (per the engine's ``keep``
    policy) and records the result in the ``_summarization_event`` state key via
    ``aupdate_state``. The raw messages remain in checkpointer state — only the
    *effective* view the model is shown on the next turn is condensed — so the
    operation is non-destructive and idempotent-ish (re-running compacts the
    newly-grown tail).

    Returns a :class:`CompactionResult` describing what happened so the TUI can
    report it. Never raises for the "nothing to do" cases; genuine summary
    failures propagate to the caller (the slash-command dispatcher logs them).
    """
    state = await graph.aget_state(config)
    values = getattr(state, "values", None) or {}
    messages = values.get("messages") or []
    event = values.get("_summarization_event")

    # Reconstruct what the model effectively sees today (prior summary +
    # post-cutoff tail), then decide a fresh cutoff over that view.
    effective = engine._apply_event_to_messages(messages, event)
    if len(effective) < 2:
        return CompactionResult(False, 0, "", "Nothing to compact yet.")

    cutoff = engine._determine_cutoff_index(effective)
    if cutoff <= 0:
        return CompactionResult(
            False, 0, "", "Conversation is short enough — nothing to compact."
        )

    to_summarize, _preserved = engine._partition_messages(effective, cutoff)
    summary = await engine._acreate_summary(to_summarize)

    # file_path=None: we deliberately skip backend offload here. The full
    # history is still in checkpointer state, and offload paths key off the
    # langgraph-run config contextvar which isn't set outside a graph step.
    summary_msg = engine._build_new_messages_with_path(summary, None)[0]
    state_cutoff = engine._compute_state_cutoff(event, cutoff)
    new_event = {
        "cutoff_index": state_cutoff,
        "summary_message": summary_msg,
        "file_path": None,
    }
    # ``as_node`` is required (several middleware nodes can write state); see
    # ``_idle_as_node`` for why we attribute the write to the terminal
    # after-model node so the graph stays idle for the next turn.
    await graph.aupdate_state(
        config, {"_summarization_event": new_event}, as_node=_idle_as_node(graph)
    )

    _log.info("Manual /compact summarized %d messages", len(to_summarize))
    return CompactionResult(
        compacted=True,
        summarized_messages=len(to_summarize),
        summary=summary,
        reason="ok",
    )


__all__ = [
    "CompactionResult",
    "build_context_editing_middleware",
    "build_manual_compaction_engine",
    "compact_thread",
]
