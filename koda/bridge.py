"""
koda.bridge — NDJSON-over-stdio bridge exposing the KODA agent to an external UI.

The TypeScript + Ink frontend (``koda-ink/``) spawns this module as a
subprocess and talks to it over stdin/stdout using newline-delimited JSON
(one JSON object per line). This reuses the *entire* existing Python backend
— adapters, the LangGraph/deepagents agent, model providers, permissions,
sessions — with no changes to any of it. The bridge is a thin translation
layer between the ``KodaAgent`` event stream (``koda/agent_api.py``) and JSON.

This is the same event schema KODA already documents for its HTTP/SSE
protocol (``thinking_delta`` / ``text_delta`` / ``tool_start`` /
``tool_result`` / ``done``); we simply carry it over stdio instead of SSE so
a local single-user CLI needs no HTTP port, CORS, or server lifecycle.

Run standalone (for debugging):
    python -m koda.bridge --model anthropic:claude-sonnet-4-6 --agent coding_agent

Client → bridge commands (one JSON object per stdin line)::
    {"type": "user", "text": "..."}          # run a turn
    {"type": "interrupt"}                     # cancel the running turn
    {"type": "decisions", "outcomes": [...]}  # answer a permission_request
                                              #   outcomes: "allow"|"always"|"deny"
    {"type": "set_mode", "mode": "default"}   # default | edits | plan
    {"type": "switch_model", "model": "..."}
    {"type": "compact"}
    {"type": "clear"}
    {"type": "tree", "node": "..."?}          # render the session tree / branch
    {"type": "resume", "session_id": "..."?}  # list old sessions / resume one
    {"type": "describe"}
    {"type": "quit"}

Bridge → client events (one JSON object per stdout line)::
    {"type": "ready", "model": ..., "backend": ..., "cwd": ..., "tools": [...],
     "supports_thinking": bool, "supports_vision": bool, "mode": "default"}
    {"type": "text_delta", "content": "..."}
    {"type": "thinking_delta", "content": "..."}
    {"type": "tool_start", "tool_id": ..., "name": ..., "arguments": {...}}
    {"type": "tool_result", "tool_id": ..., "output": "...", "is_error": bool}
    {"type": "todos", "todos": [...]}                 # write_todos snapshots
    {"type": "usage", "input_tokens": ..., ...}
    {"type": "permission_request", "items": [{"tool_name", "args",
                                              "allowed_decisions", "description"}]}
    {"type": "done", "usage": {...}|null}
    {"type": "turn_end", "reply": "..."}              # turn fully finished
    {"type": "model_changed", "model": ...}
    {"type": "info", "message": "..."}
    {"type": "error", "message": "..."}
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from koda.agent_api import (
    Done,
    PermissionRequest,
    TextDelta,
    ThinkingDelta,
    ToolResult,
    ToolStart,
    Usage,
    describe_agent,
)
from koda import session_store
from koda.subagent_tasks import BackgroundTaskRegistry
from koda.tools import permissions as _perms
from koda.modes import Mode

_log = logging.getLogger("koda.bridge")

# Cap on inlined @file attachment size, so a huge file can't blow the context.
_MAX_ATTACH_BYTES = 200_000


# ── stdout: emit one JSON event per line ────────────────────────────────


def emit(obj: dict[str, Any]) -> None:
    """Write a single JSON event line to stdout and flush.

    Everything runs on one asyncio loop/thread, so a plain synchronous
    write is safe and ordered. We flush every line so the Node client sees
    events the instant they're produced (streaming, not buffered).
    """
    try:
        sys.stdout.write(json.dumps(obj, default=str) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # Client went away — nothing we can do; let the read loop notice EOF.
        pass


def _event_to_json(ev: Any) -> dict[str, Any] | None:
    """Translate a KodaAgent ``AgentEvent`` to the wire JSON shape."""
    if isinstance(ev, TextDelta):
        return {"type": "text_delta", "content": ev.content}
    if isinstance(ev, ThinkingDelta):
        return {"type": "thinking_delta", "content": ev.content}
    if isinstance(ev, ToolStart):
        return {
            "type": "tool_start",
            "tool_id": ev.tool_id,
            "name": ev.name,
            "arguments": ev.arguments,
        }
    if isinstance(ev, ToolResult):
        return {
            "type": "tool_result",
            "tool_id": ev.tool_id,
            "output": ev.output,
            "is_error": ev.is_error,
        }
    if isinstance(ev, Usage):
        return {
            "type": "usage",
            "input_tokens": ev.input_tokens,
            "output_tokens": ev.output_tokens,
            "cache_read_tokens": ev.cache_read_tokens,
            "cache_write_tokens": ev.cache_write_tokens,
        }
    if isinstance(ev, Done):
        u = ev.usage
        return {
            "type": "done",
            "usage": (
                {
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read_tokens": u.cache_read_tokens,
                    "cache_write_tokens": u.cache_write_tokens,
                }
                if u is not None
                else None
            ),
        }
    if isinstance(ev, PermissionRequest):
        return {
            "type": "permission_request",
            "items": [_perm_item_json(it) for it in ev.items],
        }
    return None


def _perm_item_json(it: Any) -> dict[str, Any]:
    """Serialize one permission item, annotating the REAL on-disk target.

    The agent's file tools jail absolute paths to the workspace root
    (``/tmp/x`` actually lands at ``<cwd>/tmp/x`` — see koda/tools/fs.py),
    so the raw ``file_path`` argument alone misleads the user about what
    they're approving. ``resolved_path`` carries the true destination.
    """
    d: dict[str, Any] = {
        "tool_name": getattr(it, "tool_name", ""),
        "args": getattr(it, "args", {}) or {},
        "allowed_decisions": list(getattr(it, "allowed_decisions", ("approve", "reject"))),
        "description": getattr(it, "description", ""),
    }
    fp = d["args"].get("file_path")
    if isinstance(fp, str) and fp.startswith("/"):
        d["resolved_path"] = os.path.join(os.getcwd(), fp.lstrip("/"))
    return d


# ── @file attachment expansion ──────────────────────────────────────────

_AT_TOKEN_RE = re.compile(r"(?:^|\s)@([^\s@]+)")


def expand_at_files(text: str) -> str:
    """Inline the contents of any ``@path`` references into the message.

    Mirrors what a user means by "attach this file": the referenced file's
    contents are appended in a tagged block so the model sees them directly
    (in addition to the agent's own read tools). The visible ``@path`` stays
    in the text; only the copy sent to the agent is expanded. Non-existent
    or oversized paths are left as plain text.
    """
    seen: set[str] = set()
    blocks: list[str] = []
    for m in _AT_TOKEN_RE.finditer(text):
        tok = m.group(1)
        if tok in seen:
            continue
        seen.add(tok)
        p = Path(tok).expanduser()
        try:
            if not p.is_file():
                continue
            raw = p.read_bytes()[:_MAX_ATTACH_BYTES]
            content = raw.decode("utf-8", errors="replace")
        except Exception:
            continue
        blocks.append(f'\n\n<attached-file path="{tok}">\n{content}\n</attached-file>')
    return text + "".join(blocks)


# ── skills authoring (/skill command) ───────────────────────────────────
# Skills are read by deepagents' SkillsMiddleware (wired via skills=["/skills/"]
# in coding_agent/agent.py). The /skill command lets the CONFIGURED model author
# a spec-compliant SKILL.md and saves it into coding_agent/skills/<name>/, which
# the middleware then surfaces to the agent (progressive disclosure).

SKILL_AUTHOR_PROMPT = """You are authoring an Agent Skill file (SKILL.md) that follows Anthropic's Agent Skills specification.

Write ONLY the complete SKILL.md content — no preamble, no explanation, and no surrounding code fences.

It MUST begin with YAML frontmatter delimited by --- lines:
---
name: <lowercase-hyphenated identifier, 1-64 chars, only a-z 0-9 and single hyphens>
description: <one or two sentences saying WHAT the skill does AND WHEN to use it; include trigger keywords>
---

Then a markdown body with these sections:
# <Skill Title>
## When to Use   — bullet list of situations that should trigger this skill
## Instructions  — a concrete, numbered, step-by-step workflow the agent should follow
## Notes         — optional pitfalls, tips, or a short example

Make it specific and actionable — this is instructions for an AI coding agent, not marketing copy.

The skill to author:
{brief}
"""


def _slug(s: str) -> str:
    """Normalize an arbitrary string into a spec-valid skill name."""
    s = re.sub(r"[^a-z0-9]+", "-", s.strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:64]


def _clean_skill_md(text: str) -> str:
    """Strip a wrapping code fence the model may have added; ensure trailing NL."""
    t = text.strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```$", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    return t + "\n"


def _skill_frontmatter(content: str) -> dict[str, Any]:
    """Parse the YAML frontmatter mapping from SKILL.md content (or {})."""
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml

        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ── the bridge ──────────────────────────────────────────────────────────


class Bridge:
    def __init__(self, *, factory, model: str, thread_id: str | None) -> None:
        self._factory = factory
        self._model = model
        # Claude-Code-style session store: branchable JSONL at
        # ~/.koda/projects/<project-slug>/<session-id>.jsonl — the filename IS
        # the session id, and /resume appends to the same file.
        self._session = session_store.new_session()
        self._thread_id = thread_id or self._session.session_id or uuid.uuid4().hex
        self._adapter: Any = None
        self._history: list[dict[str, Any]] = []
        self._turn_task: asyncio.Task | None = None
        self._pending_perm_items: list[Any] | None = None
        # Background-subagent registry + notifications to inject next turn.
        self._registry: BackgroundTaskRegistry | None = None
        self._pending_task_notes: list[str] = []
        # Ordered work (turns + model/session ops) runs one-at-a-time here.
        self._cmd_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Set when the client sends "interrupt"; _run_turn confirms with an
        # info event once the turn actually unwinds.
        self._interrupted = False
        # Blocking hooks (the agent's ask_user tool, and the deep adapter's
        # sync permission gate) run on LangGraph worker threads; each parks on
        # a Future that the stdin reader resolves when the client answers.
        self._ask_future: concurrent.futures.Future | None = None
        self._hook_future: concurrent.futures.Future | None = None

    # ── lifecycle ────────────────────────────────────────────────────

    async def build(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._adapter = self._factory(model=self._model, thread_id=self._thread_id)
        # Warm the graph so the first turn doesn't pay the build cost mid-stream.
        ensure = getattr(self._adapter, "_ensure_graph", None)
        if ensure is not None:
            try:
                await ensure()
            except Exception:
                _log.debug("graph warm failed (will retry on first turn)", exc_info=True)
        # Background-subagent registry, bound to this loop so the agent's
        # start_async_task/… tools (which run on worker threads) can drive it.
        self._registry = BackgroundTaskRegistry(
            factory=self._factory, model=self._model, on_update=self._on_task_update
        )
        try:
            from koda import subagent_tools

            subagent_tools.bind(self._registry, self._loop)
        except Exception:
            _log.debug("subagent tools bind failed", exc_info=True)
        # Route the agent's ask_user tool to the client instead of the headless
        # sentinel, and give sync-gated backends (the `deep` adapter's plain
        # @tool permission check) a real prompt path over the wire.
        try:
            from koda.tools import ask_user as _ask

            _ask.set_hook(self._ask_user_hook)
        except Exception:
            _log.debug("ask_user hook install failed", exc_info=True)
        try:
            _perms.set_prompt_hook(self._sync_permission_hook)
        except Exception:
            _log.debug("permission hook install failed", exc_info=True)
        self._emit_ready()
        # Model list for the client's /model completion — discovery can hit the
        # network (Ollama /api/tags etc.), so it must not delay readiness.
        asyncio.create_task(self._emit_models())

    # ── blocking hooks (run on LangGraph worker threads) ──────────────

    def _ask_user_hook(self, question: str, options: list[str]) -> str:
        """The agent's ``ask_user`` tool → an ``ask_user`` event → the client's
        ``ask_answer`` command. Blocks only the tool's worker thread."""
        if self._loop is None:
            return "[ask_user unavailable] no event loop."
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._ask_future = fut
        self._loop.call_soon_threadsafe(
            emit, {"type": "ask_user", "question": question, "options": list(options or [])}
        )
        try:
            return str(fut.result(timeout=600))
        except concurrent.futures.TimeoutError:
            return "[ask_user timeout] The user didn't answer; proceed with your best guess."
        finally:
            self._ask_future = None

    def _sync_permission_hook(self, tool_name: str, args: dict) -> bool:
        """Blocking permission prompt for sync ``@tool`` backends (deep adapter).

        Without this, ``koda.tools.permissions.check`` has no hook under the
        bridge and silently ALLOWS every mutating tool. Emits the same
        ``permission_request`` event the interrupt flow uses; the client's
        ``decisions`` command resolves it (see ``_apply_decisions``).
        """
        if self._loop is None:
            return False
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._hook_future = fut
        item = {
            "tool_name": tool_name,
            "args": args or {},
            "allowed_decisions": ["approve", "reject"],
            "description": "",
        }
        fp = (args or {}).get("file_path")
        if isinstance(fp, str) and fp.startswith("/"):
            item["resolved_path"] = os.path.join(os.getcwd(), fp.lstrip("/"))
        self._loop.call_soon_threadsafe(
            emit, {"type": "permission_request", "items": [item]}
        )
        try:
            outcome = str(fut.result(timeout=600))
        except concurrent.futures.TimeoutError:
            return False
        finally:
            self._hook_future = None
        if outcome == "always":
            _perms.allow_tool(tool_name)
            return True
        return outcome == "allow"

    async def _emit_models(self) -> None:
        """Send the discovered ``provider:model`` list for /model completion."""
        try:
            from koda.model_config import get_available_models

            available = await asyncio.to_thread(get_available_models)
            models = [
                f"{provider}:{m}"
                for provider, ms in sorted((available or {}).items())
                for m in ms
            ]
            if models:
                emit({"type": "models", "models": models})
        except Exception:
            _log.debug("model discovery failed", exc_info=True)

    def _task_json(self, task: Any) -> dict[str, Any]:
        """Wire shape for one task: the compact summary plus a peek INSIDE the
        agent — its recent tool trail and the tail of its output — so the
        dashboard can show what each subagent is actually doing."""
        d = task.summary.to_json()
        d["recent_tools"] = list(task.tool_log[-10:])
        d["preview"] = (task.final_text or "")[-400:]
        # Full-ish scrollable record of what the agent did (dashboard detail view).
        d["activity"] = list(task.activity[-300:])
        return d

    def _on_task_update(self, task: Any, done: bool) -> None:
        """Registry callback (runs on the loop): stream task state to the UI and,
        when a task finishes, queue a notification for the agent's next turn."""
        emit({"type": "task_update", "task": self._task_json(task)})
        if task.summary.awaiting_permission and task._pending_perm is not None:
            emit(
                {
                    "type": "task_permission",
                    "task_id": task.id,
                    "items": [
                        {
                            "tool_name": it.tool_name,
                            "args": it.args,
                            "allowed_decisions": list(it.allowed_decisions),
                            "description": it.description,
                        }
                        for it in task._pending_perm.items
                    ],
                }
            )
        if done:
            st = task.summary.state
            emit(
                {
                    "type": "task_done",
                    "task": self._task_json(task),
                    "result": (task.final_text or "")[:2000],
                }
            )
            if st == "success":
                body = f"Result: {task.final_text[:600]}"
            elif st == "error":
                body = f"(failed: {task.summary.error})"
            else:
                body = "(cancelled)"
            self._pending_task_notes.append(
                f"<task-notification>Background {task.subagent_type} task {task.id} "
                f"{st}. Brief: {task.description[:80]}. {body} "
                f"Use check_async_task('{task.id}') for the full output.</task-notification>"
            )

    def _emit_ready(self) -> None:
        desc = describe_agent(self._adapter)
        emit(
            {
                "type": "ready",
                "model": self._model,
                "backend": desc.backend,
                "cwd": os.getcwd(),
                "mode": _perms.current_mode().value,
                "supports_thinking": desc.supports_thinking,
                "supports_vision": desc.supports_vision,
                "tools": [
                    {"name": t.name, "description": t.description} for t in desc.tools
                ],
            }
        )

    async def aclose(self) -> None:
        if self._registry is not None:
            try:
                await self._registry.aclose()
            except Exception:
                pass
            try:
                from koda import subagent_tools

                subagent_tools.unbind()
            except Exception:
                pass
        try:
            from koda.tools import ask_user as _ask

            _ask.set_hook(None)
        except Exception:
            pass
        try:
            _perms.set_prompt_hook(None)
        except Exception:
            pass
        aclose = getattr(self._adapter, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass

    # ── stdin reader (immediate control) ─────────────────────────────

    async def read_stdin(self) -> None:
        """Read client commands. Immediate ones (interrupt/decisions/mode)
        act now; ordered ones queue for the worker."""
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while not self._stop.is_set():
            line = await reader.readline()
            if not line:  # EOF — client closed stdin
                break
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except json.JSONDecodeError:
                emit({"type": "error", "message": f"bad json: {line[:120]!r}"})
                continue
            await self._dispatch(cmd)
        self._stop.set()

    async def _dispatch(self, cmd: dict[str, Any]) -> None:
        ctype = cmd.get("type")

        # ── immediate (act during a running turn) ────────────────────
        if ctype == "interrupt":
            self._interrupted = True
            if self._adapter is not None:
                await self._adapter.interrupt()
            return
        if ctype == "decisions":
            self._apply_decisions(cmd.get("outcomes") or [])
            return
        if ctype == "ask_answer":
            fut = self._ask_future
            if fut is not None and not fut.done():
                fut.set_result(str(cmd.get("value", "")))
            return
        if ctype == "set_mode":
            self._set_mode(cmd.get("mode", "default"))
            return
        # ── background-task control (act immediately, on the loop) ───
        if ctype in ("task_stop", "task_resume", "task_restart", "task_answer", "task_list"):
            self._task_control(cmd)
            return
        if ctype == "quit":
            self._stop.set()
            return

        # ── ordered (queue behind the current turn) ──────────────────
        await self._cmd_queue.put(cmd)

    def _task_control(self, cmd: dict[str, Any]) -> None:
        """User-driven background-task control from the UI (dashboard / /task …)."""
        reg = self._registry
        if reg is None:
            return
        ctype = cmd.get("type")
        tid = cmd.get("task_id", "")
        if ctype == "task_stop":
            reg.stop(tid)
        elif ctype == "task_resume":
            reg.resume(tid, cmd.get("message") or "Continue where you left off.")
        elif ctype == "task_restart":
            reg.restart(tid)
        elif ctype == "task_answer":
            reg.answer_permission(tid, cmd.get("outcomes") or [])
        elif ctype == "task_list":
            emit(
                {
                    "type": "task_list",
                    "tasks": [
                        self._task_json(t)
                        for t in (reg.get(s.id) for s in reg.list())
                        if t is not None
                    ],
                }
            )

    # ── worker loop (ordered work) ───────────────────────────────────

    async def worker(self) -> None:
        while not self._stop.is_set():
            try:
                cmd = await asyncio.wait_for(self._cmd_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            ctype = cmd.get("type")
            try:
                if ctype == "user":
                    await self._run_turn(cmd.get("text", ""))
                elif ctype == "switch_model":
                    await self._switch_model(cmd.get("model", ""))
                elif ctype == "clear":
                    await self._clear()
                elif ctype == "tree":
                    await self._tree(str(cmd.get("node") or ""))
                elif ctype == "resume":
                    await self._resume(str(cmd.get("session_id") or ""))
                elif ctype == "compact":
                    await self._compact()
                elif ctype == "describe":
                    self._emit_ready()
                elif ctype == "skill":
                    await self._skill(cmd)
                else:
                    emit({"type": "error", "message": f"unknown command: {ctype}"})
            except Exception as e:  # never let one command kill the worker
                _log.exception("command %s failed", ctype)
                emit({"type": "error", "message": f"{type(e).__name__}: {e}"})

    # ── skills (/skill command) ──────────────────────────────────────

    def _skills_dir(self) -> Path:
        from coding_agent.backend import SKILLS_DIR

        return SKILLS_DIR

    def _list_skills(self) -> list[tuple[str, str]]:
        """(name, description) for every skill on disk, sorted by name."""
        out: list[tuple[str, str]] = []
        for md in sorted(self._skills_dir().glob("*/SKILL.md")):
            fm = _skill_frontmatter(md.read_text(encoding="utf-8")) if md.exists() else {}
            out.append((str(fm.get("name") or md.parent.name), str(fm.get("description") or "")))
        return out

    def _author_skill(self, brief: str) -> str:
        """One-shot call to the CONFIGURED model to write a SKILL.md."""
        from koda.summarizer import create_chat_model

        llm = create_chat_model(self._model)
        resp = llm.invoke(SKILL_AUTHOR_PROMPT.format(brief=brief))
        text = getattr(resp, "content", resp)
        if isinstance(text, list):  # some providers return content blocks
            text = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in text)
        return _clean_skill_md(str(text))

    async def _skill(self, cmd: dict[str, Any]) -> None:
        """List skills, or author a new one with the settled LLM and save it."""
        action = cmd.get("action")
        if action == "list":
            skills = await asyncio.to_thread(self._list_skills)
            if not skills:
                emit({"type": "info", "message": "No skills yet. Create one with:  /skill new <name>: <what it does>"})
                return
            body = "\n".join(f"  • {n} — {d[:90]}" for n, d in skills)
            emit({"type": "info", "message": f"Skills ({len(skills)}) in coding_agent/skills/:\n{body}"})
            return

        if action != "create":
            emit({"type": "error", "message": f"unknown skill action: {action}"})
            return

        brief = str(cmd.get("brief") or "").strip()
        if not brief:
            emit({"type": "error", "message": "Usage:  /skill new <name>: <what the skill should do>"})
            return

        emit({"type": "info", "message": f"Authoring skill with {self._model}…"})
        try:
            content = await asyncio.to_thread(self._author_skill, brief)
        except Exception as e:
            emit({"type": "error", "message": f"skill authoring failed: {type(e).__name__}: {e}"})
            return

        fm = _skill_frontmatter(content)
        name = _slug(str(fm.get("name") or brief.split(":", 1)[0]))
        if not name:
            emit({"type": "error", "message": "could not determine a valid skill name — try naming it explicitly."})
            return
        # Keep the frontmatter name in sync with the directory (the spec requires
        # they match, and the model may have slugged it differently).
        if str(fm.get("name") or "") != name:
            content = re.sub(r"(?m)^name:.*$", f"name: {name}", content, count=1)

        dest = self._skills_dir() / name
        if (dest / "SKILL.md").exists():
            emit({"type": "error", "message": f"skill '{name}' already exists — pick another name or delete it first."})
            return
        try:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "SKILL.md").write_text(content, encoding="utf-8")
        except Exception as e:
            emit({"type": "error", "message": f"could not write skill: {e}"})
            return
        emit(
            {
                "type": "info",
                "message": (
                    f"✓ Created skill '{name}' → coding_agent/skills/{name}/SKILL.md\n"
                    f"  It loads into the skills list on your next session (/clear or restart); "
                    f"the agent can read it now via that path."
                ),
            }
        )

    # ── a single user turn ───────────────────────────────────────────

    async def _run_turn(self, text: str) -> None:
        if not text.strip() and not self._pending_task_notes:
            emit({"type": "turn_end", "reply": ""})
            return
        # Inject any finished-task notifications ahead of the user's message so
        # the agent learns its background subagents completed (Claude-Code style).
        notes = self._pending_task_notes
        self._pending_task_notes = []
        prefix = ("\n".join(notes) + "\n\n") if notes else ""
        expanded = prefix + expand_at_files(text)
        self._history.append({"role": "user", "content": expanded})
        try:
            self._session.add_message("user", text)
        except Exception:
            _log.debug("session write failed", exc_info=True)
        reply_parts: list[str] = []
        self._pending_perm_items = None
        self._interrupted = False

        self._turn_task = asyncio.current_task()
        try:
            async for ev in self._adapter.stream(expanded, self._history[:-1]):
                if isinstance(ev, ToolStart) and ev.name == "write_todos":
                    # Route todo snapshots to a dedicated event (compact
                    # inline checklist on the client), like the Textual UI.
                    emit({"type": "todos", "todos": ev.arguments.get("todos", [])})
                    emit(
                        {
                            "type": "tool_start",
                            "tool_id": ev.tool_id,
                            "name": ev.name,
                            "arguments": {},
                            "hidden": True,
                        }
                    )
                    continue
                if isinstance(ev, PermissionRequest):
                    self._pending_perm_items = list(ev.items)
                payload = _event_to_json(ev)
                if payload is not None:
                    emit(payload)
                if isinstance(ev, TextDelta):
                    reply_parts.append(ev.content)
        except asyncio.CancelledError:
            # Hard cancel of the worker task (shutdown). The user-interrupt
            # path is confirmed via the _interrupted flag below instead.
            pass
        except Exception as e:
            _log.exception("turn failed")
            emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            self._turn_task = None
            self._pending_perm_items = None

        if self._interrupted:
            # The adapter unwinds a cancelled stream gracefully (BaseAdapter
            # races the cancel event), so the CancelledError branch above never
            # fires — confirm the interrupt here instead.
            emit({"type": "info", "message": "turn interrupted"})
            self._interrupted = False

        reply = "".join(reply_parts)
        self._history.append({"role": "assistant", "content": reply})
        if reply:
            try:
                self._session.add_message("assistant", reply)
            except Exception:
                _log.debug("session write failed", exc_info=True)
        emit({"type": "turn_end", "reply": reply})

    # ── permission decisions ─────────────────────────────────────────

    def _apply_decisions(self, outcomes: list[str]) -> None:
        """Map per-item outcomes to LangGraph resume decisions and hand them
        back to the adapter, resuming the checkpointed graph. Mirrors
        ``KodaApp._on_permission_choice``."""
        # A sync-gated backend (deep adapter) prompt takes priority: its worker
        # thread is parked on _hook_future and there's exactly one item.
        hook = self._hook_future
        if hook is not None and not hook.done():
            hook.set_result(outcomes[0] if outcomes else "deny")
            return
        items = self._pending_perm_items or []
        decisions: list[dict[str, Any]] = []
        for i, item in enumerate(items):
            outcome = outcomes[i] if i < len(outcomes) else "deny"
            if outcome == "always":
                _perms.allow_tool(item.tool_name)
                decisions.append({"type": "approve"})
            elif outcome == "allow":
                decisions.append({"type": "approve"})
            else:  # deny
                decisions.append(
                    {"type": "reject", "message": _perms.reject_message(item.tool_name)}
                )
        provide = getattr(self._adapter, "provide_decisions", None)
        if provide is not None:
            try:
                provide(decisions)
            except Exception:
                _log.exception("provide_decisions failed")
        self._pending_perm_items = None

    # ── mode / model / session ops ───────────────────────────────────

    def _set_mode(self, mode_name: str) -> None:
        aliases = {
            "default": Mode.DEFAULT,
            "normal": Mode.DEFAULT,
            "edits": Mode.EDITS,
            "accept-edits": Mode.EDITS,
            "plan": Mode.PLAN,
        }
        target = aliases.get((mode_name or "").strip().lower())
        if target is None:
            emit({"type": "error", "message": f"unknown mode: {mode_name}"})
            return
        _perms.set_mode(target)
        emit({"type": "mode_changed", "mode": target.value})

    async def _switch_model(self, model_spec: str) -> None:
        model_spec = (model_spec or "").strip()
        if not model_spec:
            emit({"type": "info", "message": f"current model: {self._model}"})
            return
        old = self._adapter
        # SAME thread_id: the LangGraph checkpoint holds every prior turn, so
        # the new model continues the conversation (matches the Textual TUI).
        try:
            self._adapter = self._factory(model=model_spec, thread_id=self._thread_id)
            ensure = getattr(self._adapter, "_ensure_graph", None)
            if ensure is not None:
                await ensure()
        except Exception as e:
            self._adapter = old  # roll back
            emit({"type": "error", "message": f"switch failed: {e}"})
            return
        # The checkpoint already has the transcript — mark the rebuilt adapter
        # seeded so it doesn't re-forward history and duplicate messages.
        mark = getattr(self._adapter, "mark_seeded", None)
        if callable(mark):
            mark()
        self._model = model_spec
        if self._registry is not None:
            self._registry.set_model(model_spec)
        if old is not None:
            aclose = getattr(old, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass
        emit({"type": "model_changed", "model": self._model})
        self._emit_ready()

    # ── session tree (/tree) ─────────────────────────────────────────

    def _render_tree(self) -> str:
        """ASCII rendering of the branchable session tree (Pi-style).

        ● = on the active path, ○ = other branches, ← = current position.
        Indentation only deepens at real branch points so a linear chat stays
        flat and readable.
        """
        tree = self._session
        active_ids = {e.id for e in tree.get_active_path()}
        leaf = tree.leaf_id
        lines: list[str] = [
            f"session {tree.session_id} — {tree.message_count()} message(s)",
        ]

        def walk(parent_id: str | None, depth: int) -> None:
            children = tree.get_children(parent_id)
            branching = len(children) > 1
            for ch in children:
                d = depth + (1 if branching else 0)
                if ch.type == "message" and ch.role in ("user", "assistant"):
                    mark = "●" if ch.id in active_ids else "○"
                    here = "  ← you are here" if ch.id == leaf else ""
                    text = " ".join(ch.content.split())[:64]
                    lines.append(f"{'  ' * d}{mark} {ch.id}  {ch.role:<9} {text}{here}")
                elif ch.type == "compaction":
                    lines.append(f"{'  ' * d}◆ {ch.id}  compaction")
                walk(ch.id, d)

        walk(None, 0)
        if len(lines) == 1:
            lines.append("  (no messages yet)")
        lines.append("")
        lines.append("/tree <id> jumps to a node — your next message branches from there.")
        if len(lines) > 100:
            lines = lines[:4] + [f"  … {len(lines) - 8} more …"] + lines[-4:]
        return "\n".join(lines)

    async def _tree(self, node: str) -> None:
        if not node:
            emit({"type": "info", "message": self._render_tree()})
            return
        entry = self._session.navigate_to(node.strip())
        if entry is None:
            emit({"type": "error", "message": f"no such node: {node}"})
            return
        # Branch point moved: rebuild the agent on a FRESH checkpoint thread and
        # queue the tree's active path as seed history — LangGraphAdapter
        # forwards it on the first turn of an unseeded thread, so the model
        # picks up exactly the conversation up to the chosen node.
        self._thread_id = uuid.uuid4().hex
        old = self._adapter
        try:
            self._adapter = self._factory(model=self._model, thread_id=self._thread_id)
            ensure = getattr(self._adapter, "_ensure_graph", None)
            if ensure is not None:
                await ensure()
        except Exception as e:
            self._adapter = old
            emit({"type": "error", "message": f"branch failed: {e}"})
            return
        self._history = list(self._session.get_messages_for_agent())
        if old is not None:
            aclose = getattr(old, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass
        preview = " ".join(entry.content.split())[:60]
        emit(
            {
                "type": "info",
                "message": (
                    f"↩ moved to {entry.id} ({entry.role}: {preview}…) — "
                    "your next message branches from here."
                ),
            }
        )

    # ── /resume (Claude-Code-style session picker) ───────────────────

    async def _resume(self, session_id: str) -> None:
        """No id → send the picker list; with an id → reopen that session.

        Resuming appends to the SAME JSONL (Claude behavior). The agent's
        memory is restored by seeding the session's active path into a fresh
        checkpoint thread on the next turn (adapter left unseeded).
        """
        if not session_id:
            infos = await asyncio.to_thread(session_store.list_sessions)
            emit(
                {
                    "type": "sessions",
                    "sessions": [
                        {
                            "id": s.id,
                            "started": s.started,
                            "messages": s.messages,
                            "preview": s.preview,
                        }
                        for s in infos
                        if s.id != self._session.session_id
                    ],
                }
            )
            return

        # koda -c / --continue: resolve the sentinel to the most recent
        # resumable session for this project (list_sessions is newest-first and
        # skips empty husks; exclude the just-created session).
        if session_id in ("__latest__", "latest"):
            infos = await asyncio.to_thread(session_store.list_sessions)
            infos = [s for s in infos if s.id != self._session.session_id]
            if not infos:
                emit({"type": "info", "message": "No previous session to continue — starting fresh."})
                return
            session_id = infos[0].id

        path = session_store.find_session(session_id)
        if path is None:
            emit({"type": "error", "message": f"no session matching: {session_id}"})
            return
        try:
            tree = session_store.load_session(path)
        except Exception as e:
            emit({"type": "error", "message": f"could not load session: {e}"})
            return

        # Fresh checkpoint thread seeded from the resumed transcript.
        self._session = tree
        self._history = list(tree.get_messages_for_agent())
        self._thread_id = uuid.uuid4().hex
        old = self._adapter
        try:
            self._adapter = self._factory(model=self._model, thread_id=self._thread_id)
            ensure = getattr(self._adapter, "_ensure_graph", None)
            if ensure is not None:
                await ensure()
        except Exception as e:
            self._adapter = old
            emit({"type": "error", "message": f"resume failed: {e}"})
            return
        if old is not None:
            aclose = getattr(old, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass
        _perms.clear_session_allow()  # permission grants don't carry across resumes

        # Replay the transcript so the UI can re-render the conversation.
        emit(
            {
                "type": "resumed",
                "session_id": tree.session_id,
                "messages": [
                    {"role": m["role"], "content": m["content"][:4000]}
                    for m in self._history
                    if m.get("role") in ("user", "assistant")
                ],
            }
        )

    async def _clear(self) -> None:
        self._history.clear()
        _perms.clear_session_allow()
        # Fresh branchable session file + a matching fresh checkpoint thread.
        self._session = session_store.new_session()
        self._thread_id = self._session.session_id or uuid.uuid4().hex
        old = self._adapter
        self._adapter = self._factory(model=self._model, thread_id=self._thread_id)
        ensure = getattr(self._adapter, "_ensure_graph", None)
        if ensure is not None:
            try:
                await ensure()
            except Exception:
                pass
        if old is not None:
            aclose = getattr(old, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass
        emit({"type": "cleared"})
        self._emit_ready()

    async def _compact(self) -> None:
        compact = getattr(self._adapter, "compact", None)
        if compact is None:
            emit({"type": "info", "message": "compaction isn't supported by this agent."})
            return
        emit({"type": "info", "message": "compacting…"})
        try:
            result = await compact()
        except Exception as e:
            emit({"type": "error", "message": f"compaction failed: {e}"})
            return
        if getattr(result, "compacted", False):
            emit(
                {
                    "type": "info",
                    "message": (
                        f"✓ compacted {getattr(result, 'summarized_messages', 0)} "
                        "message(s) into a summary; recent turns kept intact."
                    ),
                }
            )
        else:
            emit({"type": "info", "message": getattr(result, "reason", "nothing to compact")})

    # ── run ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self.build()
        reader = asyncio.create_task(self.read_stdin())
        worker = asyncio.create_task(self.worker())
        await self._stop.wait()
        for t in (reader, worker):
            t.cancel()
        await asyncio.gather(reader, worker, return_exceptions=True)
        await self.aclose()


def main(argv: list[str] | None = None) -> None:
    from koda.__main__ import _build_adapter_factory, _default_model, _load_dotenv

    _load_dotenv()

    parser = argparse.ArgumentParser(prog="koda.bridge")
    parser.add_argument("--model", "-m", default=None)
    parser.add_argument("--agent", "-a", default="coding_agent")
    parser.add_argument("--cwd", "-C", default=None)
    parser.add_argument("--thread", default=None)
    parser.add_argument(
        "--auto-approve", "-y",
        action="store_true",
        help="Approve every gated tool call without prompting (unattended runs).",
    )
    args = parser.parse_args(argv)

    if args.auto_approve:
        from koda.tools import permissions as _p

        _p.set_auto_approve(True)

    if args.cwd:
        target = Path(args.cwd).expanduser().resolve()
        if target.is_dir():
            os.chdir(target)

    # Logs go to a file (stderr is reserved for the client to surface).
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("koda").setLevel(logging.WARNING)

    model = args.model or _default_model()
    factory = _build_adapter_factory(args.agent)
    bridge = Bridge(factory=factory, model=model, thread_id=args.thread)

    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
