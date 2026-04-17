"""
Tests for the suggestion popup + completer.

Covers:
  - typing `/` shows the commands popup
  - arrow keys navigate the popup
  - Enter accepts the highlighted suggestion into the input
  - typing `@` shows the file popup
  - escape dismisses the popup
"""

from __future__ import annotations

import pytest

from koda.tui.app import KodaApp
from koda.tui.widgets.suggestions import SuggestionPopup


@pytest.mark.asyncio
async def test_slash_shows_popup_and_accept_inserts():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        ci = app._chat_input
        popup = app._popup
        assert ci is not None and popup is not None

        await pilot.press("/")
        await pilot.pause()
        assert popup.is_visible, "Popup should be visible after typing '/'"
        assert popup.highlighted == 0

        # Arrow-down advances
        await pilot.press("down")
        await pilot.pause()
        assert popup.highlighted == 1

        # Enter accepts and replaces the input
        await pilot.press("enter")
        await pilot.pause()
        assert ci.value.startswith("/"), f"Expected slash-command in input, got {ci.value!r}"
        # After accept, popup should be hidden
        assert not popup.is_visible


@pytest.mark.asyncio
async def test_escape_dismisses_popup():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        popup = app._popup

        await pilot.press("/")
        await pilot.pause()
        assert popup.is_visible

        await pilot.press("escape")
        await pilot.pause()
        assert not popup.is_visible


@pytest.mark.asyncio
async def test_at_sign_shows_files():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        popup = app._popup

        await pilot.press("@")
        await pilot.pause()
        assert popup.is_visible, "Popup should show files after '@'"
        assert len(popup._suggestions) > 0, "Expected at least one file suggestion"


def test_completer_slash_commands():
    """Unit: /t matches tree + theme."""
    from koda.tui.completers import complete

    sugg, rng, title = complete("/t", 2)
    labels = [s.label for s in sugg]
    assert "/tree" in labels
    assert "/theme" in labels
    assert rng == (0, 2)
    assert title == "Commands"


def test_completer_at_token_in_middle():
    """Unit: @fragment embedded mid-string is detected at cursor."""
    from koda.tui.completers import complete

    value = "hello @app"
    result = complete(value, len(value))
    assert result is not None
    _, rng, title = result
    assert rng == (6, 10), f"Expected (6,10), got {rng}"
    assert title == "Files"
