"""
KodaApp — the KODA TUI.

Pure Textual app. Drives any `KodaAgent` via the event-stream contract in
`koda.agent_api`. No `deepagents_cli` dependency.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll

from koda import __version__
from koda.agent_api import KodaAgent
from koda.conversation_log import ConversationLog
from koda.model_config import ModelSpec, has_provider_credentials
from koda.session import SessionTree
from koda.tui.commands import dispatch as dispatch_command
from koda.tui.stream import run_turn
from koda.tui.theme import DEFAULT_THEME, get as get_theme
from koda.tui.widgets import (
    AppMessage,
    AssistantMessage,
    BaseMessage,
    ChatInput,
    ErrorMessage,
    KodaBanner,
    StatusBar,
    SuggestionPopup,
    UserMessage,
)
from koda.tui.widgets.messages import ToolCallMessage

_log = logging.getLogger("koda")


class KodaApp(App):
    """KODA — your AI companion in the terminal."""

    TITLE = "KODA"
    CSS_PATH = "app.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "copy_or_interrupt", "Copy/Interrupt", show=False, priority=True),
        Binding("ctrl+t", "open_tree", "Session Tree", show=False),
        Binding("ctrl+y", "yank_last", "Copy last response", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Toggle sidebar", show=False),
        Binding("ctrl+l", "clear_session", "New chat", show=False),
    ]

    def __init__(
        self,
        *,
        adapter: KodaAgent | None = None,
        agent: Any | None = None,  # legacy: raw LangGraph graph
        model: str = "",
        auto_approve: bool = False,
        thread_id: str | None = None,
    ) -> None:
        super().__init__()
        self._adapter: KodaAgent | None = adapter
        if adapter is None and agent is not None:
            from koda.adapters.langgraph import LangGraphAdapter

            self._adapter = LangGraphAdapter(graph=agent, model=model, thread_id=thread_id)
        self._model = model or (self._adapter.model_name() if self._adapter else "")
        self._auto_approve = auto_approve
        self._thread_id = thread_id or uuid.uuid4().hex

        self._koda_session = SessionTree(path=self._new_session_path())
        self._conv_log = self._new_conversation_log()

        self._history: list[dict[str, Any]] = []
        self._chat_input: ChatInput | None = None
        self._status_bar: StatusBar | None = None
        self._messages_container: VerticalScroll | None = None
        self._banner: KodaBanner | None = None
        self._sidebar_host = None
        self._popup: SuggestionPopup | None = None
        self._last_assistant_widget: AssistantMessage | None = None
        self._turn_task: asyncio.Task | None = None

    # ── Session file paths ──────────────────────────────────────────

    @staticmethod
    def _sessions_dir() -> Path:
        cwd_slug = os.getcwd().replace("\\", "--").replace("/", "--").strip("-")
        d = Path.home() / ".koda" / "sessions" / cwd_slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def _new_session_path(cls) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return cls._sessions_dir() / f"{ts}.jsonl"

    def _new_conversation_log(self) -> ConversationLog:
        project_root = Path(__file__).resolve().parent.parent.parent
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return ConversationLog(log_dir / f"conversation_{ts}.md", model=self._model)

    # ── Compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="app-root"):
            yield KodaBanner(thread_id=self._thread_id)
            with Horizontal(id="main-row"):
                yield Vertical(id="sidebar-host")
                with Vertical(id="chat-area"):
                    yield VerticalScroll(id="messages")
                    yield SuggestionPopup(id="suggestions")
                    yield ChatInput()
            yield StatusBar()

    async def on_mount(self) -> None:
        self._chat_input = self.query_one(ChatInput)
        self._status_bar = self.query_one(StatusBar)
        self._messages_container = self.query_one("#messages", VerticalScroll)
        self._banner = self.query_one(KodaBanner)
        self._sidebar_host = self.query_one("#sidebar-host")
        self._popup = self.query_one(SuggestionPopup)
        self._chat_input.attach_popup(self._popup)
        self.apply_theme(DEFAULT_THEME)

        parsed = ModelSpec.try_parse(self._model)
        if parsed:
            self._status_bar.set_model(parsed.provider, parsed.model)
        else:
            self._status_bar.set_model("", self._model)

        if self._banner:
            self._banner.set_connected()
        self._chat_input.focus()

    # ── Theme ────────────────────────────────────────────────────────

    def apply_theme(self, name: str) -> None:
        """Theme switching is a no-op in Phase 2.

        The app.tcss baked-in defaults render the 'koda' palette. Live theme
        swapping needs Textual's theme API; deferred to Phase 3.
        """
        _log.debug("apply_theme(%s) — no-op (CSS defaults in use)", name)

    # ── Input event ──────────────────────────────────────────────────

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        await self._handle_user_message(event.value)

    async def on_chat_input_suggestions_requested(
        self, event: ChatInput.SuggestionsRequested
    ) -> None:
        """Compute suggestions for the current input value & update popup."""
        from koda.tui.completers import complete

        result = complete(event.value, event.cursor)
        if result is None:
            if self._popup is not None:
                self._popup.clear()
            self._chat_input._last_replace_range = None  # type: ignore[attr-defined]
            return
        suggestions, replace_range, title = result
        self._chat_input._last_replace_range = replace_range  # type: ignore[attr-defined]
        if self._popup is not None:
            self._popup.set_suggestions(suggestions, title=title)

    # ── Message mount helper ─────────────────────────────────────────

    async def mount_message(self, widget: BaseMessage) -> None:
        assert self._messages_container is not None
        if isinstance(widget, ToolCallMessage):
            self._conv_log.tool_call(widget._tool_name, widget._args)
        elif isinstance(widget, AssistantMessage):
            self._last_assistant_widget = widget
        await self._messages_container.mount(widget)
        self._messages_container.scroll_end(animate=False)

    # ── Core turn ────────────────────────────────────────────────────

    async def _handle_user_message(self, message: str) -> None:
        self._koda_session.add_message("user", message)
        self._conv_log.user(message)
        await self.mount_message(UserMessage(message))

        # Slash command
        if message.startswith("/"):
            if await dispatch_command(self, message):
                return

        # Shell mode (! prefix)
        if message.startswith("!"):
            cmd = message[1:].strip()
            await self._run_shell(cmd)
            return

        # Normal agent turn
        if self._adapter is None:
            await self.mount_message(ErrorMessage("No agent attached."))
            return

        self._history.append({"role": "user", "content": message})
        self._turn_task = asyncio.create_task(
            run_turn(self, self._adapter, message, self._history[:-1])
        )
        try:
            reply = await self._turn_task
        except asyncio.CancelledError:
            await self.mount_message(AppMessage("(interrupted)"))
            return
        finally:
            self._turn_task = None

        if reply:
            self._history.append({"role": "assistant", "content": reply})
            self._koda_session.add_message("assistant", reply)
            self._conv_log.assistant(reply)

    async def _run_shell(self, cmd: str) -> None:
        """Execute a shell command and mount the output as an AssistantMessage."""
        if not cmd:
            await self.mount_message(ErrorMessage("Empty shell command."))
            return
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = (result.stdout or "") + (result.stderr or "")
            text = output.strip() or f"[exit {result.returncode}]"
        except subprocess.TimeoutExpired:
            text = "[timed out]"
        except OSError as e:
            text = f"[error: {e}]"
        msg = AssistantMessage(text)
        await self.mount_message(msg)
        self._conv_log.assistant(text)

    # ── Model switching ──────────────────────────────────────────────

    async def switch_model(self, model_spec: str) -> None:
        parsed = ModelSpec.try_parse(model_spec)
        display = model_spec
        if parsed:
            display = f"{parsed.provider}:{parsed.model}"
            creds = has_provider_credentials(parsed.provider)
            if creds is False:
                await self.mount_message(
                    ErrorMessage(f"Missing credentials for {parsed.provider}")
                )
                return

        try:
            from koda.adapters.deep import create_deep_adapter

            self._adapter = create_deep_adapter(model=display, thread_id=self._thread_id)
            self._model = display
            if self._status_bar is not None:
                p = parsed.provider if parsed else ""
                m = parsed.model if parsed else display
                self._status_bar.set_model(provider=p, model=m)
            await self.mount_message(AppMessage(f"Switched to {display}"))
        except Exception as e:
            _log.exception("Model switch failed")
            await self.mount_message(ErrorMessage(f"Failed to switch model: {e}"))

    # ── Actions ──────────────────────────────────────────────────────

    async def action_clear_session(self) -> None:
        """Start a new session (clears messages and history)."""
        assert self._messages_container is not None
        for child in list(self._messages_container.children):
            await child.remove()
        self._history.clear()
        self._koda_session = SessionTree(path=self._new_session_path())
        self._conv_log = self._new_conversation_log()
        self._last_assistant_widget = None
        if self._status_bar is not None:
            self._status_bar.reset_usage()
        await self.mount_message(AppMessage("New session started"))

    @work(exclusive=True)
    async def action_open_tree(self) -> None:
        """Open the session tree modal. Runs in a worker for push_screen_wait."""
        has_messages = any(
            e.type == "message" for e in self._koda_session.entries.values()
        )
        if not has_messages:
            await self.mount_message(AppMessage("No messages yet — tree is empty"))
            return
        from koda.tree_widget import TreeScreen

        screen = TreeScreen(self._koda_session)
        await self.push_screen_wait(screen)

    async def action_yank_last(self) -> None:
        if self._last_assistant_widget is None:
            await self.mount_message(AppMessage("No response to copy"))
            return
        try:
            import pyperclip

            pyperclip.copy(self._last_assistant_widget._content)
            await self.mount_message(AppMessage("Copied last response"))
        except ImportError:
            await self.mount_message(
                ErrorMessage("Install 'pyperclip' to enable clipboard copy")
            )

    async def action_toggle_sidebar(self) -> None:
        if self._sidebar_host is None:
            return
        if "visible" in self._sidebar_host.classes:
            self._sidebar_host.remove_class("visible")
        else:
            self._sidebar_host.add_class("visible")

    async def action_copy_or_interrupt(self) -> None:
        """Ctrl+C — interrupt a running turn, else fall through to default copy."""
        if self._turn_task and not self._turn_task.done():
            if self._adapter is not None:
                await self._adapter.interrupt()
            self._turn_task.cancel()
            return
        # Else: exit the app
        self.exit()
