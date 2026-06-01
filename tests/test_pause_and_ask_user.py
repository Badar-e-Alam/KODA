"""Tests for the soft-pause mechanism + ``ask_user`` tool bridge.

Soft pause (``koda.tools.permissions.wait_until_unpaused``): mutating
backend ops should block while a permission prompt is on screen, and
unblock the instant the user picks. Reads must never be affected.

Ask-user (``koda.tools.ask_user`` + ``AskUserPrompt``): the same
worker-thread → UI-loop bridge as the permission prompt, blocking the
worker until the user picks an option, returning the verbatim text.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from koda.tools import ask_user as ask
from koda.tools import permissions as perms
from koda.tui.app import KodaApp
from koda.tui.widgets import AskUserPrompt


# ─── Soft pause ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_event_default_is_unblocked() -> None:
    """Before any prompt fires, ``wait_until_unpaused`` returns immediately."""
    # ``_pause_event`` may have been mutated by another test; reset to a
    # known good "unpaused" state.
    perms.mark_prompt_resolved()
    # Should return almost immediately (we give it a small budget so a
    # genuine hang would surface as a test failure, not a deadlock).
    await asyncio.wait_for(perms.wait_until_unpaused(), timeout=0.5)


@pytest.mark.asyncio
async def test_pause_blocks_until_resolved() -> None:
    """When a prompt is pending, ``wait_until_unpaused`` blocks; once
    ``mark_prompt_resolved`` fires, the waiter resumes."""
    perms.mark_prompt_pending()

    waiter = asyncio.create_task(perms.wait_until_unpaused())
    # Yield a few loop iterations to confirm the waiter is genuinely stuck.
    for _ in range(5):
        await asyncio.sleep(0)
    assert not waiter.done(), "waiter resumed before mark_prompt_resolved"

    perms.mark_prompt_resolved()
    await asyncio.wait_for(waiter, timeout=0.5)


# ─── ask_user bridge ─────────────────────────────────────────────────


async def _drive_ask(
    app: KodaApp, pilot, question: str, options: list[str], keys: list[str]
) -> tuple[str, bool]:
    """Spawn the bridge call from a worker thread, wait for the prompt to
    mount, press ``keys`` in order, return ``(answer, prompt_cleared)``."""
    result: dict = {}

    def _worker() -> None:
        result["answer"] = app._ask_user_from_tool_thread(question, options)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    appeared = False
    for _ in range(60):
        await pilot.pause()
        if list(app.query(AskUserPrompt)):
            appeared = True
            break
        await asyncio.sleep(0.02)
    assert appeared, "AskUserPrompt never mounted — bridge is broken"

    for key in keys:
        await pilot.press(key)

    for _ in range(60):
        await pilot.pause()
        if not t.is_alive():
            break
        await asyncio.sleep(0.02)
    t.join(timeout=5)
    assert not t.is_alive(), "worker thread never unblocked — answer not delivered"

    still_mounted = bool(list(app.query(AskUserPrompt)))
    return result.get("answer", ""), not still_mounted


@pytest.mark.asyncio
async def test_ask_user_enter_picks_highlighted_option() -> None:
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        answer, cleared = await _drive_ask(
            app, pilot, "Which database?", ["SQLite", "Postgres"], keys=["enter"]
        )
        assert answer == "SQLite"  # default highlight = first option
        assert cleared


@pytest.mark.asyncio
async def test_ask_user_arrow_then_enter() -> None:
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        answer, cleared = await _drive_ask(
            app, pilot, "Pick one", ["A", "B", "C"], keys=["down", "down", "enter"]
        )
        assert answer == "C"
        assert cleared


@pytest.mark.asyncio
async def test_ask_user_free_text_overrides_options() -> None:
    """Typing a custom reply + Enter sends that text to the agent instead of
    a preset option (the 'say something else' field)."""
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        answer, cleared = await _drive_ask(
            app, pilot, "Pick one", ["Yes", "No", "Maybe"],
            keys=["h", "e", "l", "l", "o", "enter"],
        )
        assert answer == "hello"
        assert cleared


@pytest.mark.asyncio
async def test_ask_user_free_text_backspace_edits() -> None:
    """Backspace edits the free-text buffer before sending."""
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        answer, cleared = await _drive_ask(
            app, pilot, "Pick one", ["Yes", "No"],
            keys=["h", "i", "x", "backspace", "enter"],
        )
        assert answer == "hi"
        assert cleared


@pytest.mark.asyncio
async def test_ask_user_empty_text_falls_back_to_option() -> None:
    """With nothing typed, Enter still sends the highlighted option."""
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        answer, cleared = await _drive_ask(
            app, pilot, "Pick one", ["Yes", "No", "Maybe"], keys=["down", "enter"]
        )
        assert answer == "No"  # down once → option 2
        assert cleared


@pytest.mark.asyncio
async def test_ask_user_escape_returns_empty() -> None:
    """``Esc`` cancels — the tool sees an empty answer."""
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        answer, cleared = await _drive_ask(
            app, pilot, "Anything?", ["one", "two"], keys=["escape"]
        )
        assert answer == ""
        assert cleared


def test_ask_user_headless_returns_sentinel() -> None:
    """No hook installed → the tool returns a sentinel string instead of
    deadlocking on a prompt that can't render."""
    ask.set_hook(None)
    answer = ask.ask("What now?", ["a", "b"])
    assert "unavailable" in answer.lower() or "headless" in answer.lower()
