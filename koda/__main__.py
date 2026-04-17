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


def _load_adapter(spec: str, model: str, thread_id: str):
    """
    Load an agent adapter (KodaAgent).

    Resolution:
      "deep"                    -> KODA built-in deep adapter
      "module.path.ClassName"   -> custom factory that returns a KodaAgent
                                   (or a raw LangGraph graph — auto-wrapped)
    """
    if spec == "deep":
        from koda.adapters.deep import create_deep_adapter
        return create_deep_adapter(model=model, thread_id=thread_id)

    if "." in spec:
        module_path, class_name = spec.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            factory = getattr(module, class_name)
            result = factory(model=model)
        except (ImportError, AttributeError) as exc:
            print(f"Error loading agent '{spec}': {exc}", file=sys.stderr)
            sys.exit(1)

        # Accept both: a KodaAgent or a raw LangGraph graph
        from koda.agent_api import KodaAgent
        if isinstance(result, KodaAgent):
            return result
        from koda.adapters.langgraph import LangGraphAdapter
        return LangGraphAdapter(graph=result, model=model, thread_id=thread_id)

    print(f"Unknown agent: '{spec}'", file=sys.stderr)
    print(
        "Options:\n"
        "  deep                    KODA built-in deep adapter (default)\n"
        "  module.ClassName        Custom factory returning a KodaAgent",
        file=sys.stderr,
    )
    sys.exit(1)


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
        default="deep",
        help="Agent backend: 'deep' (default) or 'module.ClassName'",
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
    args = parser.parse_args()

    _setup_logging()

    import uuid
    thread_id = uuid.uuid4().hex
    model = args.model or _default_model()
    adapter = _load_adapter(args.agent, model=model, thread_id=thread_id)

    import logging
    logging.getLogger("koda").info("Adapter loaded: %s, model: %s", args.agent, model)

    import asyncio
    asyncio.run(_run_app(adapter=adapter, model=model, thread_id=thread_id, auto_approve=args.auto_approve))


async def _run_app(*, adapter, model: str, thread_id: str, auto_approve: bool = False) -> None:
    from koda.tui.app import KodaApp

    app = KodaApp(
        adapter=adapter,
        model=model,
        thread_id=thread_id,
        auto_approve=auto_approve,
    )
    await app.run_async()


if __name__ == "__main__":
    main()
