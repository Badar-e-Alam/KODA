"""
Example: run KODA with a `deepagents`-powered backend.

Shows how to plug a third-party agent framework into KODA via the
`--agent` flag. Requires `deepagents` installed:

    pip install deepagents

Launch:

    koda --agent examples.deepagents_backend.build --model openai:gpt-5-nano

The `--agent` resolver in `koda/__main__.py` will:
  1. Import this module, call `build(model=...)`.
  2. Receive a compiled LangGraph graph.
  3. Auto-wrap it in `LangGraphAdapter` so it speaks the `KodaAgent` protocol.

No code changes needed inside KODA — the adapter contract handles it.
"""

from __future__ import annotations

import os
from pathlib import Path


def build(model: str = "anthropic:claude-sonnet-4-6"):
    """Factory: return a LangGraph graph produced by `deepagents`."""
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend
    from langgraph.checkpoint.memory import MemorySaver

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    workspace = Path(os.environ.get("KODA_WORKSPACE", Path.cwd() / "agent_workspace"))
    workspace.mkdir(parents=True, exist_ok=True)

    return create_deep_agent(
        model=model,
        tools=[],
        backend=FilesystemBackend(root_dir=str(workspace), virtual_mode=True),
        system_prompt=(
            "You are KODA running on the deepagents backend. "
            "Use the filesystem and shell tools to help the user code."
        ),
        checkpointer=MemorySaver(),
    )
