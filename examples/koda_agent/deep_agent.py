"""Assemble KODA's deep agent.

This module is the only thing the outside world imports. It glues together
the prompt, the custom tools, the default skills, and the AGENTS.md memory
file into a compiled LangGraph graph that KODA can run.
"""

from __future__ import annotations

import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

from .prompt import build_prompt
from .skills import discover_skills
from .tools import ALL_TOOLS

DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"
AGENTS_MD_PATH = "/AGENTS.md"


def _resolve_workspace(workspace: str | Path | None) -> Path:
    if workspace:
        ws = Path(workspace)
    else:
        ws = Path(os.environ.get("KODA_WORKSPACE", Path.cwd() / "agent_workspace"))
    ws = ws.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    os.environ["KODA_WORKSPACE"] = str(ws)  # so tools.py sees the same root
    return ws


def _ensure_agents_md(workspace: Path) -> None:
    agents_md = workspace / "AGENTS.md"
    if agents_md.exists():
        return
    agents_md.write_text(
        "# AGENTS.md\n\n"
        "Persistent notes for KODA. Update this file when the user teaches\n"
        "you something worth carrying between sessions: preferences, project\n"
        "constraints, long-lived context.\n\n"
        "## User preferences\n\n(none yet)\n\n"
        "## Project context\n\n(none yet)\n",
        encoding="utf-8",
    )


def _resolve_model(model: str):
    """Return a chat-model instance ready for ``create_deep_agent``.

    Special case: ``ollama:*`` with ``OLLAMA_API_KEY`` set and no explicit
    ``OLLAMA_HOST`` → route to Ollama Cloud (``https://ollama.com``) with
    the required ``Authorization: Bearer`` header. For any other provider
    (openai, anthropic, google, local ollama, ...) we pass the string
    through to ``init_chat_model`` unchanged.
    """
    if not model.lower().startswith("ollama:"):
        return model  # create_deep_agent will call init_chat_model itself

    key = os.environ.get("OLLAMA_API_KEY")
    host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL")
    if not key or host:
        return model  # local daemon (or user-pinned host) — default path

    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        return model  # graceful fallback; init_chat_model will error clearly

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
    """Factory consumed by `koda --agent examples.koda_agent`.

    Args:
        model:      LangChain model string, e.g. 'anthropic:claude-sonnet-4-6'.
        workspace:  Root dir the agent is jailed to. Defaults to
                    $KODA_WORKSPACE or ./agent_workspace.

    Skills are discovered from ``<workspace>/skills/*/SKILL.md`` — drop a
    new skill directory there and it's picked up on the next build. No
    network I/O, no downloads.

    Returns:
        Compiled LangGraph graph. KODA's `LangGraphAdapter` wraps it
        automatically.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    ws = _resolve_workspace(workspace)
    _ensure_agents_md(ws)

    skill_paths = discover_skills(ws)

    return create_deep_agent(
        model=_resolve_model(model),
        tools=list(ALL_TOOLS),
        system_prompt=build_prompt(ws),
        # virtual_mode=True: "/" is the workspace root, so skill paths like
        # "/skills/docx/" resolve to <ws>/skills/docx/. With virtual_mode=False
        # on Windows, "/" resolved to the C:\ drive root — the agent saw
        # `/skills` as missing and SkillsMiddleware loaded zero skills.
        backend=FilesystemBackend(root_dir=str(ws), virtual_mode=True),
        skills=skill_paths or None,
        memory=[AGENTS_MD_PATH],
        checkpointer=MemorySaver(),
    )


__all__ = ["build", "DEFAULT_MODEL"]
