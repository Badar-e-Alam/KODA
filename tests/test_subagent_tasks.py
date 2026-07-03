"""Background-subagent execution-control registry: the spawn / stop / resume /
restart lifecycle that gives subagents interrupt-and-resume 'memory'.

Uses a fake adapter so the lifecycle is exercised deterministically without a
real model."""

from __future__ import annotations

import asyncio

import pytest

from koda.agent_api import Done, TextDelta, ToolResult, ToolStart
from koda.subagent_tasks import BackgroundTaskRegistry


class FakeAdapter:
    """Minimal KodaAgent-shaped stub. Replays a scripted event list, optionally
    slowly so a test can cancel it mid-stream."""

    def __init__(self, model=None, thread_id=None, script=None, slow=False):
        self.model = model
        self.thread_id = thread_id
        self._script = script or [TextDelta("hello"), Done(usage=None)]
        self._slow = slow
        self.interrupted = False

    async def stream(self, message, history):
        for ev in self._script:
            if self._slow:
                await asyncio.sleep(0.15)
            yield ev

    async def interrupt(self):
        self.interrupted = True

    def provide_decisions(self, decisions):
        pass

    async def aclose(self):
        pass


def make_factory(script=None, slow=False):
    def factory(model, thread_id):
        return FakeAdapter(model=model, thread_id=thread_id, script=script, slow=slow)

    return factory


async def _drain(task):
    if task._task is not None:
        await asyncio.gather(task._task, return_exceptions=True)


@pytest.mark.asyncio
async def test_spawn_runs_to_done_and_captures_result():
    script = [
        ToolStart(tool_id="t1", name="ls", arguments={}),
        ToolResult(tool_id="t1", output="ok", is_error=False),
        TextDelta("final "),
        TextDelta("answer"),
        Done(usage=None),
    ]
    reg = BackgroundTaskRegistry(factory=make_factory(script), model="m", on_update=None)
    tid = reg.spawn("do a thing", "explore")
    task = reg.get(tid)
    await _drain(task)
    assert task.summary.state == "success"
    assert task.summary.tool_count == 1
    assert task.final_text == "final answer"
    assert reg.active_count() == 0


@pytest.mark.asyncio
async def test_stop_cancels_and_marks_stopped():
    long_script = [TextDelta("x")] * 20 + [Done(usage=None)]
    reg = BackgroundTaskRegistry(factory=make_factory(long_script, slow=True), model="m", on_update=None)
    tid = reg.spawn("long job", "plan")
    task = reg.get(tid)
    # let it start
    for _ in range(20):
        await asyncio.sleep(0.05)
        if task.summary.state == "running":
            break
    assert reg.stop(tid) is True
    await _drain(task)
    assert task.summary.state == "cancelled"
    assert task.adapter.interrupted is True  # best-effort unwind was called


@pytest.mark.asyncio
async def test_resume_reruns_same_task():
    reg = BackgroundTaskRegistry(factory=make_factory([TextDelta("hi"), Done(usage=None)]), model="m", on_update=None)
    tid = reg.spawn("job", "general-purpose")
    task = reg.get(tid)
    await _drain(task)
    assert task.summary.state == "success"
    first_adapter = task.adapter
    assert reg.resume(tid, "keep going") is True
    await _drain(task)
    assert task.summary.state == "success"
    assert task.adapter is first_adapter  # resume keeps the SAME thread (memory)


@pytest.mark.asyncio
async def test_restart_uses_fresh_adapter():
    reg = BackgroundTaskRegistry(factory=make_factory([TextDelta("hi"), Done(usage=None)]), model="m", on_update=None)
    tid = reg.spawn("job", "explore")
    task = reg.get(tid)
    await _drain(task)
    first_adapter = task.adapter
    assert reg.restart(tid) is True
    await _drain(task)
    assert task.adapter is not first_adapter  # restart = clean slate, new thread
    assert task.summary.state == "success"


@pytest.mark.asyncio
async def test_update_callback_fires_and_reports_done():
    events: list[tuple[str, bool]] = []

    async def on_update(task, done):
        events.append((task.summary.state, done))

    reg = BackgroundTaskRegistry(
        factory=make_factory([TextDelta("hi"), Done(usage=None)]), model="m", on_update=on_update
    )
    tid = reg.spawn("job", "explore")
    await _drain(reg.get(tid))
    assert any(done for _, done in events)  # a done=True update was emitted
    assert events[-1][0] == "success"


@pytest.mark.asyncio
async def test_list_and_get_unknown():
    reg = BackgroundTaskRegistry(factory=make_factory(), model="m", on_update=None)
    assert reg.get("nope") is None
    assert reg.stop("nope") is False
    assert reg.resume("nope") is False
    tid = reg.spawn("job")
    await _drain(reg.get(tid))
    assert [s.id for s in reg.list()] == [tid]
