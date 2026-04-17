"""
Test that !command shell execution works in KODA.

The ! prefix should switch to shell mode and execute the command,
displaying output in the chat.
"""

from __future__ import annotations

import asyncio

import pytest

from koda.tui.app import KodaApp


@pytest.mark.asyncio
async def test_shell_command_mode_detection():
    """Typing ! should switch ChatInput to shell mode."""
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        ci = app._chat_input
        assert ci is not None

        await pilot.press("!")
        await pilot.pause()
        assert ci.mode == "shell", f"Expected shell mode after !, got {ci.mode!r}"
        assert ci.value == "!"


@pytest.mark.asyncio
async def test_shell_command_executes():
    """!echo hello should execute and show output."""
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]

        await pilot.press("!")
        await pilot.pause()
        for c in "echo hello":
            await pilot.press(c)
        await pilot.pause()

        await pilot.press("enter")
        # Give the shell subprocess time to run
        await asyncio.sleep(2)
        await pilot.pause()

        from koda.tui.widgets.messages import AssistantMessage, UserMessage

        messages_container = app.query_one("#messages")
        children = list(messages_container.children)

        user_msgs = [c for c in children if isinstance(c, UserMessage)]
        asst_msgs = [c for c in children if isinstance(c, AssistantMessage)]

        assert len(user_msgs) >= 1, "Expected a UserMessage for !echo hello"
        assert user_msgs[0]._content == "!echo hello"

        assert len(asst_msgs) >= 1, "Expected an AssistantMessage with command output"
        assert "hello" in asst_msgs[0]._content
