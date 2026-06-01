"""KODA-specific agent adapter for the eval suite.

What this does
--------------
Two ways to invoke your agent — pick one via ``EVAL_AGENT_MODE`` env var:

  - "import"     → imports CodingAgentAdapter from koda.adapters.coding_agent
                   (faster, in-process, traces nest naturally inside Langfuse)
  - "subprocess" → runs the ``koda`` CLI as a subprocess (same way a user would)

Default is "import". The runner sets up a Langfuse trace BEFORE calling
run_agent(), so the agent's existing @observe-decorated traces will nest
inside the eval trace automatically.

Integration notes
-----------------
The adapter calls ``CodingAgentAdapter(model=..., thread_id=session_id)``
then ``agent.stream(prompt, [])`` in an asyncio event loop. On completion it
calls ``await agent.aclose()`` to close the aiosqlite checkpointer (whose
non-daemon worker thread would otherwise hang the process on exit).

``thread_id`` is set to the eval ``session_id`` so LangGraph's checkpointer
and Langfuse's ``langfuse_callbacks()`` group all events under one trace
per task.

If the import path is different, fix ``_KODA_IMPORT_PATH`` /
``_KODA_CLASS_NAME`` below.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# CHECK: confirm these match your repo
_KODA_IMPORT_PATH = "koda.adapters.coding_agent"
_KODA_CLASS_NAME = "CodingAgentAdapter"


@dataclass
class AgentResult:
    stdout: str = ""
    stderr: str = ""
    elapsed_s: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Mode A: import KodaAgent and call it directly (preferred)
# ─────────────────────────────────────────────────────────────────────────────


def _run_via_import(prompt: str, workdir: Path, *, session_id: str) -> AgentResult:
    start = time.perf_counter()
    _EVAL_TIMEOUT_S = int(os.getenv("EVAL_AGENT_TIMEOUT", "600"))

    try:
        # Import lazily so subprocess mode still works without the package installed.
        import importlib

        mod = importlib.import_module(_KODA_IMPORT_PATH)
        AgentCls = getattr(mod, _KODA_CLASS_NAME)

        # CodingAgentAdapter(model, thread_id=None) — thread_id propagates
        # to both the LangGraph checkpointer and Langfuse langfuse_callbacks(),
        # grouping all LLM/tool events under one trace per task.
        model_spec = os.getenv("KODA_MODEL", "ollama:qwen2.5-coder:7b")
        agent = AgentCls(model=model_spec, thread_id=session_id)

        # Evals run headless — no TUI to answer permission prompts. Pre-allow
        # every mutating tool so the agent can edit files / run commands without
        # hanging on a HITL interrupt that nobody will resume.
        try:
            from koda.tools.permissions import MUTATING_TOOLS, allow_tool
            for tool_name in MUTATING_TOOLS:
                allow_tool(tool_name)
        except Exception:
            pass  # Not all KODA versions expose the same API — harmless to skip.

        # Run from inside the workdir so the agent's relative paths (read_file,
        # grep, etc.) resolve correctly.
        prev_cwd = os.getcwd()
        os.chdir(workdir)
        try:
            # CodingAgentAdapter.stream() is async — it yields AgentEvent
            # objects (TextDelta, ToolStart, ToolResult, Done, ...). We collect
            # TextDelta content into stdout and capture usage from the Done event.

            async def _collect() -> tuple[str, Any | None]:
                from koda.agent_api import (
                    Done, PermissionRequest, TextDelta, ToolStart, ToolResult,
                )

                parts: list[str] = []
                final_usage = None
                n_events = 0
                try:
                    async for event in agent.stream(prompt, []):
                        n_events += 1
                        if n_events % 10 == 0:
                            print(f"    [adapter] {n_events} events received…", flush=True)
                        if isinstance(event, TextDelta):
                            parts.append(event.content)
                        elif isinstance(event, PermissionRequest):
                            # Evals run headless — no TUI to ask the user. Auto-
                            # approve every gated tool so the agent can edit files
                            # and run shell commands without hanging on an
                            # interrupt that nobody will ever resume.
                            decisions = [
                                {"type": "approve"}
                                for _ in event.items
                            ]
                            tool_names = ", ".join(it.tool_name for it in event.items)
                            print(
                                f"    [adapter] auto-approve {len(decisions)} tool(s): {tool_names}",
                                flush=True,
                            )
                            agent.provide_decisions(decisions)
                        elif isinstance(event, ToolStart):
                            print(f"    [adapter] tool: {event.name}", flush=True)
                        elif isinstance(event, Done) and event.usage:
                            final_usage = event.usage
                            print(f"    [adapter] done: tokens in={final_usage.input_tokens} out={final_usage.output_tokens}", flush=True)
                finally:
                    # Close the aiosqlite checkpointer to prevent the non-daemon
                    # worker thread from hanging the process on exit.
                    if hasattr(agent, "aclose"):
                        await agent.aclose()
                print(f"    [adapter] stream finished: {n_events} events, {len(parts)} text deltas", flush=True)
                return "".join(parts), final_usage

            result_text, usage = asyncio.run(
                asyncio.wait_for(_collect(), timeout=_EVAL_TIMEOUT_S)
            )
        finally:
            os.chdir(prev_cwd)

        usage_info: dict[str, Any] = {}
        if usage:
            usage_info = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            }

        return AgentResult(
            stdout=result_text,
            elapsed_s=time.perf_counter() - start,
            metadata={"mode": "import", "model": model_spec, "session_id": session_id, **usage_info},
        )

    except asyncio.TimeoutError:
        # Stream exceeded EVAL_AGENT_TIMEOUT (default 600s).
        try:
            if hasattr(agent, "aclose"):
                asyncio.run(agent.aclose())
        except Exception:
            pass
        return AgentResult(
            elapsed_s=time.perf_counter() - start,
            error=f"timeout after {_EVAL_TIMEOUT_S}s",
            metadata={"mode": "import", "model": model_spec},
        )
    except Exception as e:
        return AgentResult(
            stderr=traceback.format_exc(),
            elapsed_s=time.perf_counter() - start,
            error=f"{type(e).__name__}: {e}",
            metadata={"mode": "import"},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mode B: spawn the `koda` CLI binary as a subprocess
# ─────────────────────────────────────────────────────────────────────────────


def _run_via_subprocess(prompt: str, workdir: Path, *, session_id: str) -> AgentResult:
    """Invoke the koda CLI in one-shot mode.

    Default command uses ``--prompt`` which reads from stdin and exits when
    the agent finishes — no TUI. Override with ``EVAL_AGENT_CMD`` env var;
    ``{workdir}``, ``{model}``, and ``{prompt}`` are substituted.
    """
    cmd_template = os.getenv(
        "EVAL_AGENT_CMD",
        # --prompt enables one-shot (non-TUI) mode: read prompt from stdin,
        # stream text to stdout, exit when done. See koda/__main__.py.
        "koda --cwd {workdir} --model {model} --prompt",
    )
    model = os.getenv("KODA_MODEL", "ollama:qwen2.5-coder:7b")
    cmd = cmd_template.format(workdir=str(workdir), model=model, prompt="").split()

    env = os.environ.copy()
    env["KODA_SESSION_ID"] = session_id  # picked up by your @observe trace
    env["KODA_USER_ID"] = "eval-runner"

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("EVAL_AGENT_TIMEOUT", "600")),
            env=env,
        )
        return AgentResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            elapsed_s=time.perf_counter() - start,
            error=None if proc.returncode == 0 else f"exit {proc.returncode}",
            metadata={"mode": "subprocess", "cmd": " ".join(cmd), "model": model},
        )
    except subprocess.TimeoutExpired:
        return AgentResult(
            elapsed_s=time.perf_counter() - start,
            error="timeout",
            metadata={"mode": "subprocess", "cmd": " ".join(cmd)},
        )
    except FileNotFoundError as e:
        return AgentResult(
            elapsed_s=time.perf_counter() - start,
            error=f"koda binary not found: {e}",
            metadata={"mode": "subprocess", "cmd": " ".join(cmd)},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────


def run_agent(prompt: str, workdir: Path, *, session_id: str = "") -> AgentResult:
    """Run KODA on a single task. Called by eval/runner.py."""
    mode = os.getenv("EVAL_AGENT_MODE", "import")
    if mode == "import":
        return _run_via_import(prompt, workdir, session_id=session_id)
    if mode == "subprocess":
        return _run_via_subprocess(prompt, workdir, session_id=session_id)
    raise ValueError(f"Unknown EVAL_AGENT_MODE: {mode!r} (expected 'import' or 'subprocess')")
