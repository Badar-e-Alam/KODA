"""Visual smoke test for KODA's user-facing widgets.

Run with:

    .venv/bin/python scripts/smoke_widgets.py

This script launches the full KODA TUI but skips the agent backend.
A daemon worker thread drives the demo end-to-end so all the cross-loop
plumbing (priority bindings, focus protection, soft pause, the
``call_from_thread`` bridge) is exercised exactly the way a real agent
would exercise it.

What you'll see, in order:

  1. A ``TodoMessage`` mounts with three items (one in progress).
     Eyeball the **orange left bar** and verify the in-progress glyph
     reads as bold.

  2. A second ``TodoMessage`` mounts with progress: first task
     completed (struck through), second task now in progress. Confirms
     the visual flow makes sense when todos update.

  3. A ``PermissionPrompt`` appears for a fake ``write_file`` call.
     Try every keypath:

         y         allow once
         a         always (writes to session allow-list)
         n / esc   deny
         ↑ / ↓     navigate the chip row
         enter     confirm the highlighted chip

     While the prompt is up, click into the chat composer and type —
     focus protection should snap focus back to the prompt so the
     hotkeys keep working.

  4. An ``AskUserPrompt`` appears with three options. Try:

         ↑ / ↓ or j / k     navigate
         1 / 2 / 3          jump to that option directly
         enter              submit
         esc                cancel (returns ``""``)

  5. A summary ``AppMessage`` reports both outcomes so you can verify
     the bridge round-trip delivered the right values to the worker.

Press Ctrl+C (or ``/quit``) to exit when you're done.
"""

from __future__ import annotations

import threading
import time

from koda.tui.app import KodaApp
from koda.tui.widgets import TodoMessage
from koda.tui.widgets.messages import AppMessage


# Seconds to wait between demo steps. Long enough that you can read
# each state without it feeling rushed; short enough that the whole
# demo finishes in ~10s of user time (plus however long you take to
# answer the two prompts).
_STEP_PAUSE = 2.0


def _drive_demo(app: KodaApp) -> None:
    """Sequence the demo on a worker thread.

    Mirrors the real agent's behavior: the agent backend runs on worker
    threads (LangGraph's tool nodes + ``asyncio.to_thread`` inside the
    gate wrapper), and the bridges (``_prompt_from_tool_thread`` and
    ``_ask_user_from_tool_thread``) are designed to be called from
    exactly that context. So driving the demo from a thread isn't a
    test shortcut — it's the production code path.
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

    # 3) Permission prompt — blocks here until the user picks.
    # Returns True for allow / always, False for deny.
    granted = app._prompt_from_tool_thread(
        "write_file", {"file_path": "/tmp/koda_smoke_example.py"}
    )
    time.sleep(0.4)

    # 4) Ask-user — blocks here until the user picks.
    # Returns the selected option's verbatim text, or "" on Esc.
    answer = app._ask_user_from_tool_thread(
        "Which database backend should the new ingestion pipeline use?",
        ["SQLite (default)", "Postgres", "DuckDB"],
    )
    time.sleep(0.4)

    # 5) Summary of both bridge round-trips so you can confirm the
    # worker actually received what the UI delivered.
    summary = (
        f"Smoke complete · permission granted={granted} · "
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
