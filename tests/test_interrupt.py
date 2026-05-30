"""Esc-interrupts-running-turn regression tests.

Esc is overloaded in the KODA TUI:

  • ``PermissionPrompt`` and ``AskUserPrompt`` consume it via
    ``priority=True`` bindings (deny / cancel).
  • The chat-input's ``SuggestionPopup`` consumes it when visible
    (closes the popup).
  • Otherwise it bubbles up to the app-level
    ``Binding("escape", "interrupt_turn", ...)`` which cancels the
    in-flight turn task and mounts a "⏹ Interrupting…" notice in the
    message stream.

These tests pin the third path — the bit a user actually relies on
when they want the agent to stop. The first path is already covered
by ``test_permission_modal.py::test_permission_escape_denies``.
"""

from __future__ import annotations

import asyncio

import pytest

from koda.tui.app import KodaApp
from koda.tui.widgets.messages import AppMessage


@pytest.mark.asyncio
async def test_escape_interrupts_running_turn() -> None:
    """With a turn in flight and no prompt/popup up, Esc must:

    1. Cancel ``_turn_task`` so the agent stops.
    2. Mount a "⏹ Interrupting…" ``AppMessage`` so the user has visual
       confirmation the keystroke landed.

    We park a long ``asyncio.sleep`` on ``_turn_task`` instead of
    spinning up a real adapter — ``action_interrupt_turn`` guards on
    ``_adapter is not None`` so leaving it ``None`` exercises the
    cancel path cleanly without needing the rest of the agent stack.
    """
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        long_task = asyncio.create_task(asyncio.sleep(60))
        app._turn_task = long_task
        try:
            await pilot.press("escape")
            # Give ``action_interrupt_turn``'s ``await``s (mount + the
            # cancellation itself) a few loop turns to settle.
            for _ in range(20):
                await pilot.pause()
                if long_task.cancelled() or long_task.done():
                    break
                await asyncio.sleep(0.02)

            assert (
                long_task.cancelled() or long_task.done()
            ), "_turn_task should be cancelled after Esc"

            notices = [
                m
                for m in app.query(AppMessage)
                if "Interrupting" in str(getattr(m, "_content", ""))
            ]
            assert notices, "'⏹ Interrupting…' notice was not mounted"
        finally:
            # Awaiting a cancelled task lets the loop reap it so pytest
            # doesn't see a "Task was destroyed but pending" warning.
            if not long_task.done():
                long_task.cancel()
            try:
                await long_task
            except (asyncio.CancelledError, BaseException):
                pass


@pytest.mark.asyncio
async def test_escape_is_noop_when_no_turn_running() -> None:
    """Esc with no turn in flight must NOT spam the stream.

    Important UX guard: users hit Esc reflexively after dismissing a
    prompt or popup. The handler short-circuits when ``_turn_task`` is
    ``None`` / ``done()``, so no "Interrupting…" notice should appear
    and no exception should fire.
    """
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        assert app._turn_task is None  # sanity

        before = len(list(app.query(AppMessage)))
        await pilot.press("escape")
        for _ in range(5):
            await pilot.pause()
        after = len(list(app.query(AppMessage)))

        assert before == after, "Esc with no turn must not mount any message"


@pytest.mark.asyncio
async def test_escape_does_not_interrupt_when_turn_already_done() -> None:
    """A completed task should be treated the same as no task at all.

    Without this guard, hitting Esc shortly after a turn finishes would
    fire a bogus 'Interrupting…' notice even though there's nothing
    left to interrupt.
    """
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        # A task that has already finished — the guard at
        # action_interrupt_turn must catch this via `.done()`.
        done_task: asyncio.Task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        assert done_task.done()
        app._turn_task = done_task

        before = len(list(app.query(AppMessage)))
        await pilot.press("escape")
        for _ in range(5):
            await pilot.pause()
        after = len(list(app.query(AppMessage)))

        assert before == after, "Esc on a completed turn must be a no-op"
