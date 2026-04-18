"""
Tests for the session-history sidebar (SessionPanel mounted in KodaApp).

Covers:
  - SessionPanel is mounted inside #sidebar-host.
  - Sidebar starts hidden; Ctrl+B toggles visibility.
  - Selecting a session loads its messages into the chat area.
  - Delete request removes the session file and refreshes the panel.
  - New-chat button resets the session.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from koda.session_panel import SessionInfo, SessionPanel
from koda.tui.app import KodaApp
from koda.tui.widgets import AppMessage, UserMessage


def _write_fake_session(dir_path: Path, session_id: str, user_text: str) -> Path:
    """Write a minimal valid JSONL session file and return its path."""
    # Use a far-future timestamp so scan_sessions' top-50 filter keeps it.
    ts = "20990101_000000"
    path = dir_path / f"{ts}.jsonl"
    header = {
        "id": "h0000000",
        "parent_id": None,
        "timestamp": "2099-01-01T00:00:00",
        "type": "header",
        "role": None,
        "content": "",
        "metadata": {"version": "v1", "session_id": session_id},
    }
    msg = {
        "id": "m0000000",
        "parent_id": "h0000000",
        "timestamp": "2099-01-01T00:01:00",
        "type": "message",
        "role": "user",
        "content": user_text,
        "metadata": {},
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        f.write(json.dumps(msg) + "\n")
    return path


@pytest.mark.asyncio
async def test_session_panel_is_mounted():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        panel = app.query_one(SessionPanel)
        assert panel is not None


@pytest.mark.asyncio
async def test_sidebar_hidden_initially():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        host = app._sidebar_host
        assert host is not None
        assert "visible" not in host.classes


@pytest.mark.asyncio
async def test_ctrl_b_toggles_sidebar():
    async with KodaApp().run_test(size=(120, 30)) as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()
        host = app._sidebar_host
        assert host is not None

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert "visible" in host.classes
        assert host.region.width > 0

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert "visible" not in host.classes


@pytest.mark.asyncio
async def test_selecting_session_loads_its_messages():
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        sess_dir = KodaApp._sessions_dir()
        fake = _write_fake_session(sess_dir, "test-sess-load", "saved hello")
        try:
            info = SessionInfo(
                path=fake,
                timestamp=datetime(2099, 1, 1),
                first_message="saved hello",
                message_count=1,
                session_id="test-sess-load",
            )
            await app.on_session_panel_session_selected(
                SessionPanel.SessionSelected(info)
            )
            await pilot.pause()

            assert app._koda_session.session_id == "test-sess-load"
            user_msgs = [c._content for c in app._messages_container.children
                         if isinstance(c, UserMessage)]
            assert "saved hello" in user_msgs
        finally:
            fake.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_new_chat_clears_messages(monkeypatch):
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        # Put one user message in flight
        await app._handle_user_message("first msg")
        await pilot.pause()
        assert any(isinstance(c, UserMessage) for c in app._messages_container.children)

        await app.on_session_panel_new_chat_requested(
            SessionPanel.NewChatRequested()
        )
        await pilot.pause()

        remaining = [c for c in app._messages_container.children
                     if isinstance(c, UserMessage)]
        assert remaining == [], "New-chat should clear user messages"


@pytest.mark.asyncio
async def test_delete_unlinks_file_when_confirmed(monkeypatch):
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        sess_dir = KodaApp._sessions_dir()
        fake = _write_fake_session(sess_dir, "test-sess-del", "doomed")
        assert fake.exists()

        # Short-circuit the confirmation modal
        async def auto_confirm(_screen, *a, **kw):
            return True

        monkeypatch.setattr(app, "push_screen_wait", auto_confirm)

        info = SessionInfo(
            path=fake,
            timestamp=datetime(2099, 1, 1),
            first_message="doomed",
            message_count=1,
            session_id="test-sess-del",
        )
        # The handler is @work-decorated — call the underlying coroutine
        handler = app.on_session_panel_session_delete_requested.__wrapped__
        await handler(app, SessionPanel.SessionDeleteRequested(info))
        await pilot.pause()

        assert not fake.exists(), "session file should be deleted"
        # Confirmation message mounted
        app_msgs = [m for m in app.query(AppMessage) if "Deleted" in m._content]
        assert app_msgs


@pytest.mark.asyncio
async def test_delete_aborted_keeps_file(monkeypatch):
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        await pilot.pause()

        sess_dir = KodaApp._sessions_dir()
        fake = _write_fake_session(sess_dir, "test-sess-keep", "alive")
        try:
            async def auto_cancel(_screen, *a, **kw):
                return False

            monkeypatch.setattr(app, "push_screen_wait", auto_cancel)

            info = SessionInfo(
                path=fake,
                timestamp=datetime(2099, 1, 1),
                first_message="alive",
                message_count=1,
                session_id="test-sess-keep",
            )
            handler = app.on_session_panel_session_delete_requested.__wrapped__
            await handler(app, SessionPanel.SessionDeleteRequested(info))
            await pilot.pause()

            assert fake.exists(), "file must survive a cancel"
        finally:
            fake.unlink(missing_ok=True)
