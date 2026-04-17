"""
Test that /tree command works without NoActiveWorker error.

The fix: action_open_tree must be decorated with @work so that
push_screen_wait (which requires wait_for_dismiss=True) runs
inside a Textual worker.
"""

from __future__ import annotations

import pytest
from textual.worker import Worker

from koda.tui.app import KodaApp


def test_action_open_tree_is_worker_decorated():
    """action_open_tree must be wrapped by @work so push_screen_wait
    runs inside a Textual worker and doesn't raise NoActiveWorker."""
    # The @work decorator wraps the original function and sets metadata
    # that Textual uses to identify it as a worker creator.
    method = KodaApp.action_open_tree
    assert hasattr(method, "__wrapped__"), (
        "action_open_tree should be @work-decorated (__wrapped__ missing)"
    )


@pytest.mark.asyncio
async def test_tree_command_no_active_worker_error():
    """Simulates calling /tree the way the app does — must not raise NoActiveWorker."""
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]

        # Seed a message so the "no messages yet" guard doesn't short-circuit
        app._koda_session.add_message("user", "hello")

        # This is what _handle_command("/tree") does.
        # Before the fix, this would raise:
        #   NoActiveWorker: push_screen must be run from a worker
        #     when `wait_for_dismiss` is True
        try:
            app.action_open_tree()
        except Exception as exc:
            if "NoActiveWorker" in type(exc).__name__:
                pytest.fail(f"/tree raised NoActiveWorker: {exc}")
            raise

        await pilot.pause()

        # The TreeScreen should now be pushed onto the screen stack
        from koda.tree_widget import TreeScreen
        assert any(
            isinstance(s, TreeScreen) for s in app.screen_stack
        ), "TreeScreen should be on the screen stack after /tree"
