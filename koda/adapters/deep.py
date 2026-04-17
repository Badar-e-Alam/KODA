"""
KODA default agent — a LangGraph react agent with KODA's own tools.

This replaces the old `deepagents.create_deep_agent(...)` path. No
`deepagents` import anywhere. Uses `langgraph.prebuilt.create_react_agent`
for the tool loop and wraps the result in `LangGraphAdapter`.
"""

from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from pathlib import Path

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from koda.adapters.langgraph import LangGraphAdapter
from koda.tools.fs import ALL_TOOLS as FS_TOOLS, set_workspace_root
from koda.tools.web import read_webpage, web_search


_SYSTEM_PROMPT_TEMPLATE = """\
You are KODA, a hands-on coding agent that lives in the terminal.
You are the user's teammate — not a chatbot. You write code, run commands, \
debug problems, and ship features alongside them.
Be direct, concise, and proactive. Take initiative when the path is clear, \
and ask when it isn't.

Environment:
- Date/time: {datetime_local} (UTC: {datetime_utc})
- OS: {os_info}
- Python: {python_version}
- Working directory: {cwd}

File tools use absolute paths starting with '/', rooted in the workspace \
directory ({workspace}). Use `ls` before `read_file`/`edit_file`. Always \
`read_file` a file before `edit_file`.

When given a task, break it into steps and work through them. Read existing \
code before editing. Run tests after making changes. Show your reasoning \
when the problem is non-trivial.

Tools available:
- ls, read_file, write_file, edit_file, glob, grep — filesystem
- execute — shell commands (run from workspace root)
- web_search, read_webpage — internet access

Safety:
- Never run destructive commands (rm -rf, git push --force, DROP TABLE) \
without asking first.
- Don't overwrite files without reading them first.
- Don't commit secrets, credentials, or .env files.
- If a command fails, diagnose the error before retrying.
"""


def _build_system_prompt(workspace: Path) -> str:
    now = datetime.now()
    utc = datetime.now(timezone.utc)
    return _SYSTEM_PROMPT_TEMPLATE.format(
        datetime_local=now.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
        datetime_utc=utc.strftime("%Y-%m-%d %H:%M:%S"),
        os_info=f"{platform.system()} {platform.release()}",
        python_version=platform.python_version(),
        cwd=os.getcwd(),
        workspace=workspace,
    )


def build_deep_graph(
    model: str = "anthropic:claude-sonnet-4-6",
    workspace: str | Path | None = None,
    system_prompt: str | None = None,
):
    """Build KODA's default LangGraph react agent. Returns the compiled graph.

    Exposed separately so the (Phase 1) TUI can still consume a raw graph
    while the adapter contract (Phase 2) matures.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    ws = Path(workspace) if workspace else Path(os.environ.get(
        "KODA_WORKSPACE", Path.cwd() / "agent_workspace"
    ))
    set_workspace_root(ws)

    chat_model = init_chat_model(model)
    return create_react_agent(
        model=chat_model,
        tools=[*FS_TOOLS, web_search, read_webpage],
        prompt=system_prompt or _build_system_prompt(ws.resolve()),
        checkpointer=MemorySaver(),
    )


def create_deep_adapter(
    model: str = "anthropic:claude-sonnet-4-6",
    workspace: str | Path | None = None,
    system_prompt: str | None = None,
    thread_id: str | None = None,
) -> LangGraphAdapter:
    """Build the default KODA agent and return it as a KodaAgent."""
    graph = build_deep_graph(model=model, workspace=workspace, system_prompt=system_prompt)
    return LangGraphAdapter(graph=graph, model=model, thread_id=thread_id)
