"""Mouse mode: KODA releases the mouse by default so the terminal handles
native text selection, copy, and OSC-8 link clicks. Ctrl+O / --mouse / the
KODA_MOUSE env switch to Textual's in-app mouse (scroll, click-to-focus)."""

from __future__ import annotations

import pytest

from koda.tui.app import KodaApp, _resolve_mouse_default
from koda.tui.widgets.messages import AppMessage


@pytest.fixture(autouse=True)
def _clear_mouse_env(monkeypatch):
    monkeypatch.delenv("KODA_MOUSE", raising=False)


def test_resolve_mouse_default() -> None:
    # Native (released) by default.
    assert _resolve_mouse_default(None) is False
    # Explicit value wins over env.
    assert _resolve_mouse_default(True) is True
    assert _resolve_mouse_default(False) is False


def test_resolve_mouse_default_env(monkeypatch) -> None:
    monkeypatch.setenv("KODA_MOUSE", "1")
    assert _resolve_mouse_default(None) is True
    monkeypatch.setenv("KODA_MOUSE", "off")
    assert _resolve_mouse_default(None) is False


@pytest.mark.asyncio
async def test_default_is_native_release() -> None:
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._mouse_captured is False  # released → terminal owns the mouse


@pytest.mark.asyncio
async def test_mouse_true_keeps_capture() -> None:
    app = KodaApp(model="test:model", mouse=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._mouse_captured is True


@pytest.mark.asyncio
async def test_ctrl_o_toggles_capture_and_announces() -> None:
    app = KodaApp(model="test:model")  # starts native (released)
    async with app.run_test() as pilot:
        await pilot.pause()
        calls: list[str] = []
        app._driver._disable_mouse_support = lambda: calls.append("disable")  # type: ignore[union-attr]
        app._driver._enable_mouse_support = lambda: calls.append("enable")  # type: ignore[union-attr]

        # From native → mouse mode (enable).
        await app.action_toggle_mouse_capture()
        await pilot.pause()
        assert calls == ["enable"]
        assert app._mouse_captured is True

        # Back to native (disable).
        await app.action_toggle_mouse_capture()
        await pilot.pause()
        assert calls == ["enable", "disable"]
        assert app._mouse_captured is False

        assert app._messages_container.query(AppMessage)


@pytest.mark.asyncio
async def test_toggle_is_a_noop_without_driver_mouse_methods() -> None:
    """Drivers lacking the mouse hooks must no-op, not raise or flip state."""
    app = KodaApp(model="test:model", mouse=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._driver._enable_mouse_support = None  # type: ignore[union-attr,assignment]
        app._driver._disable_mouse_support = None  # type: ignore[union-attr,assignment]
        before = app._mouse_captured
        await app.action_toggle_mouse_capture()
        await pilot.pause()
        assert app._mouse_captured is before  # unchanged
