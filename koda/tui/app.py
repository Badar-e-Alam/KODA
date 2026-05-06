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
from koda.model_config import (
    ModelSpec,
    has_provider_credentials,
    probe_provider,
)
from koda.session import SessionTree
from koda.session_panel import ConfirmDeleteScreen, SessionPanel
from koda.tui.commands import dispatch as dispatch_command
from koda.tui.stream import run_turn
from koda.tui.theme import DEFAULT_THEME, THEMES, get as get_theme, to_textual_theme
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


def _default_adapter_factory(model: str, thread_id: str) -> KodaAgent:
    """Default adapter factory — used by /model when no custom factory
    was provided at app construction. Builds KODA's built-in deep adapter.
    """
    from koda.adapters.deep import create_deep_adapter

    return create_deep_adapter(model=model, thread_id=thread_id)


# File paths that count as agent memory — editing one triggers the
# "Memory updated" notice so the user knows to /reload-memory for the
# change to take effect this session.
_MEMORY_FILES = {"/AGENTS.md", "AGENTS.md"}


def _is_memory_file_edit(widget: "ToolCallMessage") -> bool:
    """True if the tool call writes to an agent-memory file."""
    name = (widget._tool_name or "").lower()
    if name not in ("edit_file", "write_file"):
        return False
    args = widget._args or {}
    path = args.get("file_path") or args.get("path") or ""
    if not isinstance(path, str):
        return False
    return path in _MEMORY_FILES or path.endswith("/AGENTS.md")


