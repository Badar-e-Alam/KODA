"""
KODA entry point — agent-agnostic AI coding TUI.

Usage:
    koda                                          # Default model from API keys
    koda --model anthropic:claude-sonnet-4-6      # Specify model
    koda --model openai:gpt-4o                    # OpenAI
    koda --model ollama:llama3.1                  # Local Ollama
    koda --agent deep                             # Built-in deep agent (default)
    koda --agent module.ClassName                 # Custom KodaAgent class
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
    """Pick a default model from available API keys."""
    _load_dotenv()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic:claude-sonnet-4-6"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai:gpt-4o"
    if os.environ.get("GOOGLE_API_KEY"):
        return "google:gemini-2.5-flash"
    if os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_HOST"):
        return "ollama:llama3.1"
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
    args = parser.parse_args()

    if args.cwd is not None:
        import os
        from pathlib import Path as _Path

        target = _Path(args.cwd).expanduser().resolve()
        if not target.is_dir():
            parser.error(f"--cwd: not a directory: {target}")
        # chdir before adapters are built so every cwd-derived path
        # (AGENTS.md, .koda/, /memories/, checkpoints.db) anchors here.
        os.chdir(target)

    _setup_logging()

    # Warm provider model lists in the background so /model is instant.
    # Kicked off before building the (potentially slow) first adapter,
    # so by the time the user reaches the TUI the cache is usually ready.
    try:
        from koda.model_config import warm_cache_in_background

        warm_cache_in_background()
    except Exception:
        pass  # non-fatal — /model will still work off fallback lists

    # ``thread_id`` is intentionally left ``None`` here: ``KodaApp`` derives
    # it from the freshly-created ``SessionTree.session_id`` so that the
    # LangGraph checkpointer's thread matches the session shown in the
    # sidebar. A random UUID at launch would create an orphan thread that
    # the user could never resume.
    thread_id = None
    model = args.model or _default_model()
    # Build the factory eagerly (cheap — just resolves a callable) but defer
    # the expensive ``factory(model, thread_id)`` call into a background
    # thread on TUI mount, so the user sees the KODA banner within ~1 s even
    # when langchain + langgraph imports take 3–4 s to warm up.
    factory = _build_adapter_factory(args.agent)

    import logging
    logging.getLogger("koda").info("Starting KODA: agent=%s model=%s", args.agent, model)

    import asyncio
    # Wrap ``asyncio.run`` so Ctrl+C exits cleanly with status 130 (the
    # conventional exit code for SIGINT) instead of dumping a multi-screen
    # traceback. On Python 3.11+ ``asyncio.run`` installs its own SIGINT
    # handler that races with Textual's Ctrl+C keybinding; when asyncio
    # wins the race, the ``KeyboardInterrupt`` lands inside whatever
    # Textual code path was running (typically a style-property getter
    # mid-paint) and bubbles up unhandled. Catching here is the
    # documented pattern for asyncio scripts. The shutdown side-effects
    # (adapter aclose, on_unmount, atexit hooks) still run because the
    # cancellation propagates through ``run_async()`` first.
    try:
        asyncio.run(_run_app(
            factory=factory,
            model=model,
            thread_id=thread_id,
            auto_approve=args.auto_approve,
        ))
    except KeyboardInterrupt:
        sys.exit(130)


async def _run_app(*, factory, model: str, thread_id: str | None = None, auto_approve: bool = False) -> None:
    from koda.tui.app import KodaApp

    app = KodaApp(
        adapter=None,  # built lazily in KodaApp.on_mount
        adapter_factory=factory,
        model=model,
        thread_id=thread_id,
        auto_approve=auto_approve,
    )
    await app.run_async()


if __name__ == "__main__":
    main()
