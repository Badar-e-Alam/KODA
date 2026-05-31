"""Visual smoke test for KODA's user-facing widgets.

Run with:

    .venv/bin/python scripts/smoke_widgets.py

This script launches the full KODA TUI but skips the agent backend.
A daemon worker thread drives the demo end-to-end so all the cross-loop
plumbing (priority bindings, focus protection, soft pause, the
``call_from_thread`` bridge, and the interrupt-based permission resume)
is exercised exactly the way a real agent would exercise it.

What you'll see, in order:

  1. A ``TodoMessage`` mounts with three items (one in progress).
     Eyeball the **orange left bar** and verify the in-progress glyph
     reads as bold.

  2. A second ``TodoMessage`` mounts with progress: first task
     completed (struck through), second task now in progress. Confirms
     the visual flow makes sense when todos update.

  3. A ``PermissionPrompt`` via the **deep-adapter blocking bridge**
     (``_prompt_from_tool_thread``) for a fake ``write_file`` call. This
     is the path the ``deep`` adapter's synchronous ``@tool`` functions
     use — the worker thread blocks (not the event loop) until you pick.
     Try every keypath:

         y         allow once
         a         always (writes to session allow-list)
         n / esc   deny
         ↑ / ↓     navigate
         enter     confirm the highlighted option

     While the prompt is up, click into the chat composer and type —
     focus protection should snap focus back to the prompt so the
     hotkeys keep working.

  4. A ``PermissionPrompt`` via the **LangGraph interrupt flow**
     (``handle_permission_request`` → ``_on_permission_choice`` →
     ``adapter.provide_decisions``) for a single fake ``write_file``.
     This is the default ``coding_agent`` path: the graph pauses, the
     prompt mounts, and your choice is mapped to a HITL decision
     (``allow``/``always`` → ``approve``, ``deny`` → ``reject``). The
     summary at the end reports the exact decision delivered.

  5. A **multi-item** interrupt request (``edit_file`` then ``execute``)
     to exercise the sequential-prompt queue: answer the first card and
     a second one mounts in its place; both decisions come back as a
     list, in order.

  6. An ``AskUserPrompt`` appears with three options. Try:

         ↑ / ↓ or j / k     navigate
         1 / 2 / 3          jump to that option directly
         enter              submit
         esc                cancel (returns ``""``)

  7. A summary ``AppMessage`` reports every outcome so you can verify
     each bridge / resume round-trip delivered the right values.

Press Ctrl+C (or ``/quit``) to exit when you're done.
"""

from __future__ import annotations

import threading
import time

from koda.agent_api import PermissionItem, PermissionRequest
from koda.tui.app import KodaApp
from koda.tui.widgets import TodoMessage
from koda.tui.widgets.messages import AppMessage


# Seconds to wait between demo steps. Long enough that you can read
# each state without it feeling rushed; short enough that the whole
# demo finishes in ~10s of user time (plus however long you take to
# answer the prompts).
_STEP_PAUSE = 2.0


class _RecordingAdapter:
    """Minimal stand-in for a real adapter in the interrupt-flow demo.

    ``KodaApp.handle_permission_request`` reads ``app._adapter`` and, once
    the user answers, calls ``adapter.provide_decisions(decisions)`` to
    resume the (paused) graph. Here there's no graph — we just capture the
    decisions and signal the worker thread that's waiting on the answer,
    so the demo can print what the UI delivered.
    """

    def __init__(self) -> None:
        self.decisions = None
        self._done = threading.Event()

    def provide_decisions(self, decisions) -> None:
        self.decisions = decisions
        self._done.set()

    def model_name(self) -> str:
        return "test:model"

    def wait(self, timeout: float = 300.0):
        self._done.wait(timeout)
        return self.decisions