class KodaApp(App):
    """KODA — your AI companion in the terminal."""

    TITLE = "KODA"
    CSS_PATH = "app.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "copy_or_interrupt", "Copy/Interrupt", show=False, priority=True),
        Binding("ctrl+d", "quit_app", "Quit", show=False, priority=True),
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
        adapter_factory: Any | None = None,
    ) -> None:
        super().__init__()
        self._adapter: KodaAgent | None = adapter
        if adapter is None and agent is not None:
            from koda.adapters.langgraph import LangGraphAdapter

            self._adapter = LangGraphAdapter(graph=agent, model=model, thread_id=thread_id)
        self._model = model or (self._adapter.model_name() if self._adapter else "")
        self._auto_approve = auto_approve
        self._koda_thread_id = thread_id or uuid.uuid4().hex
        # Factory used by /model to rebuild the adapter for a new model.
        # Signature: factory(model: str, thread_id: str) -> KodaAgent
        # Defaults to the built-in deep adapter — overridable so that
        # custom backends (e.g. --agent examples.deepagents_backend.build)
        # keep their wiring across /model switches.
        self._adapter_factory = adapter_factory

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
        # Once the user sends a message the banner collapses permanently.
        # Guards `_set_banner_compact(False)` from restoring the tall banner.
        self._chat_started: bool = False

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
        from textual.widgets import Static

        with Vertical(id="app-root"):
            with Horizontal(id="main-row"):
                with Vertical(id="sidebar-host"):
                    yield SessionPanel(
                        sessions_dir=self._sessions_dir(),
                        current_session_id=self._koda_session.session_id,
                        id="session-panel",
                    )
                with Vertical(id="chat-area"):
                    yield KodaBanner(thread_id=self._koda_thread_id)
                    yield VerticalScroll(id="messages")
                    yield SuggestionPopup(id="suggestions")
                    yield Static("", id="last-user-preview", classes="last-user-preview")
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

        # Register all KODA palettes with Textual's theme registry so
        # /theme <name> switches live via self.theme = name.
        for theme_name in THEMES:
            try:
                self.register_theme(to_textual_theme(theme_name))
            except Exception:
                pass  # theme already registered, non-fatal
        self.apply_theme(DEFAULT_THEME)

        parsed = ModelSpec.try_parse(self._model)
        if parsed:
            self._status_bar.set_model(parsed.provider, parsed.model)
        else:
            self._status_bar.set_model("", self._model)

        if self._banner:
            self._banner.set_connected()
        self._chat_input.focus()

        # Note: the model-discovery cache is already warmed in
        # ``koda/__main__.py`` before app boot, so no second warm here.

        # Build the real adapter in a worker thread so the TUI is
        # interactive immediately. Heavy imports (langgraph, langchain,
        # deepagents) and graph compilation can take 5–8 s on a cold start.
        if self._adapter is None and self._adapter_factory is not None:
            asyncio.create_task(self._bootstrap_adapter())

    async def _bootstrap_adapter(self) -> None:
        """Build the initial adapter without blocking the UI thread."""
        loading = AppMessage(f"Loading agent… ({self._model})")
        await self.mount_message(loading)
        try:
            self._adapter = await asyncio.to_thread(
                self._adapter_factory, self._model, self._koda_thread_id
            )
        except Exception as e:
            _log.exception("Adapter bootstrap failed")
            try:
                await loading.remove()
            except Exception:
                pass
            await self.mount_message(
                ErrorMessage(f"Failed to load agent: {e}")
            )
            return
        try:
            await loading.remove()
        except Exception:
            pass
        await self.mount_message(AppMessage(f"Agent ready ({self._model})"))

    # ── Theme ────────────────────────────────────────────────────────

    def apply_theme(self, name: str) -> None:
        """Swap the live theme. Uses Textual's built-in theme registry —
        each palette in ``koda.tui.theme.THEMES`` was registered in
        ``on_mount``.
        """
        if name not in THEMES:
            _log.debug("apply_theme(%s) — unknown, ignored", name)
            return
        try:
            self.theme = name
        except Exception as e:
            _log.warning("apply_theme(%s) failed: %s", name, e)

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
            self._set_banner_compact(False)
            return
        suggestions, replace_range, title = result
        self._chat_input._last_replace_range = replace_range  # type: ignore[attr-defined]
        if self._popup is not None:
            self._popup.set_suggestions(suggestions, title=title)
            self._set_banner_compact(bool(suggestions))

    def _set_banner_compact(self, compact: bool) -> None:
        """Collapse the banner to one line while the popup is open, so the
        full suggestion list (including /tree, /usage) is never clipped off
        the bottom on small terminals.
        """
        if self._banner is None:
            return
        if compact:
            self._banner.add_class("-compact")
        elif not self._chat_started:
            # Only restore the tall banner before the first user turn.
            self._banner.remove_class("-compact")

    async def on_chat_input_suggestions_dismissed(
        self, _event: ChatInput.SuggestionsDismissed
    ) -> None:
        """User dismissed the popup (escape / accept) — restore the banner."""
        self._set_banner_compact(False)

    _PREVIEW_MAX_CHARS = 50

    def _update_last_user_preview(self, message: str) -> None:
        """Show a right-aligned snippet of the last user message above the
        ChatInput — first 50 characters, collapsed whitespace, newlines → ' ⏎ '.
        """
        from textual.widgets import Static

        try:
            preview_widget = self.query_one("#last-user-preview", Static)
        except Exception:
            return
        text = " ".join(message.replace("\n", " ⏎ ").split())
        if len(text) > self._PREVIEW_MAX_CHARS:
            text = text[: self._PREVIEW_MAX_CHARS - 1] + "…"
        preview_widget.update(f"[dim]↳ {text}[/]" if text else "")

    # ── Message mount helper ─────────────────────────────────────────

    async def mount_message(self, widget: BaseMessage) -> None:
        assert self._messages_container is not None
        notice: AppMessage | None = None
        if isinstance(widget, ToolCallMessage):
            self._conv_log.tool_call(widget._tool_name, widget._args)
            if _is_memory_file_edit(widget):
                notice = AppMessage(
                    "Memory updated — active next session (or /reload-memory)."
                )
        elif isinstance(widget, AssistantMessage):
            self._last_assistant_widget = widget
        await self._messages_container.mount(widget)
        if notice is not None:
            await self._messages_container.mount(notice)
        self._messages_container.scroll_end(animate=False)

    # ── Core turn ────────────────────────────────────────────────────

    async def _handle_user_message(self, message: str) -> None:
        self._koda_session.add_message("user", message)
        self._conv_log.user(message)
        # Collapse the banner the first time the user sends anything so the
        # #messages area is never squeezed out of view by the ~12-row ASCII
        # art on short terminal windows.
        self._chat_started = True
        if self._banner is not None:
            self._banner.add_class("-compact")
        await self.mount_message(UserMessage(message))
        self._update_last_user_preview(message)

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
            if self._adapter_factory is not None:
                await self.mount_message(
                    AppMessage("Agent still loading — give it a moment and resend.")
                )
            else:
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
        _log.warning("user shell exec: %s", cmd[:200])
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
            # Pre-flight: probe local HTTP providers (ollama / lmstudio) so
            # the user gets an actionable message instead of an httpx
            # traceback on the first message after the switch.
            reachable, hint = await asyncio.to_thread(probe_provider, parsed.provider)
            if not reachable:
                await self.mount_message(ErrorMessage(hint or f"{parsed.provider} is unreachable"))
                return

        # Immediate feedback — the heavy graph-compilation happens in a thread
        # so the UI never freezes.
        await self.mount_message(AppMessage(f"Switching to {display}…"))

        factory = self._adapter_factory or _default_adapter_factory

        try:
            new_adapter = await asyncio.to_thread(
                factory, display, self._koda_thread_id
            )
        except Exception as e:
            _log.exception("Model switch failed")
            await self.mount_message(ErrorMessage(f"Failed to switch model: {e}"))
            return

        self._adapter = new_adapter
        self._model = display
        if self._status_bar is not None:
            p = parsed.provider if parsed else ""
            m = parsed.model if parsed else display
            self._status_bar.set_model(provider=p, model=m)
        await self.mount_message(AppMessage(f"Switched to {display}"))

    async def reload_memory(self) -> None:
        """Rebuild the current adapter so ``AGENTS.md`` is re-read.

        Matches Claude Code's ``/clear``-to-apply pattern but scoped: only
        the adapter is rebuilt, the conversation UI is preserved. Heavy
        graph compilation runs in a worker thread.
        """
        factory = self._adapter_factory or _default_adapter_factory
        await self.mount_message(AppMessage("Reloading memory…"))
        try:
            new_adapter = await asyncio.to_thread(
                factory, self._model, self._koda_thread_id
            )
        except Exception as e:
            _log.exception("Memory reload failed")
            await self.mount_message(ErrorMessage(f"Failed to reload memory: {e}"))
            return
        self._adapter = new_adapter
        await self.mount_message(AppMessage("Memory reloaded"))

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
        self._chat_started = False
        if self._banner is not None:
            self._banner.remove_class("-compact")
        if self._status_bar is not None:
            self._status_bar.reset_usage()
        self._update_last_user_preview("")
        await self.mount_message(AppMessage("New session started"))
        self._refresh_session_panel()

    @work(exclusive=True)
    async def action_open_tree(self) -> None:
        """Open the session tree, then truncate history to the picked node.

        Two-step modal:
          1. TreeScreen → user picks a target node
          2. CompressionChoiceScreen → compress (summarize) or keep full

        After the user confirms, the active path is moved to ``target_id``,
        ``self._history`` is rebuilt from ``get_messages_for_agent()`` so the
        next agent turn only sees messages up to that point, and the adapter
        is reset so any cross-turn state (e.g. LangGraph's checkpointer) is
        forgotten.
        """
        has_messages = any(
            e.type == "message" for e in self._koda_session.entries.values()
        )
        if not has_messages:
            await self.mount_message(AppMessage("No messages yet — tree is empty"))
            return
        from koda.tree_widget import CompressionChoiceScreen, TreeScreen

        target_id = await self.push_screen_wait(TreeScreen(self._koda_session))
        if not target_id:
            return
        if target_id == self._koda_session.leaf_id:
            await self.mount_message(
                AppMessage("Already on the selected node — nothing to do.")
            )
            return

        target_entry = self._koda_session.entries.get(target_id)
        if target_entry is None:
            return
        preview = (target_entry.content or "").replace("\n", " ").strip()
        if len(preview) > 60:
            preview = preview[:57] + "..."

        mode = await self.push_screen_wait(CompressionChoiceScreen(preview))
        if mode not in ("compress", "keep"):
            return

        await self._jump_to_node(target_id, mode)

    async def _jump_to_node(self, target_id: str, mode: str) -> None:
        """Move the active leaf to ``target_id`` and resync UI + adapter.

        ``mode`` is ``"compress"`` (summarize the path up to target into one
        system message) or ``"keep"`` (send the full path up to target).
        """
        assert self._messages_container is not None
        session = self._koda_session

        session.navigate_to(target_id)

        if mode == "compress":
            messages_to_compress = session.get_messages_for_agent()
            if messages_to_compress:
                await self.mount_message(AppMessage("Summarizing previous memory…"))
                try:
                    from koda.summarizer import summarize_messages

                    summary = await summarize_messages(
                        messages_to_compress, self._model
                    )
                except Exception as e:
                    _log.exception("tree-jump compaction failed")
                    await self.mount_message(
                        ErrorMessage(f"Compaction failed: {e} — keeping full history.")
                    )
                else:
                    session.add_compaction(
                        summary=summary,
                        source_message_count=len(messages_to_compress),
                    )

        # Rebuild the agent-facing history from the (possibly compacted) active path.
        self._history = list(session.get_messages_for_agent())

        # Repaint the messages area to mirror the active path. Walk the path
        # directly so we can render a marker for the compaction node instead
        # of leaving the user staring at the pre-jump UI.
        for child in list(self._messages_container.children):
            await child.remove()
        self._last_assistant_widget = None
        for entry in session.get_active_path():
            if entry.type == "compaction":
                await self.mount_message(
                    AppMessage(
                        f"[Earlier conversation summarized — "
                        f"{entry.metadata.get('source_message_count', 0)} messages compacted]"
                    )
                )
                continue
            if entry.type != "message" or entry.role not in ("user", "assistant"):
                continue
            if entry.role == "user":
                await self.mount_message(UserMessage(entry.content))
                self._update_last_user_preview(entry.content)
            else:
                await self.mount_message(AssistantMessage(entry.content))

        # Forget cross-turn state on the adapter so it doesn't replay the
        # abandoned branch from its own cache (LangGraph checkpointer).
        if self._adapter is not None:
            try:
                new_thread = uuid.uuid4().hex
                self._adapter.reset_history(new_thread)
                self._koda_thread_id = new_thread
            except AttributeError:
                # Older adapters predate reset_history — best-effort, skip.
                pass

        self._conv_log = self._new_conversation_log()
        await self.mount_message(
            AppMessage(
                f"Jumped to selected node ({mode}). "
                f"Next message will see {len(self._history)} prior message(s)."
            )
        )

    @work(exclusive=True)
    async def action_open_model_picker(self) -> None:
        """Open the model picker modal. Runs in a worker for push_screen_wait.

        Called by ``/model`` with no arguments. On selection, switches to the
        picked ``provider:model`` via ``switch_model``.
        """
        from koda.tui.model_picker import ModelPickerScreen

        picked = await self.push_screen_wait(ModelPickerScreen(current=self._model))
        if not picked:
            return
        if picked == self._model:
            await self.mount_message(AppMessage(f"Already on {picked}"))
            return
        await self.switch_model(picked)

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
            # Refresh the list when shown so newly-added sessions appear
            try:
                panel = self.query_one(SessionPanel)
                panel.refresh_sessions()
            except Exception:
                pass

    # ── Sidebar wiring ───────────────────────────────────────────────

    async def on_session_panel_session_selected(
        self, event: SessionPanel.SessionSelected
    ) -> None:
        """User picked an existing session — swap to it."""
        await self._load_session(event.session_info.path)

    async def on_session_panel_new_chat_requested(
        self, _event: SessionPanel.NewChatRequested
    ) -> None:
        """User pressed the + New Chat button."""
        await self.action_clear_session()

    @work(exclusive=True)
    async def on_session_panel_session_delete_requested(
        self, event: SessionPanel.SessionDeleteRequested
    ) -> None:
        """User pressed Del on a session — confirm and delete."""
        info = event.session_info
        confirmed = await self.push_screen_wait(ConfirmDeleteScreen(info))
        if not confirmed:
            return
        try:
            info.path.unlink()
        except OSError as e:
            await self.mount_message(ErrorMessage(f"Failed to delete: {e}"))
            return
        await self.mount_message(AppMessage(f"Deleted session {info.display_time}"))
        # If we just deleted the current session, start a fresh one
        if info.session_id == self._koda_session.session_id:
            await self.action_clear_session()
        else:
            self._refresh_session_panel()

    # ── Session load / helpers ───────────────────────────────────────

    async def _load_session(self, path: Path) -> None:
        """Load a previously-saved session into the UI."""
        assert self._messages_container is not None
        new_tree = SessionTree(path=path)
        self._koda_session = new_tree
        # Rebuild message history for the agent + UI
        for child in list(self._messages_container.children):
            await child.remove()
        self._history.clear()
        self._last_assistant_widget = None
        for entry in new_tree.get_active_path():
            if entry.type != "message" or entry.role not in ("user", "assistant"):
                continue
            self._history.append({"role": entry.role, "content": entry.content})
            if entry.role == "user":
                await self.mount_message(UserMessage(entry.content))
                self._update_last_user_preview(entry.content)
            else:
                await self.mount_message(AssistantMessage(entry.content))
        self._conv_log = self._new_conversation_log()
        self._refresh_session_panel()

    def _refresh_session_panel(self) -> None:
        try:
            panel = self.query_one(SessionPanel)
        except Exception:
            return
        panel.set_active_session(self._koda_session.session_id)

    async def action_copy_or_interrupt(self) -> None:
        """Ctrl+C:
          1. If the user has a mouse selection, copy it to the OS clipboard.
             (Never interrupts in this case — selection copy must be safe.)
          2. Otherwise, if a turn is running, interrupt it.
          3. Otherwise, exit the app.
        """
        selected = self._current_selection_text()
        if selected:
            self._copy_to_os_clipboard(selected)
            try:
                self.screen.clear_selection()
            except Exception:
                pass
            await self.mount_message(
                AppMessage(f"Copied {len(selected)} char{'s' if len(selected) != 1 else ''}")
            )
            return

        if self._turn_task and not self._turn_task.done():
            if self._adapter is not None:
                await self._adapter.interrupt()
            self._turn_task.cancel()
            return
        self.exit()

    def _current_selection_text(self) -> str:
        """Return the currently mouse-selected text across the screen, or ''.

        Textual 1.0 dropped ``Screen.get_selected_text``; the AttributeError
        is caught below so Ctrl+C-with-selection silently degrades to the
        interrupt/exit path. Replace with a 1.0-compatible selection source
        when one becomes available.
        """
        screen = getattr(self, "screen", None)
        if screen is None:
            return ""
        try:
            text = screen.get_selected_text()
        except Exception:
            return ""
        return text or ""

    def _copy_to_os_clipboard(self, text: str) -> None:
        """Copy to the system clipboard.

        Tries pyperclip first (works in most local terminals), then falls
        back to Textual's OSC52 path (works over SSH into supported
        terminals).
        """
        try:
            import pyperclip

            pyperclip.copy(text)
            return
        except Exception:
            pass
        try:
            self.copy_to_clipboard(text)
        except Exception:
            pass

    def action_quit_app(self) -> None:
        """Ctrl+D — always exits."""
        self.exit()
