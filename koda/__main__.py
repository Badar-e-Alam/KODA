"""
KODA entry point — agent-agnostic AI coding assistant.

Usage — Interactive TUI (default)
    koda                                          # Default model from API keys
    koda --model anthropic:claude-sonnet-4-6      # Specify model
    koda --model openai:gpt-4o                    # OpenAI
    koda --model ollama:llama3.1                  # Local Ollama
    koda --agent deep                             # Built-in deep agent
    koda --agent module.ClassName                 # Custom KodaAgent class

Usage — One-shot mode (no TUI)
    koda --prompt "Fix the pagination bug in pagination.py"
    echo "Add a README" | koda --prompt
    koda --no-tui --cwd /path/to/project
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    # Disable LangSmith tracing — KODA doesn't use it
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    os.environ.setdefault("LANGSMITH_TRACING", "false")


def _default_model() -> str:
    """Pick a default model from available API keys.

    ``KODA_DEFAULT_MODEL`` (env) wins if set so users can pin a default
    in their ``.env`` without passing ``--model`` on every launch.

    The Ollama split matters: ``OLLAMA_HOST`` points at a daemon
    (local or self-hosted) where ``llama3.1`` is a reasonable bet;
    ``OLLAMA_API_KEY`` *alone* means cloud-only — and Ollama Cloud's
    catalog does not include ``llama3.1``, so falling back to it gives
    a 404 on the first turn. Pick a small, currently-hosted cloud model
    instead so the first turn just works.
    """
    _load_dotenv()
    explicit = os.environ.get("KODA_DEFAULT_MODEL")
    if explicit:
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic:claude-sonnet-4-6"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai:gpt-4o"
    if os.environ.get("GOOGLE_API_KEY"):
        return "google:gemini-2.5-flash"
    if os.environ.get("OLLAMA_HOST"):
        # Explicit host — assume the user knows their daemon has llama3.1.
        return "ollama:llama3.1"
    if os.environ.get("OLLAMA_API_KEY"):
        # Cloud-only fallback. ``gpt-oss:20b`` is small + fast + present
        # in the Ollama Cloud catalog. Override with KODA_DEFAULT_MODEL
        # or ``--model`` for anything bigger.
        return "ollama:kimi-k2.7-code"
    return "anthropic:claude-sonnet-4-6"


def _build_adapter_factory(spec: str):
    """Return a callable ``factory(model, thread_id) -> KodaAgent``.

    Resolution rules (same as before, but reusable across /model switches):
      "deep"                    -> KODA built-in deep adapter
      "module.path.ClassName"   -> custom factory that returns a KodaAgent
                                   (or a raw LangGraph graph — auto-wrapped)
    """
    if spec == "deep":
        from koda.adapters.deep import create_deep_adapter
        return lambda model, thread_id: create_deep_adapter(
            model=model, thread_id=thread_id
        )

    if spec == "coding_agent":
        from koda.adapters.coding_agent import create_coding_agent_adapter
        return lambda model, thread_id: create_coding_agent_adapter(
            model=model, thread_id=thread_id
        )

    if "." in spec:
        module_path, class_name = spec.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            user_factory = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            print(f"Error loading agent '{spec}': {exc}", file=sys.stderr)
            sys.exit(1)

        def factory(model: str, thread_id: str):
            result = user_factory(model=model)
            from koda.agent_api import KodaAgent
            if isinstance(result, KodaAgent):
                return result
            from koda.adapters.langgraph import LangGraphAdapter
            return LangGraphAdapter(graph=result, model=model, thread_id=thread_id)

        return factory

    print(f"Unknown agent: '{spec}'", file=sys.stderr)
    print(
        "Options:\n"
        "  deep                    KODA built-in deep adapter (default)\n"
        "  coding_agent            OpenAI-Agents-SDK coding agent (coding_agent/)\n"
        "  module.ClassName        Custom factory returning a KodaAgent",
        file=sys.stderr,
    )
    sys.exit(1)


def _load_adapter(spec: str, model: str, thread_id: str):
    """Build the initial adapter. See ``_build_adapter_factory`` for details."""
    return _build_adapter_factory(spec)(model, thread_id)


def _setup_logging() -> str:
    """Write debug logs to logs/ inside the KODA project directory."""
    import logging
    from datetime import datetime

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(log_dir, f"session_{ts}.log")

    logging.basicConfig(
        filename=log_path,
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )
    logging.getLogger("koda").setLevel(logging.DEBUG)
    logging.getLogger("langgraph").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("koda").info("=== KODA session started === log: %s", log_path)
    return log_path


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(
        prog="koda",
        description="KODA — Agent-agnostic AI coding TUI",
    )
    parser.add_argument(
        "--agent", "-a",
        default="coding_agent",
        help="Agent backend: 'coding_agent' (default), 'deep', or 'module.ClassName'",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model: provider:model (e.g. anthropic:claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--auto-approve", "-y",
        action="store_true",
        help="Auto-approve all tool calls",
    )
    parser.add_argument(
        "--cwd", "-C",
        default=None,
        metavar="PATH",
        help=(
            "Project directory the agent will operate on. Default: the "
            "shell's current directory. Lets you target a project without "
            "having to `cd` into it (e.g. `koda --cwd ~/work/meal-planning`)."
        ),
    )
    parser.add_argument(
        "--prompt", "-p",
        default=None,
        nargs="?",
        const="__stdin__",
        metavar="TEXT",
        help=(
            "One-shot mode: run a single prompt and stream text to stdout, "
            "then exit. If no TEXT is given, reads the prompt from stdin. "
            "Useful for evals, scripts, and piping."
        ),
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        dest="no_tui",
        help="Alias for --prompt (one-shot, non-interactive)",
    )
    parser.add_argument(
        "--mouse",
        dest="mouse",
        default=None,
        action=argparse.BooleanOptionalAction,
        help=(
            "Capture the mouse for in-app scroll/click (--mouse) or release it "
            "for native terminal select/copy and link-clicks (--no-mouse, the "
            "default). Toggle at runtime with Ctrl+O. Env: KODA_MOUSE=1."
        ),
    )
    args = parser.parse_args()

    # --no-tui is an alias for --prompt (backward compat with eval harness)
    prompt = args.prompt
    if args.no_tui and prompt is None:
        prompt = "__stdin__"
    elif args.no_tui and prompt is not None and prompt != "__stdin__":
        # --prompt was already set, leave it alone
        pass

    if args.cwd is not None:
        import os
        from pathlib import Path as _Path

        target = _Path(args.cwd).expanduser().resolve()
        if not target.is_dir():
            parser.error(f"--cwd: not a directory: {target}")
        os.chdir(target)

    _setup_logging()

    model = args.model or _default_model()
    factory = _build_adapter_factory(args.agent)

    import logging
    logging.getLogger("koda").info(
        "Starting KODA: agent=%s model=%s mode=%s",
        args.agent, model, "oneshot" if prompt is not None else "interactive",
    )

    import asyncio
    try:
        if prompt is not None:
            # One-shot mode: stream text to stdout, no TUI
            asyncio.run(_run_oneshot(
                factory=factory,
                model=model,
                prompt=prompt,
                auto_approve=args.auto_approve,
            ))
        else:
            asyncio.run(_run_app(
                factory=factory,
                model=model,
                thread_id=None,
                auto_approve=args.auto_approve,
                mouse=args.mouse,
            ))
    except KeyboardInterrupt:
        sys.exit(130)


async def _run_oneshot(*, factory, model: str, prompt: str, auto_approve: bool = False) -> None:
    """Run one prompt, stream text to stdout, then exit.

    Reads the prompt from stdin if ``--prompt`` was given without a value,
    or from the first positional arg / ``sys.stdin`` when piped.
    """
    import time
    from koda.agent_api import Done, TextDelta, ToolStart

    # Resolve prompt text
    if prompt == "__stdin__":
        prompt_text = sys.stdin.read()
    else:
        prompt_text = prompt

    # Build adapter (same path as the TUI)
    adapter = factory(model=model, thread_id=f"oneshot-{os.getpid()}")

    if auto_approve:
        # Allow all gated tool calls so the agent can run unattended.
        try:
            from koda.tools import permissions as _perms  # type: ignore[attr-defined]
            _perms.set_auto_approve(True)  # type: ignore[union-attr]
        except Exception:
            pass  # The module doesn't have this function — the eval runner
            # will just wait on permission prompts until timeout. That's
            # harmless but should be rare; the eval harness doesn't use this.

    stream_start = time.monotonic()
    event_count = 0
    try:
        async for event in adapter.stream(prompt_text, []):
            event_count += 1
            if isinstance(event, TextDelta):
                print(event.content, end="", flush=True)
            elif isinstance(event, ToolStart):
                # One tool dot per call on stderr so users know what is happening
                print(".", end="", file=sys.stderr, flush=True)
            elif isinstance(event, Done) and event.usage:
                # Print a compact footer on stderr so stdout stays clean
                u = event.usage
                used = []
                if u.input_tokens:
                    used.append(f"in={u.input_tokens}")
                if u.output_tokens:
                    used.append(f"out={u.output_tokens}")
                print(
                    f"\n[{event_count} events  {' '.join(used)}]",
                    file=sys.stderr, flush=True,
                )
    finally:
        # Clean up the aiosqlite checkpointer (non-daemon worker thread)
        if hasattr(adapter, "aclose"):
            await adapter.aclose()
    elapsed = time.monotonic() - stream_start
    print(f"\n[{elapsed:.1f}s] done", file=sys.stderr, flush=True)


async def _run_app(
    *,
    factory,
    model: str,
    thread_id: str | None = None,
    auto_approve: bool = False,
    mouse: bool | None = None,
) -> None:
    from koda.tui.app import KodaApp

    app = KodaApp(
        adapter=None,  # built lazily in KodaApp.on_mount
        adapter_factory=factory,
        model=model,
        thread_id=thread_id,
        auto_approve=auto_approve,
        mouse=mouse,
    )
    await app.run_async()


if __name__ == "__main__":
    main()
