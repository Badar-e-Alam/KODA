"""
KODA entry point.

Uses deepagents-cli TUI with a pluggable agent backend.

Usage:
    koda                                          # Deep agent, auto-detect model
    koda --model anthropic:claude-sonnet-4-6      # Specify model
    koda --model openai:gpt-4o                    # OpenAI
    koda --model ollama:llama3.1                  # Ollama
    koda --agent deep                             # Explicit deep agent (default)
    koda --agent module.path.ClassName            # Custom agent class
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
    # Ollama — local server needs no key, cloud uses OLLAMA_API_KEY
    if os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_HOST"):
        return "ollama:llama3.1"
    return "anthropic:claude-sonnet-4-6"


def _load_agent(spec: str, model: str):
    """
    Load an agent backend. Returns a LangGraph Pregel graph.

    Resolution:
      "deep"                    -> KODA deep agent (file tools + shell + web)
      "module.path.ClassName"   -> custom Python class (must return a graph)
    """
    if spec == "deep":
        from koda.adapters.deep import build_deep_graph
        return build_deep_graph(model=model)

    # Custom Python class
    if "." in spec:
        module_path, class_name = spec.rsplit(".", 1)
        try:
            module = importlib.import_module(module_path)
            factory = getattr(module, class_name)
            return factory(model=model)
        except (ImportError, AttributeError) as exc:
            print(f"Error loading agent '{spec}': {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"Unknown agent: '{spec}'", file=sys.stderr)
    print(
        "Options:\n"
        "  deep                    KODA deep agent (default)\n"
        "  module.ClassName        Custom Python class",
        file=sys.stderr,
    )
    sys.exit(1)


def _setup_logging() -> str:
    """Write debug logs to logs/ inside the KODA project directory.

    Each session gets its own timestamped file:
        logs/session_2026-03-15_02-00-00.log
        logs/session_2026-03-15_02-11-00.log

    Returns the log file path.
    """
    import logging
    from datetime import datetime

    # logs/ lives next to the koda package (project root)
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
    # KODA's own logs: everything. Libraries: only warnings+errors.
    logging.getLogger("koda").setLevel(logging.DEBUG)
    logging.getLogger("deepagents_cli").setLevel(logging.WARNING)
    logging.getLogger("deepagents_cli.model_config").setLevel(logging.ERROR)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("langgraph").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    # Silence benign warnings that repeat every session
    logging.getLogger("deepagents_cli.widgets.chat_input").setLevel(logging.ERROR)
    logging.getLogger("deepagents_cli.widgets.message_store").setLevel(logging.ERROR)
    logging.getLogger("koda").info("=== KODA session started === log: %s", log_path)
    return log_path


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(
        prog="koda",
        description="KODA — Your AI companion in the terminal",
    )
    parser.add_argument(
        "--agent", "-a",
        default="deep",
        help="Agent backend: 'deep' (default) or 'module.ClassName'",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model to use. Format: provider:model (e.g. anthropic:claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--auto-approve", "-y",
        action="store_true",
        help="Auto-approve all tool calls",
    )
    args = parser.parse_args()

    _setup_logging()

    model = args.model or _default_model()
    agent = _load_agent(args.agent, model=model)

    import logging
    logging.getLogger("koda").info("Agent loaded: %s, model: %s", args.agent, model)

    # Run the TUI with the agent
    import asyncio
    asyncio.run(_run_app(agent=agent, auto_approve=args.auto_approve))


async def _run_app(*, agent, auto_approve: bool = False) -> None:
    import uuid
    from koda.app import KodaApp

    app = KodaApp(
        agent=agent,
        auto_approve=auto_approve,
        thread_id=uuid.uuid4().hex,
    )
    await app.run_async()


if __name__ == "__main__":
    main()
