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


def _wants_ollama_cloud(name: str) -> bool:
    """Decide whether an ``ollama`` model should be routed to Ollama Cloud.

    Routing is **opt-in only** so a global ``OLLAMA_API_KEY`` never silently
    hijacks a user's local ``ollama serve`` (which would 404 on the cloud
    catalog and bill cloud usage). Two explicit signals enable it:

    1. A cloud-tagged model name — Ollama Cloud models carry a ``:cloud`` or
       ``-cloud`` suffix (e.g. ``gpt-oss:120b-cloud``, ``glm-4.6:cloud``).
    2. ``OLLAMA_USE_CLOUD`` set truthy — forces *every* ``ollama:`` model to
       cloud (for users with no local daemon at all).

    Anything else stays on the local daemon path.
    """
    if name.endswith(("-cloud", ":cloud")):
        return True
    flag = os.environ.get("OLLAMA_USE_CLOUD", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _build_chat_model(model: str):
    """Build the LangChain chat model for ``provider:name``.

    Special-cases **Ollama Cloud**: ``init_chat_model("ollama:…")`` builds a
    ``ChatOllama`` that always talks to ``http://localhost:11434`` with no
    auth header. A cloud-only user therefore gets ``ConnectError`` on every
    turn — and because the request never reaches a provider, no
    ``usage_metadata`` is ever emitted, so the status bar's token counters
    stay at ``0``. When cloud routing is explicitly requested (see
    ``_wants_ollama_cloud``) and a key is present, point ``ChatOllama`` at
    Ollama Cloud and forward the bearer token. This is what makes both the
    response *and* the token usage work for cloud ``ollama:`` models.

    Every other provider falls through to plain ``init_chat_model``.
    """
    provider, _, name = model.partition(":")
    if provider == "ollama" and name and _wants_ollama_cloud(name):
        api_key = os.environ.get("OLLAMA_API_KEY")
        if api_key:
            from langchain_ollama import ChatOllama

            cloud_host = os.environ.get("OLLAMA_CLOUD_HOST", "https://ollama.com")
            return ChatOllama(
                model=name,
                base_url=cloud_host.rstrip("/"),
                client_kwargs={"headers": {"Authorization": f"Bearer {api_key}"}},
            )
    return init_chat_model(model)


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

    chat_model = _build_chat_model(model)
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
