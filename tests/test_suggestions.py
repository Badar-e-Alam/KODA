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
async def test_slash_popup_navigates_and_tab_inserts():
    """Tab accepts the highlighted suggestion into the input without
    submitting — arrow keys navigate the popup.
    """
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

        # Tab accepts WITHOUT submitting: input is populated, popup hides
        await pilot.press("tab")
        await pilot.pause()
        assert ci.value.startswith("/"), (
            f"Expected slash-command in input, got {ci.value!r}"
        )
        assert not popup.is_visible


@pytest.mark.asyncio
async def test_enter_on_arg_command_accepts_without_submitting():
    """Enter on a command that takes an argument (e.g. /theme) inserts the
    command with a trailing space and waits for the user's arg — does NOT
    submit immediately.
    """
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        ci = app._chat_input
        popup = app._popup
        assert ci is not None and popup is not None

        for c in "/the":  # prefix that matches /theme uniquely
            await pilot.press(c)
        await pilot.pause()
        assert popup.is_visible
        # /theme is expected to be the sole match and first highlighted
        labels = [s.label for s in popup._suggestions]
        assert "/theme" in labels
        popup.highlighted = labels.index("/theme")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        # Value should have the command + trailing space waiting for the arg
        assert ci.value == "/theme ", ci.value


@pytest.mark.asyncio
async def test_enter_after_at_suggestion_submits_once(tmp_path, monkeypatch):
    """Regression: typing '!echo @.env.example' then pressing Enter once
    used to re-open the popup (watch_value fired with stale cursor) and
    append the suggestion again instead of submitting. Must submit on the
    very first Enter.
    """
    # Create a deterministic file tree so the @-completer has something to
    # suggest regardless of the repo state.
    (tmp_path / ".env.example").write_text("x")
    monkeypatch.chdir(tmp_path)
    from koda.tui import completers
    completers.invalidate_files_cache()

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        ci = app._chat_input
        assert ci is not None
        await pilot.pause()

        for c in "!echo @.env.example":
            await pilot.press(c)
        await pilot.pause()

        await pilot.press("enter")
        # Shell runs off the event loop
        import asyncio
        await asyncio.sleep(1.2)
        await pilot.pause()

        # Input must be cleared (submit happened in one Enter)
        assert ci.value == "", f"Expected clear input after submit, got {ci.value!r}"
        # A UserMessage with the original text must exist in the chat
        from koda.tui.widgets import UserMessage
        user_msgs = [c._content for c in app._messages_container.children
                     if isinstance(c, UserMessage)]
        assert any("!echo @.env.example" in m for m in user_msgs), user_msgs


@pytest.mark.asyncio
async def test_enter_on_no_arg_command_submits():
    """Enter on a command without args (e.g. /clear) should accept AND submit
    in a single press.
    """
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        ci = app._chat_input
        popup = app._popup
        assert ci is not None and popup is not None

        for c in "/cle":
            await pilot.press(c)
        await pilot.pause()
        labels = [s.label for s in popup._suggestions]
        assert "/clear" in labels
        popup.highlighted = labels.index("/clear")
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()
        # After submit the input is cleared
        assert ci.value == "", ci.value


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
