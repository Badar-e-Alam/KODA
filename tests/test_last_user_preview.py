"""
Tests for the last-user-message preview line above the ChatInput.

Contract:
  - Starts empty (no prior message).
  - After a user message submits, the widget shows a truncated snippet
    (<= 50 chars, with ellipsis when truncated).
  - Sits directly above the ChatInput.
  - Text-align is right (so it hugs the right edge of the input).
"""

from __future__ import annotations

import pytest
from textual.widgets import Static

from koda.tui.app import KodaApp


@pytest.mark.asyncio
async def test_preview_empty_before_any_message():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        w = app.query_one("#last-user-preview", Static)
        assert str(w.render()).strip() == ""


@pytest.mark.asyncio
async def test_preview_shows_after_user_message():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Enter a short message
        for c in "hi there":
            await pilot.press(c)
        await pilot.press("enter")
        await pilot.pause()

        w = app.query_one("#last-user-preview", Static)
        text = str(w.render())
        assert "hi there" in text


@pytest.mark.asyncio
async def test_preview_truncates_at_50_chars():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        long_msg = "A" * 80
        for c in long_msg:
            await pilot.press(c)
        await pilot.press("enter")
        await pilot.pause()

        w = app.query_one("#last-user-preview", Static)
        text = str(w.render())
        # Strip the decorative prefix ("↳ ") to measure just the message slice
        after_arrow = text.split(" ", 1)[-1] if " " in text else text
        # Message portion must be at most 50 chars (49 As + ellipsis)
        # Remove the leading arrow + space for counting
        assert len(after_arrow) <= 52, f"preview too long: {len(after_arrow)} chars"
        assert "…" in text, "truncated preview should end with an ellipsis"


@pytest.mark.asyncio
async def test_preview_is_directly_above_chat_input():
    async with KodaApp().run_test(size=(100, 30)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        w = app.query_one("#last-user-preview", Static)
        ci = app._chat_input
        assert ci is not None
        assert w.region.y + w.region.height == ci.region.y, (
            f"preview should sit flush above the input: preview={w.region} ci={ci.region}"
        )


@pytest.mark.asyncio
async def test_preview_updates_on_each_new_message():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        w = app.query_one("#last-user-preview", Static)

        for c in "first":
            await pilot.press(c)
        await pilot.press("enter")
        await pilot.pause()
        assert "first" in str(w.render())

        for c in "second":
            await pilot.press(c)
        await pilot.press("enter")
        await pilot.pause()
        text = str(w.render())
        assert "second" in text
        assert "first" not in text  # only the most recent message


@pytest.mark.asyncio
async def test_preview_collapses_newlines():
    """Multi-line messages must render as a single line preview."""
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Inject a message directly (easier than typing newline in pilot)
        await app._handle_user_message("line one\nline two\nline three")
        await pilot.pause()

        w = app.query_one("#last-user-preview", Static)
        text = str(w.render())
        # No bare newline in the rendered preview
        assert "\n" not in text
