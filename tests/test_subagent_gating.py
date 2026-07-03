"""Regression guard: writes performed *inside a subagent* are still gated.

KODA's old gate lived in the shared backend, so it covered subagent
mutations too. The new design gates via deepagents' ``interrupt_on``. This
test pins the (externally-provided) behavior that a declarative subagent
inherits the main graph's ``interrupt_on`` AND that a sub-agent
``interrupt()`` propagates to the *top-level* graph — which is what
``LangGraphAdapter`` inspects via ``aget_state``. If a deepagents upgrade
ever breaks that propagation, this test fails loudly instead of silently
letting subagent writes bypass the permission prompt.

It's an integration test (real ``create_deep_agent`` + ``AsyncSqliteSaver``)
driven by a scripted model, so it needs no network / API key.
"""

from __future__ import annotations

import pytest

from koda.adapters.langgraph import LangGraphAdapter
from koda.agent_api import PermissionRequest
from koda.tools import permissions as perms
from koda.modes import Mode


def _scripted(responses):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Scripted(BaseChatModel):
        responses: list = []
        idx: dict = {}

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools, **kw):
            return self

        def _make(self):
            i = self.idx.get("i", 0)
            self.idx["i"] = i + 1
            return ChatResult(
                generations=[ChatGeneration(message=self.responses[min(i, len(self.responses) - 1)])]
            )

        def _generate(self, *a, **k):
            return self._make()

        async def _agenerate(self, *a, **k):
            return self._make()

    return Scripted(responses=responses, idx={})


def _build_agent_with_subagent(model):
    import tempfile

    import aiosqlite
    from deepagents import create_deep_agent
    from deepagents.backends import LocalShellBackend
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    d = tempfile.mkdtemp()
    backend = LocalShellBackend(root_dir=d, virtual_mode=True)
    conn = aiosqlite.connect(":memory:", check_same_thread=False)
    graph = create_deep_agent(
        model=model,
        backend=backend,
        interrupt_on=perms.INTERRUPT_ON,
        checkpointer=AsyncSqliteSaver(conn),
        system_prompt="test",
        subagents=[
            {"name": "edit", "description": "apply edits", "system_prompt": "edit", "tools": []}
        ],
    )
    return graph, conn, d


@pytest.fixture(autouse=True)
def _reset_perms():
    perms.set_mode(Mode.DEFAULT)
    perms.clear_session_allow()
    yield
    perms.set_mode(Mode.DEFAULT)
    perms.clear_session_allow()


@pytest.mark.asyncio
async def test_subagent_write_is_gated_at_top_level():
    from langchain_core.messages import AIMessage

    import os

    model = _scripted([
        # main agent dispatches the edit subagent
        AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "write notes", "subagent_type": "edit"}, "id": "t1", "type": "tool_call"}]),
        # subagent tries to write — must trigger a top-level PermissionRequest
        AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"file_path": "/sub.md", "content": "x"}, "id": "w1", "type": "tool_call"}]),
        AIMessage(content="subagent done"),
        AIMessage(content="all done"),
    ])
    graph, conn, d = _build_agent_with_subagent(model)
    try:
        adapter = LangGraphAdapter(graph=graph, model="test:model", thread_id="sub-gate")
        saw_perm_for_write = False
        kinds = []
        async for ev in adapter.stream("delegate the edit", []):
            kinds.append(type(ev).__name__)
            if isinstance(ev, PermissionRequest):
                if any(i.tool_name == "write_file" for i in ev.items):
                    saw_perm_for_write = True
                adapter.provide_decisions([{"type": "approve"} for _ in ev.items])
            if len(kinds) > 80:  # safety against an unexpected loop
                break
        assert saw_perm_for_write, f"subagent write was NOT gated; events={kinds}"
        assert isinstance(kinds, list) and "Done" in kinds
        assert os.path.exists(os.path.join(d, "sub.md")), "approved write should land"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_subagent_write_rejected_in_plan_mode():
    """PLAN mode must block a subagent mutation too (auto-reject, no prompt)."""
    from langchain_core.messages import AIMessage

    perms.set_mode(Mode.PLAN)
    model = _scripted([
        AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "write", "subagent_type": "edit"}, "id": "t1", "type": "tool_call"}]),
        AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"file_path": "/p.md", "content": "x"}, "id": "w1", "type": "tool_call"}]),
        AIMessage(content="subagent done"),
        AIMessage(content="all done"),
    ])
    graph, conn, d = _build_agent_with_subagent(model)
    try:
        adapter = LangGraphAdapter(graph=graph, model="test:model", thread_id="sub-plan")
        kinds = []
        async for ev in adapter.stream("delegate", []):
            kinds.append(type(ev).__name__)
            if isinstance(ev, PermissionRequest):
                # In PLAN mode the adapter should auto-reject — if we get here
                # the policy failed to gate the subagent write.
                adapter.provide_decisions([{"type": "reject"} for _ in ev.items])
            if len(kinds) > 80:
                break
        import os
        assert "PermissionRequest" not in kinds, "PLAN must not prompt"
        assert not os.path.exists(os.path.join(d, "p.md")), "PLAN must block the write"
        assert "Done" in kinds
    finally:
        await conn.close()
