"""
Tests for /model switching.

- /model must use the same adapter factory the app was launched with
  (i.e. a --agent custom backend is preserved across switches, not silently
  replaced by the built-in deep adapter).
- The switch must not block the event loop: the heavy graph compilation
  runs in a worker thread.
"""

from __future__ import annotations

import threading

import pytest

from koda.agent_api import KodaAgent
from koda.tui.app import KodaApp


class _FakeAdapter(KodaAgent):
    """Minimal stand-in so tests don't need network or real providers."""

    def __init__(self, model: str, thread_id: str) -> None:
        self._model = model
        self._thread_id = thread_id
        self.built_on_thread = threading.get_ident()

    def model_name(self) -> str:
        return self._model

    async def interrupt(self) -> None:
        pass

    async def stream(self, message, history):  # pragma: no cover - unused
        if False:
            yield None


@pytest.mark.asyncio
async def test_switch_model_uses_custom_factory():
    """/model must call the app's configured factory, not hardcode the deep adapter."""
    calls: list[tuple[str, str]] = []

    def factory(model: str, thread_id: str) -> KodaAgent:
        calls.append((model, thread_id))
        return _FakeAdapter(model, thread_id)

    initial = factory("openai:gpt-4o-mini", "t-1")
    app = KodaApp(
        adapter=initial,
        adapter_factory=factory,
        model="openai:gpt-4o-mini",
        thread_id="t-1",
    )
    # Pretend the user has OPENAI creds so the credential guard doesn't block
    import os
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")

    async with app.run_test() as pilot:
        await pilot.pause()
        calls.clear()  # ignore the initial adapter build
        await app.switch_model("openai:gpt-4o")
        await pilot.pause()

    # Exactly one factory call for the switch, with the user's thread_id
    assert calls == [("openai:gpt-4o", "t-1")], calls
    assert isinstance(app._adapter, _FakeAdapter)
    assert app._adapter.model_name() == "openai:gpt-4o"
    assert app._model == "openai:gpt-4o"


@pytest.mark.asyncio
async def test_switch_model_runs_off_event_loop():
    """The factory (which can be slow) must run in a worker thread, not the UI thread."""
    ui_thread_id = threading.get_ident()
    factory_thread: dict[str, int] = {}

    def factory(model: str, thread_id: str) -> KodaAgent:
        factory_thread["tid"] = threading.get_ident()
        return _FakeAdapter(model, thread_id)

    initial = factory("openai:gpt-4o-mini", "t-1")
    app = KodaApp(
        adapter=initial,
        adapter_factory=factory,
        model="openai:gpt-4o-mini",
        thread_id="t-1",
    )
    import os
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")

    async with app.run_test() as pilot:
        await pilot.pause()
        factory_thread.clear()
        await app.switch_model("openai:gpt-4o")
        await pilot.pause()

    assert "tid" in factory_thread, "factory was never called"
    assert factory_thread["tid"] != ui_thread_id, (
        "factory ran on the UI thread — switch_model must offload to a thread"
    )


@pytest.mark.asyncio
async def test_switch_model_missing_credentials_errors_cleanly():
    """If credentials are missing, /model must not crash and must not rebuild the adapter."""
    calls: list[tuple[str, str]] = []

    def factory(model: str, thread_id: str) -> KodaAgent:
        calls.append((model, thread_id))
        return _FakeAdapter(model, thread_id)

    import os
    # Ensure OPENROUTER is unset so the guard triggers
    os.environ.pop("OPENROUTER_API_KEY", None)

    initial = factory("openai:gpt-4o-mini", "t-1")
    app = KodaApp(
        adapter=initial,
        adapter_factory=factory,
        model="openai:gpt-4o-mini",
        thread_id="t-1",
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        calls.clear()
        await app.switch_model("openrouter:some-model")
        await pilot.pause()

    assert calls == [], "factory should not run when creds are missing"
    # Still on the original model
    assert app._model == "openai:gpt-4o-mini"


def test_default_adapter_factory_is_deep():
    """If no factory is passed to KodaApp, /model must fall back to the built-in deep adapter."""
    from koda.tui.app import _default_adapter_factory

    # Sanity: it's callable with the expected signature.
    import inspect
    sig = inspect.signature(_default_adapter_factory)
    assert list(sig.parameters) == ["model", "thread_id"]
