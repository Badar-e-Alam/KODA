"""
Tests for provider model caching.

- warm_cache_in_background must return a started daemon thread.
- get_available_models must be cache-fast (return quickly after warm-up).
- The cached-only path must never raise or hit the network.
"""

from __future__ import annotations

import threading
import time

import pytest


def test_warm_cache_returns_quickly():
    """Must not block: model discovery is on a daemon thread."""
    from koda.model_config import warm_cache_in_background

    t0 = time.perf_counter()
    warm_cache_in_background()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.2, f"warm_cache_in_background blocked for {elapsed:.2f}s"


def test_warm_cache_starts_daemon_thread():
    """The warmer thread must be a daemon so it never blocks shutdown."""
    from koda.model_config import warm_cache_in_background

    before = {t.ident for t in threading.enumerate()}
    warm_cache_in_background()
    after_threads = [t for t in threading.enumerate() if t.ident not in before]
    # The worker thread name is stable
    named = [t for t in after_threads if t.name == "koda-model-warm"]
    assert named, "expected a 'koda-model-warm' thread to be started"
    assert all(t.daemon for t in named), "warm thread must be a daemon"


def test_get_available_models_is_fast_after_warm():
    """After the warm-up has populated the cache, discovery must be trivial."""
    from koda.model_config import get_available_models, invalidate_models_cache

    # First call populates the in-memory cache (may hit disk, still fast because
    # cached-only path doesn't do network I/O).
    invalidate_models_cache()
    get_available_models()

    t0 = time.perf_counter()
    for _ in range(50):
        get_available_models()
    elapsed = time.perf_counter() - t0
    # Should be well under 100ms for 50 calls.
    assert elapsed < 0.5, f"50 cached lookups took {elapsed:.2f}s"


def test_cached_only_never_hits_network(monkeypatch):
    """get_models_cached_only must not perform network I/O."""
    from koda import provider_models

    def boom(*_a, **_kw):
        raise AssertionError("network call in cached-only path")

    monkeypatch.setattr(provider_models, "_fetch", boom)
    # Exercise one known provider. Even with no cache, _fetch must not be called.
    result = provider_models.get_models_cached_only("openai")
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_model_completer_non_blocking_on_first_keystroke():
    """Typing '/model ' should never hang — the completer is cache-only."""
    from koda.tui.app import KodaApp
    from koda.tui.completers import complete

    async with KodaApp().run_test() as pilot:
        await pilot.pause()
        t0 = time.perf_counter()
        result = complete("/model ", 7)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, f"completer blocked for {elapsed:.2f}s"
        assert result is not None
        _, _, title = result
        assert title == "Models"
