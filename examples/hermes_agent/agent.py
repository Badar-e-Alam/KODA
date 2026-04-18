"""Hermes-style KODA backend.

Second reference backend alongside ``examples.koda_agent``. Demonstrates
that KODA's agent contract is framework-agnostic:

  * ``koda_agent``   — deepagents middleware stack, Anthropic skill format
  * ``hermes_agent`` — plain LangGraph react-agent, Hermes self-improving
                       loop (author_skill tool + AGENTS.md memory)

Both share:
  * the same ``agent_workspace/`` on disk
  * the same ``/skills/`` directory (agentskills.io format)
  * the same web + widget tools (``examples.koda_agent.tools``)

The Hermes example talks to the model through ``create_react_agent``
directly — no deepagents middleware. That keeps the dependency surface
small and makes the code easier to read end-to-end.
"""

from __future__ import annotations

import os
from pathlib import Path

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

# Reuse everything from koda_agent that isn't framework-specific — prompt,
# tools, and skill discovery — so the only thing that differs between the
# two backends is the agent framework under the hood.
from examples.koda_agent.prompt import build_prompt
from examples.koda_agent.skills import discover_skills
from examples.koda_agent.tools import ALL_TOOLS as WEB_TOOLS
from koda.tools.fs import ALL_TOOLS as FS_TOOLS, set_workspace_root

from .skill_author import author_skill

DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"
AGENTS_MD_NAME = "AGENTS.md"


def _resolve_workspace(workspace: str | Path | None) -> Path:
    if workspace:
        ws = Path(workspace)
    else:
        ws = Path(os.environ.get("KODA_WORKSPACE", Path.cwd() / "agent_workspace"))
    ws = ws.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    os.environ["KODA_WORKSPACE"] = str(ws)
    return ws


def _load_memory(workspace: Path) -> str:
    """Return the AGENTS.md contents as a preamble string (empty if absent).

    The plain react-agent has no memory middleware, so we splice the
    file into the system prompt ourselves. That matches deepagents'
    ``memory=[...]`` behavior closely enough for the example.
    """
    path = workspace / AGENTS_MD_NAME
    if not path.is_file():
        return ""
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not body:
        return ""
    return f"\n\n### Persistent memory ({AGENTS_MD_NAME})\n\n{body}\n"


def _resolve_model(model: str):
    """Route ``ollama:*`` models with ``OLLAMA_API_KEY`` set to Ollama Cloud.

    Same trick as koda_agent — kept inline so the two backends can
    evolve independently without a cross-import.
    """
    if not model.lower().startswith("ollama:"):
        return init_chat_model(model)
    key = os.environ.get("OLLAMA_API_KEY")
    host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL")
    if not key or host:
        return init_chat_model(model)
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        return init_chat_model(model)
    _, _, name = model.partition(":")
    return ChatOllama(
        model=name,
        base_url="https://ollama.com",
        client_kwargs={"headers": {"Authorization": f"Bearer {key}"}},
    )


def build(
    model: str = DEFAULT_MODEL,
    workspace: str | Path | None = None,
):
    """Factory consumed by ``koda --agent examples.hermes_agent``.

    Args:
        model:      LangChain model string (provider:name).
        workspace:  Workspace root. Defaults to ``$KODA_WORKSPACE`` or
                    ``./agent_workspace`` — same default as koda_agent so
                    both backends see the same AGENTS.md, skills, and
                    widget outputs.

    Returns:
        Compiled LangGraph graph. KODA wraps it in ``LangGraphAdapter``
        automatically.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    ws = _resolve_workspace(workspace)
    set_workspace_root(ws)

    skill_paths = discover_skills(ws)
    skill_hint = ""
    if skill_paths:
        skill_hint = (
            "\n\n### Available skills\n\n"
            + "\n".join(f"- `{p}`" for p in skill_paths)
            + "\n"
        )

    prompt = build_prompt(ws) + _load_memory(ws) + skill_hint

    tools = [*FS_TOOLS, *WEB_TOOLS, author_skill]

    return create_react_agent(
        model=_resolve_model(model),
        tools=tools,
        prompt=prompt,
        checkpointer=MemorySaver(),
    )


__all__ = ["build", "DEFAULT_MODEL"]
