"""
Tests for the inline suggestion popup layout.

The popup should sit *right above* the ChatInput (no gap), span the full
width of the chat area, and collapse out of the layout when hidden.
"""

from __future__ import annotations

import pytest

from koda.tui.app import KodaApp


@pytest.mark.asyncio
async def test_popup_pinned_above_input_stack():
    """When visible, the popup must sit directly above the input stack
    (preview row + ChatInput) — no gap, no overlap.
    """
    from textual.widgets import Static

    async with KodaApp().run_test(size=(100, 30)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        ci = app._chat_input
        popup = app._popup
        preview = app.query_one("#last-user-preview", Static)
        assert ci is not None and popup is not None

        await pilot.press("/")
        await pilot.pause()
        assert popup.is_visible

        # Popup bottom == preview top; preview bottom == input top.
        assert popup.region.y + popup.region.height == preview.region.y, (
            f"Popup should be flush above preview. popup={popup.region} preview={preview.region}"
        )
        assert preview.region.y + preview.region.height == ci.region.y, (
            f"Preview should be flush above input. preview={preview.region} input={ci.region}"
        )


@pytest.mark.asyncio
async def test_popup_spans_chat_area_width():
    """The popup should be full-width (no arbitrary 70-col cap)."""
    async with KodaApp().run_test(size=(100, 30)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        popup = app._popup
        chat_area = app.query_one("#chat-area")
        assert popup is not None

        await pilot.press("/")
        await pilot.pause()

        # Width should match the chat-area, not the old fixed 70
        assert popup.region.width == chat_area.region.width, (
            f"Popup width {popup.region.width} != chat-area {chat_area.region.width}"
        )


@pytest.mark.asyncio
async def test_popup_has_footer_hint():
    """The popup must surface keybindings in a footer — navigate / accept / dismiss."""
    from textual.widgets import Static

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()

        popup = app._popup
        assert popup is not None
        footer = popup.query_one("#suggest-footer", Static)
        text = str(footer.render()).lower()
        low = text
        assert "navigate" in low
        assert "accept" in low
        assert "dismiss" in low


@pytest.mark.asyncio
async def test_tree_option_is_fully_visible_on_small_terminal():
    """On a 24-row terminal the whole command list (including /tree, /usage)
    must be inside the visible screen — previously the banner pushed them off.
    """
    async with KodaApp().run_test(size=(80, 24)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()

        popup = app._popup
        assert popup is not None
        screen_h = app.size.height
        popup_bottom = popup.region.y + popup.region.height
        assert popup_bottom <= screen_h, (
            f"popup extends past screen ({popup_bottom} > {screen_h}) — "
            f"lower commands like /tree would be clipped"
        )

        # /tree must exist in the suggestion set and be selectable
        labels = [s.label for s in popup._suggestions]
        assert "/tree" in labels, labels


@pytest.mark.asyncio
async def test_banner_collapses_with_popup_and_restores_on_dismiss():
    """Banner should shrink while popup is open, restore after escape."""
    async with KodaApp().run_test(size=(80, 24)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        banner = app._banner
        assert banner is not None

        original_h = banner.region.height
        assert original_h > 1, "banner should be multi-line initially"

        await pilot.press("/")
        await pilot.pause()
        assert "-compact" in banner.classes, "banner should collapse while popup is open"

        await pilot.press("escape")
        await pilot.pause()
        assert "-compact" not in banner.classes, (
            "banner should restore to full size after escape"
        )
        assert banner.region.height == original_h


@pytest.mark.asyncio
async def test_banner_restores_after_accept():
    """Banner must also restore when the user accepts a suggestion."""
    async with KodaApp().run_test(size=(80, 24)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        banner = app._banner
        assert banner is not None

        await pilot.press("/")
        await pilot.pause()
        assert "-compact" in banner.classes

        await pilot.press("tab")  # accept current suggestion
        await pilot.pause()
        assert "-compact" not in banner.classes


@pytest.mark.asyncio
async def test_popup_collapses_when_hidden():
    """When no suggestions are active, the popup must not steal screen rows."""
    async with KodaApp().run_test(size=(100, 30)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        popup = app._popup
        assert popup is not None
        # No trigger → popup hidden → zero height contribution
        assert not popup.is_visible
        assert popup.region.height == 0, (
            f"Hidden popup should have 0 height, got {popup.region.height}"
        )
