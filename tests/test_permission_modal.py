"""Regression tests for the permission-prompt bridge.

The agent's permission gate runs on a worker thread (the backend wraps
``_perms.check`` in ``asyncio.to_thread``) and calls
``KodaApp._prompt_from_tool_thread`` to ask the user. The bridge mounts
an inline ``PermissionPrompt`` widget in the messages container and
blocks the worker on a ``concurrent.futures.Future`` until the user
answers.

Historical bugs these tests pin:

1. The original implementation scheduled ``push_screen_wait`` via
   ``run_coroutine_threadsafe`` — a bare loop task outside the app
   context — so the screen never rendered and the bridge wedged the
   first time the agent ran a mutating tool.
2. The full-screen ``PermissionScreen`` modal that replaced it had
   bindings on a screen whose ``ChatInput`` retained focus, so ``y``,
   ``a``, ``n`` got typed into the composer instead of routing to the
   modal's actions.

The current implementation (inline ``PermissionPrompt`` mounted in
``#messages`` with ``priority=True`` bindings) sidesteps both.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from koda.tools import permissions as perms
from koda.tui.app import KodaApp
from koda.tui.modes import Mode
from koda.tui.widgets import PermissionPrompt


async def _answer_prompt(
    app: KodaApp, pilot, tool_name: str, key: str
) -> tuple[object, bool]:
    """Call the permission bridge from a worker thread, render + answer the
    inline prompt via ``key``, and return ``(bridge_return_value,
    prompt_still_mounted)``."""
    result: dict = {}

    def _worker() -> None:
        result["outcome"] = app._prompt_from_tool_thread(
            tool_name, {"file_path": "AGENTS.md"}
        )

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    # The prompt must actually mount in the messages container.
    appeared: PermissionPrompt | None = None
    for _ in range(60):
        await pilot.pause()
        prompts = list(app.query(PermissionPrompt))
        if prompts:
            appeared = prompts[0]
            break
        await asyncio.sleep(0.02)
    assert appeared is not None, "PermissionPrompt never mounted — bridge is broken"

    await pilot.press(key)

    for _ in range(60):
        await pilot.pause()
        if not t.is_alive():
            break
        await asyncio.sleep(0.02)
    t.join(timeout=5)
    assert not t.is_alive(), "worker thread never unblocked — answer not delivered"

    still_mounted = bool(list(app.query(PermissionPrompt)))
    return result.get("outcome"), still_mounted


@pytest.mark.asyncio
async def test_permission_allow_routes_back_and_cleans_up() -> None:
    perms.clear_session_allow()
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        outcome, still_mounted = await _answer_prompt(app, pilot, "write_file", "y")
        assert outcome is True
        assert not still_mounted, "prompt widget should be removed after answer"
        assert "write_file" not in perms._session_allow  # "allow once" ≠ remember


@pytest.mark.asyncio
async def test_permission_deny_returns_false() -> None:
    perms.clear_session_allow()
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        outcome, still_mounted = await _answer_prompt(app, pilot, "execute", "n")
        assert outcome is False
        assert not still_mounted


@pytest.mark.asyncio
async def test_permission_escape_denies() -> None:
    """Esc should map to deny (matches the on-screen hint)."""
    perms.clear_session_allow()
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        outcome, still_mounted = await _answer_prompt(app, pilot, "execute", "escape")
        assert outcome is False
        assert not still_mounted


@pytest.mark.asyncio
async def test_permission_always_allows_and_remembers() -> None:
    perms.clear_session_allow()
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        outcome, still_mounted = await _answer_prompt(app, pilot, "edit_file", "a")
        assert outcome is True
        assert not still_mounted
        assert "edit_file" in perms._session_allow  # "always" adds to allow-list
    perms.clear_session_allow()


@pytest.mark.asyncio
async def test_permission_arrow_nav_and_enter() -> None:
    """↓ + Enter from the first option (allow) should land on always."""
    perms.clear_session_allow()
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        result: dict = {}

        def _worker() -> None:
            result["outcome"] = app._prompt_from_tool_thread(
                "edit_file", {"file_path": "AGENTS.md"}
            )

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        # Wait for prompt to mount.
        for _ in range(60):
            await pilot.pause()
            if list(app.query(PermissionPrompt)):
                break
            await asyncio.sleep(0.02)

        # Default highlight is index 0 (allow). Down once → index 1 (always).
        await pilot.press("down")
        await pilot.press("enter")

        for _ in range(60):
            await pilot.pause()
            if not t.is_alive():
                break
            await asyncio.sleep(0.02)
        t.join(timeout=5)

        assert result["outcome"] is True
        assert "edit_file" in perms._session_allow
    perms.clear_session_allow()


@pytest.mark.asyncio
async def test_permission_headless_defaults_to_allow() -> None:
    """With no UI loop captured (no on_mount), the bridge must not block —
    it allows so non-TUI consumers don't hang on a prompt that can't render."""
    app = KodaApp(model="test:model")
    # Do not run the app → _ui_loop is never set.
    assert getattr(app, "_ui_loop", None) is None
    assert app._prompt_from_tool_thread("write_file", {"file_path": "x"}) is True


def test_permission_plan_mode_refuses_outright() -> None:
    """PLAN mode = advisory-only. The gate refuses mutations directly
    without firing the prompt — no override, no modal — so the agent
    stalls on the refusal string instead of waiting for the user."""
    perms.clear_session_allow()
    perms.set_mode(Mode.PLAN)
    hook_called: list[bool] = []
    perms.set_prompt_hook(lambda name, args: (hook_called.append(True), True)[1])
    try:
        refusal = perms.check("write_file", {"file_path": "x"})
        assert refusal is not None
        assert "plan mode" in refusal.lower()
        assert "Shift+A" in refusal or "apply" in refusal
        assert hook_called == [], "PLAN must not call the prompt hook"
    finally:
        perms.set_mode(Mode.DEFAULT)
        perms.set_prompt_hook(None)


def test_permission_plan_mode_execute_refused() -> None:
    """Same contract for ``execute``: PLAN refuses without prompting."""
    perms.clear_session_allow()
    perms.set_mode(Mode.PLAN)
    perms.set_prompt_hook(lambda name, args: True)
    try:
        refusal = perms.check("execute", {"command": "git checkout main"})
        assert refusal is not None
        assert "plan mode" in refusal.lower()
    finally:
        perms.set_mode(Mode.DEFAULT)
        perms.set_prompt_hook(None)
