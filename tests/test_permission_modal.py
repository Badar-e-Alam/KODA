"""Regression tests for the LangGraph-native permission flow.

KODA gates mutating tools with deepagents' ``interrupt_on``: the graph
hits a human-in-the-loop ``interrupt()`` before running a gated tool,
pausing and checkpointing its state. The adapter surfaces that as a
``PermissionRequest`` event; the TUI shows an inline ``PermissionPrompt``
and resumes the graph with ``Command(resume=…)`` once the user decides.

Nothing blocks — not the event loop, not a worker thread — so the TUI
stays interactive while the agent is paused. These tests pin three layers:

  1. the decision *policy* (``koda.tools.permissions.decide``),
  2. the adapter's interrupt → ``PermissionRequest`` → resume loop, and
  3. the TUI's prompt → ``provide_decisions`` wiring.
"""

from __future__ import annotations

from typing import TypedDict

import pytest

from koda.adapters.langgraph import LangGraphAdapter
from koda.agent_api import Done, PermissionItem, PermissionRequest
from koda.tools import permissions as perms
from koda.tui.app import KodaApp
from koda.tui.modes import Mode
from koda.tui.widgets import PermissionPrompt


@pytest.fixture(autouse=True)
def _reset_perms():
    """Each test starts from DEFAULT mode with an empty allow-list."""
    perms.set_mode(Mode.DEFAULT)
    perms.clear_session_allow()
    yield
    perms.set_mode(Mode.DEFAULT)
    perms.clear_session_allow()


# ─── 1. policy ───────────────────────────────────────────────────────


def test_decide_default_asks():
    assert perms.decide("write_file", {}) == "ask"
    assert perms.decide("execute", {}) == "ask"


def test_decide_plan_rejects_all_mutators():
    perms.set_mode(Mode.PLAN)
    assert perms.decide("write_file", {}) == "reject"
    assert perms.decide("edit_file", {}) == "reject"
    assert perms.decide("execute", {}) == "reject"


def test_decide_edits_approves_file_edits_but_asks_execute():
    perms.set_mode(Mode.EDITS)
    assert perms.decide("write_file", {}) == "approve"
    assert perms.decide("edit_file", {}) == "approve"
    assert perms.decide("multi_edit", {}) == "approve"
    assert perms.decide("execute", {}) == "ask"  # shell still prompts in EDITS


def test_decide_session_allow_approves():
    perms.allow_tool("execute")
    assert perms.decide("execute", {}) == "approve"


def test_decide_non_mutating_tool_auto_approves():
    # A tool that isn't gated should never wedge the resume loop.
    assert perms.decide("read_file", {}) == "approve"


def test_reject_message_plan_mode_points_to_apply():
    perms.set_mode(Mode.PLAN)
    msg = perms.reject_message("write_file")
    assert "plan mode" in msg.lower()
    assert "Shift+A" in msg or "apply" in msg


# ─── 2. adapter interrupt → resume ───────────────────────────────────


class _S(TypedDict):
    messages: list


