"""
Deep Agent backend for KODA.

Creates a LangGraph agent with:
- File operations (read, write, edit, ls) via FilesystemBackend
- Shell execution rooted in the project directory
- Web search & webpage reading via Jina
- Multi-model support (Anthropic, OpenAI, Ollama, Google)

Returns a compiled LangGraph graph compatible with DeepAgentsApp.
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from langchain.tools import tool
from deepagents.backends import FilesystemBackend

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

When given a task, break it into steps and work through them. Read existing \
code before editing. Run tests after making changes. Show your reasoning \
when the problem is non-trivial.

Your file tools (read, write, edit, ls) are rooted in the agent_workspace \
directory. All files you create go there. To read files outside the \
workspace, use shell commands (cat, type, etc.).
Never ask the user for file paths — use ls/shell to explore and \
read_file to inspect. Figure it out yourself.

You have access to tools for web search, reading webpages, running shell \
commands, and file operations. Use the right tool for each step. \
When you don't know what's available, run ls first.

Packages:
- If a task requires a package that isn't installed, install it using the \
shell tool (pip install, npm install, etc.).
- Check the project's package manager first (requirements.txt, pyproject.toml, \
package.json) and use the same one.

Safety:
- Never run destructive commands (rm -rf, git push --force, DROP TABLE) \
without asking first.
- Don't overwrite files without reading them first.
- Don't commit secrets, credentials, or .env files.
- If a command fails, diagnose the error before retrying.
"""


def _build_system_prompt() -> str:
    """Build system prompt with current date/time and environment info."""
    now = datetime.now()
    utc = datetime.now(timezone.utc)
    return _SYSTEM_PROMPT_TEMPLATE.format(
        datetime_local=now.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
        datetime_utc=utc.strftime("%Y-%m-%d %H:%M:%S"),
        os_info=f"{platform.system()} {platform.release()}",
        python_version=platform.python_version(),
        cwd=os.getcwd(),
    )


# ── Tools ──────────────────────────────────────────────────────────────

@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for current information, documentation, articles, and more.

    Args:
        query: The search query
        max_results: Maximum number of results to return
    """
    import httpx
    from urllib.parse import quote

    headers: dict[str, str] = {
        "Accept": "application/json",
        "X-Return-Format": "text",
        "X-Max-Results": str(max_results),
    }
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.get(
        f"https://s.jina.ai/{quote(query)}",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text[:8000]


@tool
def read_webpage(url: str) -> str:
    """Read and extract the main content from a webpage URL.

    Args:
        url: The full URL to read
    """
    import httpx

    headers: dict[str, str] = {
        "Accept": "text/markdown",
        "X-Return-Format": "markdown",
        "X-No-Cache": "true",
        "X-Skip-Images": "true",
        "X-Skip-Links": "true",
        "X-Skip-Scripts": "true",
    }
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.get(f"https://r.jina.ai/{url}", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text[:12000]


# ── Agent Factory ──────────────────────────────────────────────────────

def create_koda_agent(
    model: str = "anthropic:claude-sonnet-4-6",
    system_prompt: str | None = None,
):
    """
    Create a LangGraph deep agent for KODA.

    Returns a compiled Pregel graph compatible with DeepAgentsApp.
    """
    from deepagents import create_deep_agent
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    from langgraph.checkpoint.memory import MemorySaver

    return create_deep_agent(
        model=model,
        tools=[web_search, read_webpage],
        backend=FilesystemBackend(
            root_dir="C:/Users/badar/Desktop/KODA/agent_workspace",
            virtual_mode=True,
        ),
        system_prompt=system_prompt or _build_system_prompt(),
        checkpointer=MemorySaver(),
    )