def _run_interrupt_flow(app: KodaApp, items: list[PermissionItem]):
    """Drive one ``PermissionRequest`` through the interrupt-based flow.

    Sets a recording adapter, mounts the prompt(s) via
    ``handle_permission_request`` (which returns immediately — it does NOT
    block), then waits on the worker thread for the user's decisions to
    arrive through ``provide_decisions``. Returns the decisions list.
    """
    rec = _RecordingAdapter()
    # handle_permission_request short-circuits if _adapter is None, so wire
    # the recording adapter in first. Plain attribute write is GIL-atomic;
    # the UI loop reads it inside the call_from_thread'd coroutine below.
    app._adapter = rec  # type: ignore[assignment]
    app.call_from_thread(app.handle_permission_request, PermissionRequest(items=items))
    return rec.wait()


def _drive_demo(app: KodaApp) -> None:
    """Sequence the demo on a worker thread.

    Mirrors the real agent's threading: the ``deep`` adapter's tool nodes
    run on worker threads and call ``_prompt_from_tool_thread`` /
    ``_ask_user_from_tool_thread`` from exactly that context, while the
    ``coding_agent`` adapter surfaces a ``PermissionRequest`` that the
    stream pump hands to ``handle_permission_request`` on the UI loop. We
    exercise both here, so driving the demo from a thread isn't a test
    shortcut — it's the production code path.
    """
    # Wait for ``on_mount`` to finish (sets ``_ui_loop`` + composes the
    # message container). Without this the first ``call_from_thread``
    # races with composition.
    time.sleep(1.5)

    # 1) Initial todo state — three pending/in-progress items.
    app.call_from_thread(
        app.mount_message,
        TodoMessage(
            [
                {"content": "Read the changelog", "status": "in_progress"},
                {"content": "Update the schema", "status": "pending"},
                {"content": "Run the migration", "status": "pending"},
            ]
        ),
    )
    time.sleep(_STEP_PAUSE)

    # 2) Progress — first task completed, second now in progress.
    app.call_from_thread(
        app.mount_message,
        TodoMessage(
            [
                {"content": "Read the changelog", "status": "completed"},
                {"content": "Update the schema", "status": "in_progress"},
                {"content": "Run the migration", "status": "pending"},
            ]
        ),
    )
    time.sleep(_STEP_PAUSE)

    # 3) Permission prompt — DEEP-adapter blocking bridge. Blocks the
    # worker thread here until the user picks. True for allow/always.
    granted = app._prompt_from_tool_thread(
        "write_file", {"file_path": "/tmp/koda_smoke_example.py"}
    )
    time.sleep(0.4)

    # 4) Permission prompt — coding_agent INTERRUPT flow, single item.
    # Returns a list with one HITL decision dict, e.g. [{"type": "approve"}].
    single = _run_interrupt_flow(
        app, [PermissionItem(tool_name="write_file", args={"file_path": "/tmp/koda_interrupt.py"})]
    )
    time.sleep(0.4)

    # 5) Permission prompt — interrupt flow, MULTI item (sequential cards).
    # Answer the edit_file card, then the execute card mounts; both
    # decisions come back as a list in action-request order.
    multi = _run_interrupt_flow(
        app,
        [
            PermissionItem(tool_name="edit_file", args={"file_path": "/tmp/a.py"}),
            PermissionItem(tool_name="execute", args={"command": "pytest -q"}),
        ],
    )
    time.sleep(0.4)

    # 6) Ask-user — blocks the worker until the user picks.
    # Returns the selected option's verbatim text, or "" on Esc.
    answer = app._ask_user_from_tool_thread(
        "Which database backend should the new ingestion pipeline use?",
        ["SQLite (default)", "Postgres", "DuckDB"],
    )
    time.sleep(0.4)

    # 7) Summary of every round-trip so you can confirm the worker / UI
    # delivered the right values.
    summary = (
        f"Smoke complete · deep-bridge granted={granted} · "
        f"interrupt single={single!r} · interrupt multi={multi!r} · "
        f"ask_user answer={answer!r} · press Ctrl+C to exit"
    )
    app.call_from_thread(app.mount_message, AppMessage(summary))


def main() -> None:
    # ``test:model`` is the same sentinel the headless tests pass — it
    # lets the app boot without API-key validation. The agent backend
    # never starts because no user turn is ever submitted.
    app = KodaApp(model="test:model")
    threading.Thread(target=_drive_demo, args=(app,), daemon=True).start()
    app.run()


if __name__ == "__main__":
    main()
