"""
KODA — AI agent TUI powered by deepagents-cli.

Thin subclass of DeepAgentsApp that adds:
  - KODA ASCII art banner
  - Session tree navigation (/tree, Ctrl+T)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import deepagents_cli
from textual import work
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll

from deepagents_cli.app import DeepAgentsApp
from deepagents_cli.widgets.chat_input import ChatInput
from deepagents_cli.widgets.messages import AssistantMessage, ToolCallMessage
from deepagents_cli.widgets.status import StatusBar

from koda.session_panel import SessionPanel
from koda.widgets import KodaBanner

_log = logging.getLogger("koda")

# Resolve parent's CSS — Textual resolves CSS_PATH relative to the class
# file, so we must give an absolute path to the parent's app.tcss.
_PARENT_CSS = Path(deepagents_cli.__file__).parent / "app.tcss"


class KodaApp(DeepAgentsApp):
    """KODA — your AI companion in the terminal."""

    TITLE = "KODA"
    CSS_PATH = [_PARENT_CSS]

    CSS = """
    #main-layout {
        height: 1fr;
    }

    #chat-area {
        width: 1fr;
        height: 100%;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        b
        for b in DeepAgentsApp.BINDINGS
        if not (isinstance(b, Binding) and b.key in ("ctrl+t", "ctrl+c", "shift+tab"))
    ] + [
        Binding("ctrl+c", "copy_or_interrupt", "Copy/Interrupt", show=False, priority=True),
        Binding("ctrl+t", "open_tree", "Session Tree", show=False),
        Binding("ctrl+y", "yank_last", "Copy last response", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Toggle sidebar", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        from koda.session import SessionTree

        self._koda_session = SessionTree(path=self._new_session_path())
        self._conv_log = self._new_conversation_log()

    @staticmethod
    def _sessions_dir() -> Path:
        """Directory that holds all JSONL session files for the current project."""
        cwd_slug = os.getcwd().replace("\\", "--").replace("/", "--").strip("-")
        d = Path.home() / ".koda" / "sessions" / cwd_slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def _new_session_path(cls) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return cls._sessions_dir() / f"{ts}.jsonl"

    def _new_conversation_log(self):
        from koda.conversation_log import ConversationLog

        project_root = Path(__file__).resolve().parent.parent
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        model = getattr(self, "_model_name", "") or ""
        return ConversationLog(log_dir / f"conversation_{ts}.md", model=model)

    # ── Compact tool output + conversation logging ────────────────────

    # Show 1-line preview (default is 6 lines / 400 chars)
    _TOOL_PREVIEW_LINES = 1
    _TOOL_PREVIEW_CHARS = 80

    async def _mount_message(self, widget) -> None:
        """Mount message — compresses tool output and logs conversation.

        Works with any agent backend; only ToolCallMessage instances are
        patched so assistant text, diffs, and app messages render normally.
        """
        if isinstance(widget, ToolCallMessage):
            widget._PREVIEW_LINES = self._TOOL_PREVIEW_LINES
            widget._PREVIEW_CHARS = self._TOOL_PREVIEW_CHARS
            self._log_tool_call(widget)

        if isinstance(widget, AssistantMessage):
            self._log_assistant_when_done(widget)

        await super()._mount_message(widget)

    def _log_tool_call(self, widget: ToolCallMessage) -> None:
        """Log tool call to conversation file and wrap result callbacks."""
        name = widget._tool_name
        args = widget._args

        self._conv_log.tool_call(name, args)

        orig_success = widget.set_success
        orig_error = widget.set_error

        def _on_success(result: str = "", _orig=orig_success) -> None:
            self._conv_log.tool_result(name, result)
            _orig(result)

        def _on_error(error: str = "", _orig=orig_error) -> None:
            self._conv_log.tool_result(name, error, error=True)
            _orig(error)

        widget.set_success = _on_success
        widget.set_error = _on_error

    def _log_assistant_when_done(self, widget: AssistantMessage) -> None:
        """Wrap stop_stream so assistant text is logged when streaming ends."""
        orig_stop = widget.stop_stream

        async def _on_stop(_orig=orig_stop) -> None:
            await _orig()
            content = getattr(widget, "_content", "") or ""
            if content.strip():
                self._conv_log.assistant(content)

        widget.stop_stream = _on_stop

    # ── Layout: swap WelcomeBanner → KodaBanner ──────────────────────

    def compose(self):
        with Horizontal(id="main-layout"):
            yield SessionPanel(
                sessions_dir=self._sessions_dir(),
                current_session_id=self._koda_session.session_id,
                id="session-panel",
            )
            with Vertical(id="chat-area"):
                with VerticalScroll(id="chat"):
                    yield KodaBanner(
                        thread_id=self._lc_thread_id,
                        mcp_tool_count=self._mcp_tool_count,
                        connecting=self._connecting,
                        resuming=self._resume_thread_intent is not None,
                        local_server=self._server_kwargs is not None,
                        id="welcome-banner",
                    )
                    yield Container(id="messages")
                with Container(id="bottom-app-container"):
                    yield ChatInput(
                        cwd=self._cwd,
                        image_tracker=self._image_tracker,
                        id="input-area",
                    )
        yield StatusBar(cwd=self._cwd, id="status-bar")

    # ── Slash commands: add /tree ────────────────────────────────────

    async def on_mount(self) -> None:
        await super().on_mount()
        self._inject_tree_command()
        self._setup_ollama_models()
        self._patch_file_completion()

    @work(thread=True)
    def _setup_ollama_models(self) -> None:
        """Inject extra providers (Ollama, LM Studio, etc.) into /model."""
        from koda.provider_models import inject_into_registry, refresh_stale

        inject_into_registry()
        refresh_stale()  # Re-fetch any provider whose cache is > 24h

    def _patch_file_completion(self) -> None:
        """Patch @ file completion to include untracked files.

        The upstream FuzzyFileController uses ``git ls-files`` which only
        returns *tracked* files.  In a fresh repo with no commits this
        produces an empty list and ``@`` shows nothing.  We replace
        ``_get_files`` so it calls ``git ls-files --cached --others
        --exclude-standard`` which includes both tracked and untracked
        files (respecting .gitignore).
        """
        import shutil
        import subprocess

        ci = self._chat_input
        if not ci or not getattr(ci, "_file_controller", None):
            return

        fc = ci._file_controller
        project_root = fc._project_root

        def _get_files_with_untracked() -> list[str]:
            git = shutil.which("git")
            if git:
                try:
                    result = subprocess.run(
                        [git, "ls-files", "--cached", "--others", "--exclude-standard"],
                        cwd=project_root,
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    if result.returncode == 0:
                        files = [f for f in result.stdout.strip().split("\n") if f]
                        if files:
                            return files
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass
            # Fallback: glob up to 4 levels deep
            files = []
            for pattern in ["*", "*/*", "*/*/*", "*/*/*/*"]:
                for p in project_root.glob(pattern):
                    if p.is_file() and not any(
                        part.startswith(".") for part in p.relative_to(project_root).parts
                    ):
                        files.append(p.relative_to(project_root).as_posix())
                    if len(files) >= 1000:
                        break
                if len(files) >= 1000:
                    break
            return files

        fc._get_files = _get_files_with_untracked
        fc._file_cache = None  # Force re-scan with the new function

    async def _check_optional_tools_background(self) -> None:
        pass

    async def _discover_skills(self) -> None:
        """Run parent skill discovery, then re-inject /tree."""
        await super()._discover_skills()
        self._inject_tree_command()

    _HIDDEN_COMMANDS = {
        "/trace", "/auto-update", "/update",
        "/threads", "/remember", "/notifications",
        "/changelog", "/docs", "/feedback",
        "/skill-creator",
    }

    def _inject_tree_command(self) -> None:
        """Add /tree and filter out unwanted commands."""
        if not self._chat_input:
            return
        from deepagents_cli.command_registry import SLASH_COMMANDS, build_skill_commands

        skill_cmds = build_skill_commands(self._discovered_skills) if self._discovered_skills else []
        merged = [
            c for c in list(SLASH_COMMANDS) + skill_cmds
            if c[0] not in self._HIDDEN_COMMANDS
        ] + [
            ("/tree", "Navigate session tree (branch/compress history)", "branches history"),
            ("/copy", "Copy last response to clipboard (or Ctrl+Y)", "clipboard yank"),
        ]
        self._chat_input.update_slash_commands(merged)

    async def _handle_command(self, command: str) -> None:
        cmd = command.lower().strip()
        if cmd == "/tree":
            self.action_open_tree()
            return
        if cmd == "/copy":
            self._copy_last_response()
            return
        if cmd == "/help":
            await self._show_koda_help(command)
            return
        if cmd in self._HIDDEN_COMMANDS:
            return
        await super()._handle_command(command)

    async def _show_koda_help(self, command: str) -> None:
        """Show KODA-specific help with only the commands that exist."""
        from deepagents_cli.widgets.messages import AppMessage, UserMessage
        from deepagents_cli.config import newline_shortcut
        from textual.content import Content

        await self._mount_message(UserMessage(command))
        help_body = (
            "Commands:\n"
            "  /clear          Clear chat and start new thread\n"
            "  /model          Switch or configure model\n"
            "  /mcp            Show active MCP servers and tools\n"
            "  /tree           Navigate session tree (Ctrl+T)\n"
            "  /copy           Copy last response (Ctrl+Y)\n"
            "  /offload        Free up context window space\n"
            "  /editor         Open prompt in external editor\n"
            "  /theme          Switch color theme\n"
            "  /tokens         Show token usage\n"
            "  /reload         Reload config from .env\n"
            "  /version        Show version\n"
            "  /quit           Exit app\n\n"
            "Interactive Features:\n"
            "  Enter           Submit your message\n"
            f"  {newline_shortcut():<15} Insert newline\n"
            "  Ctrl+X          Open prompt in external editor\n"
            "  Ctrl+B          Toggle session sidebar\n"
            "  Ctrl+C          Copy selected text / interrupt\n"
            "  Ctrl+Y          Copy last response\n"
            "  Ctrl+T          Open session tree\n"
            "  @filename       Auto-complete files and inject content\n"
            "  /command        Slash commands\n"
            "  !command        Run shell commands directly\n"
        )
        help_text = Content.styled(help_body, "dim italic")
        await self._mount_message(AppMessage(help_text))

    # ── Ctrl+C: copy when text is selected, interrupt otherwise ────────

    def action_copy_or_interrupt(self) -> None:
        """Ctrl+C — copy selected text, or interrupt/quit if nothing selected."""
        if self._try_copy_selection():
            return
        # No selection → fall through to parent interrupt logic
        self.action_quit_or_interrupt()

    def _try_copy_selection(self) -> bool:
        """Copy any widget's text selection to clipboard. Returns True if copied."""
        from deepagents_cli.clipboard import copy_selection_to_clipboard

        # Check if any widget has an active selection
        for widget in self.query("*"):
            sel = getattr(widget, "text_selection", None)
            if sel and getattr(sel, "end", None) is not None:
                copy_selection_to_clipboard(self)
                return True
        return False

    # ── Copy helpers ─────────────────────────────────────────────────

    def action_yank_last(self) -> None:
        """Copy the last assistant response to the clipboard (Ctrl+Y)."""
        self._copy_last_response()

    def _copy_last_response(self) -> None:
        """Find the last assistant message and copy it to clipboard."""
        from deepagents_cli.widgets.message_store import MessageType

        for msg in reversed(self._message_store.get_all_messages()):
            if msg.type == MessageType.ASSISTANT and msg.content:
                self._copy_text(msg.content)
                return
        self.notify("No assistant message to copy", severity="warning", timeout=2)

    def _copy_text(self, text: str) -> None:
        """Copy text to clipboard using best available method."""
        copy_methods = [self.copy_to_clipboard]
        try:
            import pyperclip
            copy_methods.insert(0, pyperclip.copy)
        except ImportError:
            pass
        for fn in copy_methods:
            try:
                fn(text)
                preview = text[:40].replace("\n", " ")
                if len(text) > 40:
                    preview += "..."
                self.notify(f'Copied: "{preview}"', timeout=2)
                return
            except (OSError, RuntimeError, TypeError):
                continue
        self.notify("No clipboard method available", severity="warning", timeout=3)

    # ── Model switching (recreate local agent) ─────────────────────────

    async def _switch_model(
        self, model_spec: str, *, extra_kwargs: dict | None = None
    ) -> None:
        """Switch model by recreating the deep agent with the new model.

        The parent rejects local agents — we override to rebuild the graph.
        """
        from deepagents_cli.widgets.messages import AppMessage, ErrorMessage
        from deepagents_cli.model_config import ModelSpec, has_provider_credentials

        parsed = ModelSpec.try_parse(model_spec)
        display = model_spec
        if parsed:
            provider = parsed.provider
            model_name = parsed.model
            display = f"{provider}:{model_name}" if provider else model_name
        else:
            provider = None

        # Check credentials
        if provider:
            creds = has_provider_credentials(provider)
            if creds is False:
                await self._mount_message(
                    ErrorMessage(f"Missing credentials for {provider}")
                )
                return

        try:
            from koda.agents.deep import create_koda_agent

            new_agent = create_koda_agent(model=display)
            self._agent = new_agent
            _log.info("Model switched to %s — agent recreated", display)
            await self._mount_message(AppMessage(f"Switched to {display}"))

            if self._status_bar:
                p = parsed.provider if parsed else ""
                m = parsed.model if parsed else display
                self._status_bar.set_model(provider=p or "", model=m or "")
        except Exception as exc:
            _log.exception("Model switch failed: %s", exc)
            await self._mount_message(
                ErrorMessage(f"Failed to switch model: {exc}")
            )

    # ── Record messages to session tree ──────────────────────────────

    async def _handle_user_message(self, message: str) -> None:
        _log.info(
            ">>> user message: %r | agent=%s adapter=%s session=%s",
            message[:80],
            bool(self._agent),
            bool(self._ui_adapter),
            bool(self._session_state),
        )
        self._koda_session.add_message("user", message)
        self._conv_log.user(message)
        self._update_input_title(message)

        # Refresh sidebar so the active session preview updates
        try:
            panel = self.query_one("#session-panel", SessionPanel)
            panel.refresh_sessions()
        except Exception:
            pass

        try:
            await super()._handle_user_message(message)
            _log.info(">>> super()._handle_user_message returned OK")
        except Exception:
            _log.exception(">>> super()._handle_user_message FAILED")

    def _update_input_title(self, message: str) -> None:
        """Show first 70 chars of the user's last message on the input border."""
        if not self._chat_input:
            return
        clean = message.replace("\n", " ").strip()
        if len(clean) > 70:
            clean = clean[:67] + "..."
        self._chat_input.border_title = clean

    # ── Tree navigation ──────────────────────────────────────────────

    @work
    async def action_open_tree(self) -> None:
        """Open the KODA session tree modal."""
        from koda.tree_widget import TreeScreen, CompressionChoiceScreen

        if self._koda_session.message_count() == 0:
            from deepagents_cli.widgets.messages import AppMessage

            await self._mount_message(
                AppMessage("No messages yet — send a message first.")
            )
            return

        old_leaf_id = self._koda_session.leaf_id

        selected_id = await self.push_screen_wait(
            TreeScreen(self._koda_session)
        )
        if selected_id is None:
            return

        target = self._koda_session.entries.get(selected_id)
        preview = ""
        if target:
            preview = target.content[:60]
            if len(target.content) > 60:
                preview += "..."

        choice = await self.push_screen_wait(
            CompressionChoiceScreen(preview)
        )
        if choice is None:
            return

        # Compress abandoned branch if requested
        if choice == "compress" and old_leaf_id and old_leaf_id != selected_id:
            abandoned = self._koda_session.get_abandoned_path(
                old_leaf_id, selected_id
            )
            msgs = [
                {"role": e.role, "content": e.content}
                for e in abandoned
                if e.role in ("user", "assistant")
            ]
            if msgs:
                try:
                    from koda.summarizer import summarize_messages

                    summary = await summarize_messages(
                        msgs, self._get_summary_model()
                    )
                    self._koda_session.add_branch_summary(summary, old_leaf_id)
                except Exception:
                    # Fallback: text-only summary
                    items = [
                        f"- {m['role']}: {m['content'][:80]}"
                        for m in msgs[:10]
                    ]
                    self._koda_session.add_branch_summary(
                        "\n".join(items), old_leaf_id
                    )

        self._koda_session.navigate_to(selected_id)

        from deepagents_cli.widgets.messages import AppMessage

        await self._mount_message(
            AppMessage(
                f"Navigated to: {preview}\nMemory mode: {choice}"
            )
        )

    # ── Sidebar toggle + session switching ─────────────────────────

    def action_toggle_sidebar(self) -> None:
        """Ctrl+B — show / hide the session sidebar."""
        try:
            panel = self.query_one("#session-panel", SessionPanel)
            panel.toggle_class("-hidden")
        except Exception:
            pass

    async def on_session_panel_session_selected(
        self, event: SessionPanel.SessionSelected
    ) -> None:
        """Switch to the selected session."""
        info = event.session_info
        if info.session_id == self._koda_session.session_id:
            return  # Already on this session

        from koda.session import SessionTree
        from deepagents_cli.widgets.messages import (
            AppMessage,
            AssistantMessage,
            UserMessage,
        )

        # Load the selected session tree from disk
        new_session = SessionTree(path=info.path)

        # Clear current chat UI
        await self._clear_messages()

        # Replace session
        self._koda_session = new_session
        self._conv_log = self._new_conversation_log()

        # Re-render historical messages from the session's active path
        for entry in new_session.get_active_path():
            if entry.type != "message":
                continue
            if entry.role == "user":
                await self._mount_message(UserMessage(entry.content))
            elif entry.role == "assistant":
                msg = AssistantMessage(content=entry.content)
                await self._mount_message(msg)
                await msg.write_initial_content()
                await msg.stop_stream()

        # Update sidebar highlight
        try:
            panel = self.query_one("#session-panel", SessionPanel)
            panel.set_active_session(new_session.session_id)
        except Exception:
            pass

        await self._mount_message(
            AppMessage(f"Resumed session from {info.display_time}")
        )

    async def on_session_panel_new_chat_requested(
        self, event: SessionPanel.NewChatRequested
    ) -> None:
        """Start a brand-new session."""
        from koda.session import SessionTree
        from deepagents_cli.widgets.messages import AppMessage

        new_session = SessionTree(path=self._new_session_path())

        await self._clear_messages()

        self._koda_session = new_session
        self._conv_log = self._new_conversation_log()

        await self._mount_message(AppMessage("New session started"))

        try:
            panel = self.query_one("#session-panel", SessionPanel)
            panel.set_active_session(new_session.session_id)
            panel.refresh_sessions()
        except Exception:
            pass

    @work
    async def on_session_panel_session_delete_requested(
        self, event: SessionPanel.SessionDeleteRequested
    ) -> None:
        """Show confirmation dialog, then delete the session file."""
        from koda.session_panel import ConfirmDeleteScreen
        from deepagents_cli.widgets.messages import AppMessage

        info = event.session_info

        # Don't delete the currently active session
        if info.session_id == self._koda_session.session_id:
            self.notify(
                "Cannot delete the active session", severity="warning", timeout=3
            )
            return

        confirmed = await self.push_screen_wait(ConfirmDeleteScreen(info))
        if not confirmed:
            return

        # Remove the JSONL file
        try:
            info.path.unlink()
            _log.info("Deleted session file %s", info.path)
        except OSError as exc:
            _log.warning("Failed to delete %s: %s", info.path, exc)
            self.notify(f"Delete failed: {exc}", severity="error", timeout=3)
            return

        # Refresh sidebar
        try:
            panel = self.query_one("#session-panel", SessionPanel)
            panel.refresh_sessions()
        except Exception:
            pass

        await self._mount_message(
            AppMessage(f"Deleted session from {info.display_time}")
        )

    def _get_summary_model(self) -> str:
        """Get a model string for the summarizer."""
        try:
            from deepagents_cli.config import settings

            provider = getattr(settings, "model_provider", None) or "openai"
            name = getattr(settings, "model_name", None) or "gpt-4o"
            return f"{provider}:{name}"
        except Exception:
            return "openai:gpt-4o"
