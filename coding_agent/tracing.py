"""Langfuse tracing wiring for `coding_agent`.

A LangChain ``CallbackHandler`` is the most ergonomic way to plug
Langfuse v4 into a LangGraph agent: attach it via
``config={"callbacks": [...]}`` on each ``invoke``/``stream`` call and
every LLM call, tool call, and chain step is traced automatically
without changes inside the graph.

The handler is lazy + cached so processes without Langfuse installed or
configured pay nothing.
"""

# ``from __future__ import annotations`` enables PEP 604 union syntax
# (e.g. ``BaseCallbackHandler | None``) in type annotations even on
# Python < 3.10.
from __future__ import annotations

import logging  # stdlib logging; used to emit debug / warning messages
import os  # env-var access for Langfuse credentials and host
from functools import lru_cache  # caches the handler for the process lifetime

# ``BaseCallbackHandler`` is the abstract base every LangChain callback
# must implement — we use it as the return-type annotation and to keep
# callers decoupled from the concrete Langfuse class.
from langchain_core.callbacks import BaseCallbackHandler

# Module-level logger named after this file so downstream log
# configuration can route/level tracing messages independently.
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
    # Early exit: if the public key isn't set the user hasn't configured
    # Langfuse, so there's nothing to wire up.
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
        # Langfuse isn't installed — tracing is opt-in, so just log at
        # debug level and move on silently.
        _log.debug("langfuse not installed; tracing disabled")
        return None
    try:
        # Instantiate the handler — it reads LANGFUSE_PUBLIC_KEY,
        # LANGFUSE_SECRET_KEY, and LANGFUSE_HOST from env at init time.
        # Broad except (BLE001) is intentional: we never want a
        # mis-configured tracer to crash the agent itself.
        return CallbackHandler()
    except Exception as exc:  # noqa: BLE001
        # Something went wrong during init (bad credentials, network
        # issue, etc.). Log a warning so ops can spot it, then degrade
        # gracefully — the agent continues without tracing.
        _log.warning("Langfuse CallbackHandler init failed: %s", exc)
        return None


def langfuse_callbacks() -> list[BaseCallbackHandler]:
    """List of callback handlers to attach to a graph invocation.

    Returns an empty list when Langfuse isn't configured — pass it
    unconditionally as ``callbacks=…`` and tracing only kicks in when
    ``LANGFUSE_PUBLIC_KEY`` is set.
    """
    handler = _build_langfuse_handler()  # cached after first call
    # Wrap in a list when present; callers can always pass the result
    # directly as ``config={"callbacks": langfuse_callbacks()}`` without
    # checking whether tracing is enabled.
    return [handler] if handler is not None else []
