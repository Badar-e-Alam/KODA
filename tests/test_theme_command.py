"""
Tests for the /theme command.

Contract:
  - All KODA palettes are registered as Textual themes on mount.
  - apply_theme(name) swaps the live theme when `name` is known.
  - Unknown names are ignored (no crash).
  - The /theme slash command calls apply_theme and mounts a status line.
"""

from __future__ import annotations

import pytest

from koda.tui.app import KodaApp
from koda.tui.theme import DEFAULT_THEME, THEMES


@pytest.mark.asyncio
async def test_every_palette_registered_as_theme():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        for name in THEMES:
            assert name in app.available_themes, (
                f"theme '{name}' not registered — available: "
                f"{list(app.available_themes)}"
            )


@pytest.mark.asyncio
async def test_default_theme_is_applied():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        assert app.theme == DEFAULT_THEME


@pytest.mark.asyncio
async def test_apply_theme_swaps_colors():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        default_primary = app.current_theme.primary
        default_bg = app.current_theme.background

        app.apply_theme("dracula")
        await pilot.pause()
        assert app.theme == "dracula"
        assert app.current_theme.primary.lower() != default_primary.lower() \
            or app.current_theme.background.lower() != default_bg.lower()


@pytest.mark.asyncio
async def test_apply_theme_unknown_name_is_noop():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        before = app.theme
        app.apply_theme("does-not-exist")
        await pilot.pause()
        assert app.theme == before  # unchanged


@pytest.mark.asyncio
async def test_slash_theme_command_switches_and_confirms():
    from koda.tui.widgets import AppMessage
    from koda.tui.commands import dispatch

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        handled = await dispatch(app, "/theme solarized-dark")
        await pilot.pause()
        assert handled
        assert app.theme == "solarized-dark"
        # An AppMessage confirming the change should be mounted
        app_msgs = [m for m in app.query(AppMessage) if "solarized-dark" in m._content]
        assert app_msgs, "expected an AppMessage confirming the theme switch"


@pytest.mark.asyncio
async def test_theme_autocomplete_accept_produces_clean_command():
    """Regression: typing '/t', accepting '/theme', typing a fragment,
    accepting a theme suggestion, then submitting must swap the theme.
    Previously the inserted text duplicated into '/theme /theme <name>'
    which dispatch couldn't parse.
    """
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        ci = app._chat_input
        assert ci is not None
        await pilot.pause()

        # Simulate: user types '/theme ' (fully), then 'sol', then tab to
        # accept 'solarized-dark' from the popup.
        ci.value = "/theme sol"
        ci.cursor_position = len(ci.value)
        await pilot.pause()
        popup = app._popup
        assert popup is not None and popup.is_visible

        # Force-highlight solarized-dark
        labels = [s.label for s in popup._suggestions]
        idx = labels.index("solarized-dark")
        popup.highlighted = idx
        await pilot.pause()

        await pilot.press("tab")  # accept
        await pilot.pause()

        # Accepting the suggestion must not duplicate the prefix
        assert not ci.value.startswith("/theme /theme"), ci.value
        assert ci.value == "/theme solarized-dark", ci.value

        # Submit and verify the theme actually changed
        await pilot.press("enter")
        await pilot.pause()
        assert app.theme == "solarized-dark"


@pytest.mark.asyncio
async def test_slash_theme_with_unknown_errors_without_crash():
    from koda.tui.widgets import ErrorMessage
    from koda.tui.commands import dispatch

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        handled = await dispatch(app, "/theme not-a-theme")
        await pilot.pause()
        assert handled
        errs = list(app.query(ErrorMessage))
        assert errs, "expected an ErrorMessage for unknown theme"
