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
        model=model,
        tools=list(ALL_TOOLS),
        system_prompt=build_prompt(ws),
        backend=FilesystemBackend(root_dir=str(ws), virtual_mode=False),
        skills=skill_paths or None,
        memory=[AGENTS_MD_PATH],
        checkpointer=MemorySaver(),
    )


__all__ = ["build", "DEFAULT_MODEL"]
