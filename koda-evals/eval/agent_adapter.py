"""KODA-specific agent adapter for the eval suite.

What this does
--------------
Two ways to invoke your agent -- pick one via `EVAL_AGENT_MODE` env var:

  - "import"     -> imports CodingAgentAdapter from koda.adapters.coding_agent
                   (faster, in-process, traces nest naturally inside Langfuse)
  - "subprocess" -> runs the `koda` CLI as a subprocess (same way a user would)

Default is "import". The runner sets up a Langfuse trace BEFORE calling
run_agent(), so the agent's existing @observe-decorated traces will nest
inside the eval trace automatically.

What you need to verify in YOUR repo
------------------------------------
Search your code for these and tweak the lines marked `# CHECK:` below if
they don't match:

  1. CodingAgentAdapter constructor signature -> `CodingAgentAdapter(model="ollama:qwen2.5-coder:7b")`?
  2. The method that runs one prompt  -> `.stream(message, history)` (async iterator of AgentEvent)?
  3. Whether it accepts session_id    -> for grouping traces per eval run

If the import path is different, fix `_KODA_IMPORT_PATH` below.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any


# CHECK: confirm this matches your repo
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


# -----------------------------------------------------------------------
# Mode A: import CodingAgentAdapter and call .stream() (preferred)
# -----------------------------------------------------------------------


def _enable_yolo_permissions() -> str:
    """Auto-approve every gated tool so the agent never blocks on a prompt.

    KODA's permission gate (``koda.tools.permissions``) returns ``"ask"`` for
    mutating tools (``edit_file``, ``execute``, …) unless the user is driving
    the TUI. In a headless eval there is no human to answer, so the graph
    emits a ``PermissionRequest`` and blocks until ``EVAL_AGENT_TIMEOUT`` —
    every instance then dies with an empty patch. SWE-bench runs in disposable
    tempdir clones, so blanket-approval (true "YOLO") is both safe and the
    documented intent. We force ``decide`` to always approve, which also
    covers the dangerous-execute backstop (repo-rooted ``grep -r`` etc. that
    SWE-bench agents legitimately run).

    Returns a short status string for logging. Best-effort: if the internal
    API ever moves, we log and let the run proceed rather than abort.
    """
    try:
        from koda.tools import permissions as _perms

        _perms.set_auto_approve(True)
        return "auto-approve on"
    except AttributeError:
        # Older KODA without set_auto_approve — fall back to the previous
        # belt-and-braces monkeypatch so the eval still runs against it.
        try:
            from koda.tools import permissions as _perms
            from koda.modes import Mode

            _perms.set_mode(Mode.EDITS)
            for tool in _perms.MUTATING_TOOLS:
                _perms.allow_tool(tool)
            _perms.decide = lambda tool_name, args=None: "approve"  # type: ignore[assignment]
            return "yolo fallback: decide→approve"
        except Exception as e:  # pragma: no cover - defensive
            return f"yolo setup failed ({type(e).__name__}: {e})"
    except Exception as e:  # pragma: no cover - defensive
        return f"yolo setup failed ({type(e).__name__}: {e})"


def _run_via_import(prompt: str, workdir: Path, *, session_id: str) -> AgentResult:
    start = time.perf_counter()
    captured_stdout, captured_stderr = StringIO(), StringIO()

    try:
        import asyncio
        import importlib

        mod = importlib.import_module(_KODA_IMPORT_PATH)
        AgentCls = getattr(mod, _KODA_CLASS_NAME)

        yolo_status = _enable_yolo_permissions()
        print(f"    [adapter] permissions: {yolo_status}", flush=True)

        # CodingAgentAdapter(model=...) -- model spec routed via BaseAdapter
        model_spec = os.getenv("KODA_MODEL", "ollama:qwen2.5-coder:7b")
        agent = AgentCls(model=model_spec)

        # Skip AGENTS.md bootstrap inside eval workdirs -- eval repos are
        # tiny and don't need a project context file. Saves tokens + time.
        os.environ.setdefault("KODA_DISABLE_BOOTSTRAP", "1")

        # Run from inside the workdir so the agent's relative paths (read_file,
        # grep, run_shell, etc.) resolve correctly.
        prev_cwd = os.getcwd()
        os.chdir(workdir)
        try:
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):

                async def _collect() -> str:
                    """Consume the async stream and return concatenated text.

                    KODA's AgentEvent is a Union of dataclasses defined in
                    ``koda.agent_api``; the one we care about is
                    ``TextDelta(content=str)`` — *not* ``.text``. The
                    earlier ``hasattr(event, "text")`` check silently
                    matched nothing, so ``result_text`` was always empty
                    and logs misleadingly said "agent produced 0 stdout
                    chars" even when the agent ran a full multi-step turn.
                    SWE-bench grading reads ``git diff`` from the workdir,
                    so the patch was still captured correctly — but the
                    captured stdout is what tells you whether the agent
                    actually said anything.
                    """
                    from koda.agent_api import TextDelta, ToolResult

                    parts: list[str] = []
                    n_events = 0
                    async for event in agent.stream(prompt, []):
                        n_events += 1
                        if n_events % 20 == 0:
                            print(f"    [adapter] {n_events} events received…", flush=True)
                        if isinstance(event, TextDelta):
                            parts.append(event.content)
                        # BaseAdapter.stream swallows build/runtime exceptions and
                        # emits them as a synthetic error ToolResult, then ends the
                        # stream cleanly. Without this, a failed run (e.g. an
                        # unroutable model spec) reads as "empty patch, no error".
                        elif isinstance(event, ToolResult) and getattr(
                            event, "tool_id", None
                        ) == "adapter_error":
                            raise RuntimeError(getattr(event, "output", "adapter error"))
                    print(f"    [adapter] stream finished: {n_events} events, "
                          f"{len(parts)} text deltas", flush=True)
                    return "".join(parts)

                timeout_s = int(os.getenv("EVAL_AGENT_TIMEOUT", "900"))
                result_text = asyncio.run(
                    asyncio.wait_for(_collect(), timeout=timeout_s)
                )
        finally:
            os.chdir(prev_cwd)

        return AgentResult(
            stdout=captured_stdout.getvalue() + result_text,
            stderr=captured_stderr.getvalue(),
            elapsed_s=time.perf_counter() - start,
            metadata={"mode": "import", "model": model_spec, "session_id": session_id},
        )

    except Exception as e:
        return AgentResult(
            stdout=captured_stdout.getvalue(),
            stderr=captured_stderr.getvalue() + "\n" + traceback.format_exc(),
            elapsed_s=time.perf_counter() - start,
            error=f"{type(e).__name__}: {e}",
            metadata={"mode": "import"},
        )


# -----------------------------------------------------------------------
# Mode B: spawn the `koda` CLI binary as a subprocess
# -----------------------------------------------------------------------


def _run_via_subprocess(prompt: str, workdir: Path, *, session_id: str) -> AgentResult:
    """Invoke the koda CLI. Customize EVAL_AGENT_CMD if your entry point differs.

    Default assumes a one-shot mode where prompt comes via stdin and the agent
    runs in --cwd. If your CLI takes the prompt as an arg or from a flag,
    tweak EVAL_AGENT_CMD; {prompt} and {workdir} are substituted.
    """
    cmd_template = os.getenv(
        "EVAL_AGENT_CMD",
        # CHECK: confirm `koda` is on PATH and accepts these flags. Your doc
        # mentions `--model openai:X / --model ollama:X` so this should work.
        "koda --cwd {workdir} --model {model} --no-tui",
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


# -----------------------------------------------------------------------
# Dispatcher
# -----------------------------------------------------------------------


def run_agent(prompt: str, workdir: Path, *, session_id: str = "") -> AgentResult:
    """Run KODA on a single task. Called by eval/runner.py."""
    mode = os.getenv("EVAL_AGENT_MODE", "import")
    if mode == "import":
        return _run_via_import(prompt, workdir, session_id=session_id)
    if mode == "subprocess":
        return _run_via_subprocess(prompt, workdir, session_id=session_id)
    raise ValueError(f"Unknown EVAL_AGENT_MODE: {mode!r} (expected 'import' or 'subprocess')")