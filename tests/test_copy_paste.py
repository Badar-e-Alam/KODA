"""
Tests for mouse-select copy (Ctrl+C) and OS clipboard paste (Ctrl+V).

Contract:
  - Ctrl+C with a live mouse selection must copy to the OS clipboard and
    NOT interrupt / exit (so users never lose work to a stray selection).
  - Ctrl+C without a selection keeps its legacy meaning: interrupt running
    turn, else exit.
  - Ctrl+V pastes from the OS clipboard (via pyperclip) into the ChatInput
    at the cursor, collapsing newlines (input is single-line).
"""

from __future__ import annotations

import sys
import types

import pytest

from koda.tui.app import KodaApp
from koda.tui.widgets import AppMessage


def _install_fake_pyperclip(monkeypatch, *, paste_text: str = "", sink: list | None = None):
    """Drop a stub pyperclip into sys.modules so tests never touch the host clipboard."""
    fake = types.ModuleType("pyperclip")
    fake.paste = lambda: paste_text
    if sink is None:
        sink = []
    fake.copy = lambda t: sink.append(t)
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    return sink


@pytest.mark.asyncio
async def test_ctrl_c_copies_selection_and_does_not_exit(monkeypatch):
    sink = _install_fake_pyperclip(monkeypatch)

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Force a selection via the screen API
        monkeypatch.setattr(app.screen, "get_selected_text", lambda: "hello selected")

        await app.action_copy_or_interrupt()
        await pilot.pause()

        assert sink == ["hello selected"], f"clipboard sink: {sink}"
        assert app._running, "app must still be running (Ctrl+C with selection must not exit)"


@pytest.mark.asyncio
async def test_ctrl_c_without_selection_still_exits(monkeypatch):
    _install_fake_pyperclip(monkeypatch)

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Empty selection
        monkeypatch.setattr(app.screen, "get_selected_text", lambda: "")

        # No turn running → Ctrl+C exits. We just check it's routed through
        # the exit path without raising, by patching app.exit.
        called = []
        monkeypatch.setattr(app, "exit", lambda *a, **kw: called.append(True))

        await app.action_copy_or_interrupt()
        await pilot.pause()
        assert called == [True]


@pytest.mark.asyncio
async def test_ctrl_c_selection_shows_status_message(monkeypatch):
    _install_fake_pyperclip(monkeypatch)

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        monkeypatch.setattr(app.screen, "get_selected_text", lambda: "abc")

        before = sum(1 for c in app.query(AppMessage))
        await app.action_copy_or_interrupt()
        await pilot.pause()
        after = sum(1 for c in app.query(AppMessage))

        assert after == before + 1, "a confirmation AppMessage should be mounted"


@pytest.mark.asyncio
async def test_ctrl_v_paste_uses_os_clipboard(monkeypatch):
    _install_fake_pyperclip(monkeypatch, paste_text="hello from OS")

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        ci = app._chat_input
        assert ci is not None
        ci.value = ""
        ci.action_paste()
        await pilot.pause()
        assert ci.value == "hello from OS"


@pytest.mark.asyncio
async def test_ctrl_v_collapses_newlines(monkeypatch):
    _install_fake_pyperclip(monkeypatch, paste_text="line1\nline2\r\nline3")

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        ci = app._chat_input
        assert ci is not None
        ci.value = ""
        ci.action_paste()
        await pilot.pause()
        # No bare newlines in single-line input
        assert "\n" not in ci.value and "\r" not in ci.value
        assert ci.value == "line1 line2 line3"


@pytest.mark.asyncio
async def test_ctrl_v_bound_to_paste():
    """Ensure the Input base class binding for ctrl+v is still active."""
    from textual.widgets import Input

    keys = [b.key for b in Input.BINDINGS]
    assert "ctrl+v" in keys