def _build_interrupting_graph():
    """A tiny checkpointed graph that interrupts once with a HITL-shaped
    payload (mirrors what ``HumanInTheLoopMiddleware`` emits), then finishes
    after the resume."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    def node(state: _S):
        decision = interrupt(
            {
                "action_requests": [
                    {"name": "write_file", "args": {"file_path": "/AGENTS.md"}, "description": "d"}
                ],
                "review_configs": [
                    {"action_name": "write_file", "allowed_decisions": ["approve", "reject"]}
                ],
            }
        )
        return {"messages": [*state["messages"], {"decision": decision}]}

    g = StateGraph(_S)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    conn = aiosqlite.connect(":memory:", check_same_thread=False)
    return g.compile(checkpointer=AsyncSqliteSaver(conn)), conn


@pytest.mark.asyncio
async def test_adapter_emits_permission_request_then_resumes_on_approve():
    graph, conn = _build_interrupting_graph()
    try:
        adapter = LangGraphAdapter(graph=graph, model="test:model", thread_id="t-approve")
        events = []
        async for ev in adapter.stream("go", []):
            events.append(ev)
            if isinstance(ev, PermissionRequest):
                # Loop stayed responsive; deliver the user's choice.
                assert ev.items and ev.items[0].tool_name == "write_file"
                adapter.provide_decisions([{"type": "approve"}])
        kinds = [type(e).__name__ for e in events]
        assert "PermissionRequest" in kinds, kinds
        assert isinstance(events[-1], Done), "stream must finish after resume"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_adapter_auto_rejects_in_plan_mode_without_prompting():
    """PLAN mode resolves the interrupt with a reject — no PermissionRequest
    should ever reach the TUI."""
    perms.set_mode(Mode.PLAN)
    graph, conn = _build_interrupting_graph()
    try:
        adapter = LangGraphAdapter(graph=graph, model="test:model", thread_id="t-plan")
        events = [ev async for ev in adapter.stream("go", [])]
        kinds = [type(e).__name__ for e in events]
        assert "PermissionRequest" not in kinds, kinds
        assert isinstance(events[-1], Done)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_adapter_auto_approves_when_session_allowed():
    perms.allow_tool("write_file")
    graph, conn = _build_interrupting_graph()
    try:
        adapter = LangGraphAdapter(graph=graph, model="test:model", thread_id="t-allow")
        events = [ev async for ev in adapter.stream("go", [])]
        kinds = [type(e).__name__ for e in events]
        assert "PermissionRequest" not in kinds, kinds
        assert isinstance(events[-1], Done)
    finally:
        await conn.close()


# ─── 3. TUI prompt → provide_decisions ───────────────────────────────


class _RecordingAdapter:
    """Stand-in adapter that records the decisions handed back to it."""

    def __init__(self) -> None:
        self.decisions = None
        self.calls = 0

    def provide_decisions(self, decisions):
        self.decisions = decisions
        self.calls += 1

    def model_name(self) -> str:
        return "test:model"


async def _drive_prompt(app: KodaApp, pilot, tool_name: str, key: str):
    """Mount a one-item PermissionRequest, press ``key``, return the recording
    adapter and whether the prompt was cleared."""
    rec = _RecordingAdapter()
    app._adapter = rec  # type: ignore[assignment]
    req = PermissionRequest(
        items=[PermissionItem(tool_name=tool_name, args={"file_path": "AGENTS.md"})]
    )
    await app.handle_permission_request(req)

    appeared = False
    for _ in range(60):
        await pilot.pause()
        if list(app.query(PermissionPrompt)):
            appeared = True
            break
    assert appeared, "PermissionPrompt never mounted"

    await pilot.press(key)
    for _ in range(30):
        await pilot.pause()
        if rec.calls:
            break
    cleared = not list(app.query(PermissionPrompt))
    return rec, cleared


@pytest.mark.asyncio
async def test_prompt_allow_resumes_with_approve():
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        rec, cleared = await _drive_prompt(app, pilot, "write_file", "y")
        assert rec.decisions == [{"type": "approve"}]
        assert cleared
        assert "write_file" not in perms._session_allow  # allow-once ≠ remember
        assert app._awaiting_permission is False


@pytest.mark.asyncio
async def test_prompt_always_approves_and_remembers():
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        rec, cleared = await _drive_prompt(app, pilot, "edit_file", "a")
        assert rec.decisions == [{"type": "approve"}]
        assert cleared
        assert "edit_file" in perms._session_allow


@pytest.mark.asyncio
async def test_prompt_deny_resumes_with_reject():
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        rec, cleared = await _drive_prompt(app, pilot, "execute", "n")
        assert rec.decisions is not None
        assert rec.decisions[0]["type"] == "reject"
        assert cleared


@pytest.mark.asyncio
async def test_prompt_escape_denies():
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        rec, cleared = await _drive_prompt(app, pilot, "execute", "escape")
        assert rec.decisions is not None
        assert rec.decisions[0]["type"] == "reject"
        assert cleared


# ─── 3b. composer lock — keys reach the prompt, not the composer ──────
#
# Regression for the live-app bug where the prompt was up but ↑/↓ and
# y/a/n did nothing: the focused ChatInput swallowed them (y/a/n as text,
# arrows as history) because the prompt's priority bindings only fire when
# the prompt holds focus. The fix disables the composer while a prompt is
# up and forces focus to the prompt.


@pytest.mark.asyncio
async def test_prompt_locks_composer_and_key_reaches_prompt():
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        # Reproduce the real scenario: the composer is focused first.
        app._chat_input.focus()
        await pilot.pause()
        rec = _RecordingAdapter()
        app._adapter = rec  # type: ignore[assignment]
        await app.handle_permission_request(
            PermissionRequest(items=[PermissionItem(tool_name="execute", args={"command": "ls"})])
        )
        appeared = False
        for _ in range(60):
            await pilot.pause()
            if list(app.query(PermissionPrompt)):
                appeared = True
                break
        assert appeared
        # Composer must be locked so it can't eat the prompt's keys.
        assert app._chat_input.disabled is True

        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()
            if rec.calls:
                break
        assert rec.decisions == [{"type": "approve"}]
        # The 'y' must NOT have been typed into the composer.
        assert app._chat_input.value == ""
        # …and the composer is handed back afterwards.
        assert app._chat_input.disabled is False


@pytest.mark.asyncio
async def test_prompt_key_routes_via_app_fallback_when_unfocused():
    """If a focus race leaves the card unfocused, the app-level on_key
    fallback must still route the key to it (composer is disabled, so the
    key bubbles to the app instead of being eaten)."""
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        rec = _RecordingAdapter()
        app._adapter = rec  # type: ignore[assignment]
        await app.handle_permission_request(
            PermissionRequest(items=[PermissionItem(tool_name="execute", args={"command": "ls"})])
        )
        for _ in range(60):
            await pilot.pause()
            if list(app.query(PermissionPrompt)):
                break
        # Simulate the focus race: nothing is focused.
        app.set_focus(None)
        await pilot.pause()
        await pilot.press("y")
        for _ in range(30):
            await pilot.pause()
            if rec.calls:
                break
        assert rec.decisions == [{"type": "approve"}]


@pytest.mark.asyncio
async def test_prompt_arrow_nav_with_locked_composer():
    """↓↓ + Enter must navigate the prompt (allow→always→deny) — not the
    composer's history — and land on deny → reject."""
    app = KodaApp(model="test:model")
    async with app.run_test() as pilot:
        app._chat_input.focus()
        await pilot.pause()
        rec = _RecordingAdapter()
        app._adapter = rec  # type: ignore[assignment]
        await app.handle_permission_request(
            PermissionRequest(items=[PermissionItem(tool_name="write_file", args={"file_path": "x"})])
        )
        for _ in range(60):
            await pilot.pause()
            if list(app.query(PermissionPrompt)):
                break
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if rec.calls:
                break
        assert rec.decisions is not None
        assert rec.decisions[0]["type"] == "reject"  # 0=allow →1=always →2=deny
        assert app._chat_input.value == ""
