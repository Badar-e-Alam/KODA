"""Regression: a running turn must not block Textual's message pump.

The bug: ``KodaApp.on_chat_input_submitted`` used to ``await`` the entire
turn. A Textual message handler that awaits for the turn's whole duration
blocks the app's message pump, so no key events are dispatched while the
agent works. At a permission prompt that *deadlocks* — the turn pauses
waiting for the user's y/a/n, but the keypress can't be processed because
the pump is stuck awaiting the submit handler. Symptom: "terminal frozen,
can't answer the permission."

The fix runs the turn in a worker (``run_worker``), so the handler returns
immediately and the pump stays free. This test drives the **real** submit
path (posting ``ChatInput.Submitted``) with an adapter that pauses on a
``PermissionRequest``, and asserts the prompt both mounts and is resolvable
by a keypress — which is impossible if the pump is blocked.
"""

from __future__ import annotations

import asyncio

import pytest

from koda.agent_api import Done, PermissionItem, PermissionRequest
from koda.tui.app import KodaApp
from koda.tui.widgets import ChatInput, PermissionPrompt


class _PausingAdapter:
    """Minimal adapter: emits one PermissionRequest, waits for the decision,
    then finishes. No graph — just enough to drive ``run_turn``."""

    def __init__(self) -> None:
        self._fut: asyncio.Future | None = None
        self.decisions = None

    def model_name(self) -> str:
        return "test:model"

    async def interrupt(self) -> None:
        if self._fut is not None and not self._fut.done():
            self._fut.cancel()

    async def stream(self, message, history):
        self._fut = asyncio.get_running_loop().create_future()
        yield PermissionRequest(
            items=[PermissionItem(tool_name="execute", args={"command": "ls"})]
        )
        self.decisions = await self._fut
        yield Done()

    def provide_decisions(self, decisions) -> None:
        self.decisions = decisions
        if self._fut is not None and not self._fut.done():
            self._fut.set_result(decisions)


@pytest.mark.asyncio
async def test_turn_runs_in_worker_so_prompt_is_answerable():
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        adapter = _PausingAdapter()
        app._adapter = adapter  # type: ignore[assignment]
        ci = app.query_one(ChatInput)
        ci.focus()
        await pilot.pause()

        # Drive the real handler path (NOT a standalone task).
        app.post_message(ChatInput.Submitted(ci, "do something", "chat"))

        # If the submit handler blocked the pump, the turn would deadlock and
        # the prompt would never appear here.
        appeared = False
        for _ in range(80):
            await pilot.pause()
            if list(app.query(PermissionPrompt)):
                appeared = True
                break
        assert appeared, "permission prompt never mounted — pump blocked / deadlock?"

        # And a keypress must resolve it — proving the pump is free and keys
        # are reaching the prompt.
        await pilot.press("y")
        resolved = False
        for _ in range(80):
            await pilot.pause()
            if adapter.decisions is not None and not list(app.query(PermissionPrompt)):
                resolved = True
                break
        assert resolved, "prompt not resolved after 'y' — keys not reaching it"
        assert adapter.decisions == [{"type": "approve"}]
