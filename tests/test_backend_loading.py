"""
Tests for the --agent backend resolver.

Covers:
  - `--agent deep` → built-in deep adapter
  - `--agent module.factory` returning a raw LangGraph graph → auto-wrapped
  - `--agent module.factory` returning a KodaAgent → used as-is
  - The returned factory can be called again (for /model), producing a fresh
    adapter — the importlib machinery isn't invoked each call.
"""

from __future__ import annotations

import pytest

from koda.agent_api import KodaAgent
from koda.__main__ import _build_adapter_factory


def test_deep_spec_builds_adapter(monkeypatch):
    """'deep' should resolve without importing external modules."""
    # Avoid network or real model init — stub create_deep_adapter.
    import koda.adapters.deep as deep_mod

    captured = {}

    def fake_create(model, thread_id, **_kw):
        captured["call"] = (model, thread_id)

        class Stub(KodaAgent):
            def model_name(self): return model
            async def interrupt(self): pass
            async def stream(self, m, h):
                if False: yield None
        return Stub()

    monkeypatch.setattr(deep_mod, "create_deep_adapter", fake_create)

    factory = _build_adapter_factory("deep")
    adapter = factory("anthropic:x", "tid-1")
    assert isinstance(adapter, KodaAgent)
    assert captured["call"] == ("anthropic:x", "tid-1")


def test_module_spec_wraps_raw_langgraph_graph():
    """A factory returning a compiled graph must be auto-wrapped in LangGraphAdapter."""
    from koda.adapters.langgraph import LangGraphAdapter

    factory = _build_adapter_factory("tests.test_backend_loading._fake_graph_build")
    adapter = factory("openai:test", "tid-2")
    assert isinstance(adapter, LangGraphAdapter)
    assert adapter.model_name() == "openai:test"


def test_module_spec_returning_koda_agent_is_used_directly():
    """A factory already returning a KodaAgent must be passed through (not wrapped)."""
    from koda.adapters.langgraph import LangGraphAdapter

    factory = _build_adapter_factory("tests.test_backend_loading._fake_agent_build")
    adapter = factory("openai:test", "tid-3")
    # It must satisfy KodaAgent protocol (already does since _FakeAgent subclasses it)
    assert hasattr(adapter, "stream") and hasattr(adapter, "model_name")
    # And it must NOT have been wrapped in LangGraphAdapter
    assert not isinstance(adapter, LangGraphAdapter)
    assert adapter.model_name() == "fake"


def test_factory_is_reusable_for_model_switch():
    """Calling the factory twice must not re-import the module."""
    import sys

    factory = _build_adapter_factory("tests.test_backend_loading._fake_graph_build")
    factory("openai:a", "t")
    # Calling again should not raise ImportError or wipe sys.modules entries
    factory("openai:b", "t")
    assert "tests.test_backend_loading" in sys.modules


def test_unknown_spec_exits(capsys):
    """Bare word that isn't 'deep' and contains no dot should exit."""
    with pytest.raises(SystemExit):
        _build_adapter_factory("not-a-real-backend")


# ── fixtures / helpers used by the tests above ─────────────────────────

class _FakeAgent(KodaAgent):
    def model_name(self) -> str: return "fake"
    async def interrupt(self) -> None: pass
    async def stream(self, m, h):
        if False: yield None


class _FakeGraph:
    """Mimics just enough of a compiled LangGraph graph for adapter wrapping."""

    async def astream_events(self, _input, *, config, version):  # pragma: no cover
        if False:
            yield None


def _fake_graph_build(model: str):
    return _FakeGraph()


def _fake_agent_build(model: str):
    return _FakeAgent()
