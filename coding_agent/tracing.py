"""Langfuse tracing wiring for `coding_agent`.

A LangChain ``CallbackHandler`` is the most ergonomic way to plug
Langfuse v4 into a LangGraph agent: attach it via
``config={"callbacks": [...]}`` on each ``invoke``/``stream`` call and
every LLM call, tool call, and chain step is traced automatically
without changes inside the graph.

The handler is lazy + cached so processes without Langfuse installed or
configured pay nothing.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from langchain_core.callbacks import BaseCallbackHandler

_log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _build_langfuse_handler() -> BaseCallbackHandler | None:
    """Return a Langfuse ``CallbackHandler`` if Langfuse is configured, else None.

    Detection is ``LANGFUSE_PUBLIC_KEY`` in env — that's the one
    credential Langfuse always needs. Other vars
    (``LANGFUSE_SECRET_KEY``, ``LANGFUSE_HOST``) are read by the SDK
    directly.

    Cached for the process lifetime: ``CallbackHandler`` holds a shared
    Langfuse client; creating a new one per call wastes resources and
    fragments traces.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None

    # Langfuse v4 reads ``LANGFUSE_HOST``; older project ``.env`` files
    # tend to use ``LANGFUSE_BASE_URL``. Promote one to the other if only
    # the legacy name is set so traces ship to the right place even when
    # callers haven't migrated yet.
    if not os.environ.get("LANGFUSE_HOST"):
        legacy = os.environ.get("LANGFUSE_BASE_URL")
        if legacy:
            os.environ["LANGFUSE_HOST"] = legacy

    try:
        # Lazy: Langfuse v4 → ``langfuse.langchain.CallbackHandler``. We
        # don't import at module top-level so users without Langfuse
        # installed (or configured) never pay the cost.
        from langfuse.langchain import CallbackHandler
    except ImportError:
        _log.debug("langfuse not installed; tracing disabled")
        return None
    try:
        return CallbackHandler()
    except Exception as exc:  # noqa: BLE001
        _log.warning("Langfuse CallbackHandler init failed: %s", exc)
        return None


def langfuse_callbacks() -> list[BaseCallbackHandler]:
    """List of callback handlers to attach to a graph invocation.

    Returns an empty list when Langfuse isn't configured — pass it
    unconditionally as ``callbacks=…`` and tracing only kicks in when
    ``LANGFUSE_PUBLIC_KEY`` is set.
    """
    handler = _build_langfuse_handler()
    return [handler] if handler is not None else []
