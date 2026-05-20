"""
Session sidebar panel for KODA.

Displays a list of past sessions with message previews, allowing
users to browse and switch between them.  Inspired by ralph-tui's
task sidebar layout.

Messages:
  SessionPanel.SessionSelected  — user picked an existing session
  SessionPanel.NewChatRequested — user wants a fresh session
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, ListItem, ListView, OptionList, Static
from textual.widgets.option_list import Option


# ── Lightweight session metadata ────────────────────────────────────────


@dataclass
class SessionInfo:
    """Metadata extracted from a session JSONL file (fast scan)."""

    path: Path
    timestamp: datetime
    first_message: str
    message_count: int
    session_id: str

    @property
    def display_time(self) -> str:
        return self.timestamp.strftime("%b %d, %H:%M")

    @property
    def title(self) -> str:
        if self.first_message:
            msg = self.first_message.replace("\n", " ").strip()
            if len(msg) > 35:
                return msg[:32] + "\u2026"
            return msg
        return "Empty session"


def scan_sessions(sessions_dir: Path, limit: int = 50) -> list[SessionInfo]:
    """Scan *sessions_dir* for JSONL session files and return metadata.

    Returns a list sorted newest-first, capped at *limit*.  Only the
    first ~30 lines of each file are read so this stays fast even with
    large session histories.
    """
    if not sessions_dir.exists():
        return []

    sessions: list[SessionInfo] = []
    files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:limit]

    for fpath in files:
        # Derive timestamp from filename (e.g. 20260413_223352.jsonl)
        try:
            timestamp = datetime.strptime(fpath.stem, "%Y%m%d_%H%M%S")
        except ValueError:
            continue

        first_user_msg = ""
        msg_count = 0
        session_id = ""

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i > 30:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") == "header":
                        session_id = entry.get("metadata", {}).get(
                            "session_id", ""
                        )
                    if entry.get("type") == "message":
                        msg_count += 1
                        if not first_user_msg and entry.get("role") == "user":
                            first_user_msg = entry.get("content", "")

            # Fast total-line count as a rough message estimate.
            with open(fpath, "r", encoding="utf-8") as f:
                total_lines = sum(1 for _ in f)
            msg_count = max(msg_count, total_lines - 1)
        except OSError:
            continue

        sessions.append(
            SessionInfo(
                path=fpath,
                timestamp=timestamp,
                first_message=first_user_msg,
                message_count=msg_count,
                session_id=session_id,
            )
        )

    return sessions


# ── Widgets ─────────────────────────────────────────────────────────────


class SessionItem(ListItem):
    """Single session row inside the sidebar list."""

    DEFAULT_CSS = """
    SessionItem {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(
        self, info: SessionInfo, is_active: bool = False, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.info = info
        self._is_active = is_active

    def compose(self) -> ComposeResult:
        if self._is_active:
            yield Static(
                f"[bold green]{self.info.display_time}[/] "
                f"[bold yellow]\u25c0 active[/]\n"
                f"  [bold]{self.info.title}[/]"
            )
        else:
            yield Static(
                f"[dim]{self.info.display_time}[/]  "
                f"[dim cyan]{self.info.message_count} msgs[/]\n"
                f"  {self.info.title}"
            )


class SessionListView(ListView):
    """ListView that forwards Delete/Backspace to the parent SessionPanel."""

    BINDINGS = [
        Binding("delete", "request_delete", "Delete session", show=False),
        Binding("backspace", "request_delete", "Delete session", show=False),
    ]

    def action_request_delete(self) -> None:
        item = self.highlighted_child
        if isinstance(item, SessionItem):
            # Message bubbles up through the DOM to KodaApp
            self.post_message(SessionPanel.SessionDeleteRequested(item.info))


class SessionPanel(Vertical):
    """Left sidebar showing all sessions for the current project."""

    # ── Custom messages ──────────────────────────────────────────────

    class SessionSelected(Message):
        """User picked an existing session from the list."""

        def __init__(self, session_info: SessionInfo) -> None:
            super().__init__()
            self.session_info = session_info

    class NewChatRequested(Message):
        """User wants to start a fresh session."""

    class SessionDeleteRequested(Message):
        """User wants to delete a session."""

        def __init__(self, session_info: SessionInfo) -> None:
            super().__init__()
            self.session_info = session_info

    # ── Styling ──────────────────────────────────────────────────────

    DEFAULT_CSS = """
    SessionPanel {
        width: 34;
        height: 100%;
        border-right: solid $primary 30%;
        background: $surface;
    }

    SessionPanel.-hidden {
        display: none;
    }

    SessionPanel #panel-header {
        height: auto;
        padding: 0 1;
    }

    SessionPanel #panel-title {
        height: 1;
        text-style: bold;
        color: $success;
        margin: 0 0 0 0;
    }

    SessionPanel #new-chat-btn {
        width: 100%;
        margin: 0 0 1 0;
    }

    SessionPanel #session-list {
        height: 1fr;
        background: $surface;
    }

    SessionPanel #session-list > ListItem {
        background: $surface;
    }

    SessionPanel #session-list > ListItem:hover {
        background: $primary 10%;
    }

    SessionPanel #session-list:focus > .--highlight {
        background: $primary 20%;
    }

    SessionPanel #session-count {
        height: auto;
        max-height: 2;
        dock: bottom;
        color: $text-muted;
        padding: 0 1;
    }
    """

    # ── Init / compose ───────────────────────────────────────────────

    def __init__(
        self,
        sessions_dir: Path,
        current_session_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sessions_dir = sessions_dir
        self._current_session_id = current_session_id
        self._sessions: list[SessionInfo] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="panel-header"):
            yield Static("[bold green]Sessions[/]", id="panel-title")
            yield Button("+ New Chat", id="new-chat-btn", variant="success")
        yield SessionListView(id="session-list")
        yield Static("", id="session-count")

    def on_mount(self) -> None:
        self.refresh_sessions()

    # ── Public API ───────────────────────────────────────────────────

    def refresh_sessions(self) -> None:
        """Re-scan session files and rebuild the list."""
        self._sessions = scan_sessions(self._sessions_dir)
        list_view = self.query_one("#session-list", SessionListView)
        list_view.clear()

        for info in self._sessions:
            is_active = info.session_id == self._current_session_id
            list_view.append(SessionItem(info, is_active=is_active))

        count_label = self.query_one("#session-count", Static)
        count_label.update(
            f"[dim]{len(self._sessions)} sessions[/]\n"
            f"[dim]Del[/] delete  [dim]Ctrl+B[/] toggle"
        )

    def set_active_session(self, session_id: str) -> None:
        """Mark a different session as active and refresh."""
        self._current_session_id = session_id
        self.refresh_sessions()

    # ── Event handlers ───────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SessionItem):
            self.post_message(self.SessionSelected(item.info))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-chat-btn":
            event.stop()
            self.post_message(self.NewChatRequested())


# ── Confirmation modal ──────────────────────────────────────────────────


class ConfirmDeleteScreen(ModalScreen[bool]):
    """Modal asking the user to confirm session deletion."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }

    #confirm-container {
        width: 60;
        height: auto;
        border: solid $error 40%;
        background: $surface;
        padding: 1 2;
    }

    #confirm-title {
        text-style: bold;
        color: $error;
        margin: 0 0 1 0;
    }

    #confirm-options {
        height: auto;
        max-height: 6;
        border: none;
        background: $surface;
        margin: 1 0 0 0;
    }

    #confirm-options > .option-list--option-highlighted {
        background: $error 20%;
    }

    #confirm-help {
        color: $text-muted;
        margin: 1 0 0 0;
    }
    """

    def __init__(self, session_info: SessionInfo) -> None:
        super().__init__()
        self._info = session_info

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-container"):
            yield Static("Delete Session?", id="confirm-title")
            yield Static(
                f"[dim]{self._info.display_time}[/]  "
                f"{self._info.title}\n"
                f"[dim]{self._info.message_count} messages — "
                f"this cannot be undone[/]"
            )
            yield OptionList(
                Option("[bold red]Delete[/]  — permanently remove this session", id="yes"),
                Option("[bold]Cancel[/]  — keep the session", id="no"),
                id="confirm-options",
            )
            yield Static(
                "[dim]Up/Down[/] navigate  |  "
                "[dim]Enter[/] select  |  "
                "[dim]Esc[/] cancel",
                id="confirm-help",
            )

    def on_mount(self) -> None:
        options = self.query_one("#confirm-options", OptionList)
        options.highlighted = 1  # Default to Cancel (safer)
        options.focus()

    def on_option_list_option_selected(self, event) -> None:
        event.stop()
        self.dismiss(str(event.option.id) == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)
