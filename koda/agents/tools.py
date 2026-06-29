"""
KODA Tool Registry — LangChain-style @tool decorator with automatic argument parsing.

Usage:
    from koda.agents.tools import tool, get_all_tools

    @tool
    def my_tool(query: str, limit: int = 10) -> str:
        '''Search for something.

        Args:
            query: What to search for
            limit: Max results to return
        '''
        return "result"

    # Get all registered tools as plain functions (for deepagents)
    tools = get_all_tools()
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import quote
from langchain.tools import tool

@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for current information, documentation, articles, and more.
    Use this when you need to find up-to-date information or research a topic.

    Args:
        query: The search query
        max_results: Maximum number of results to return
    """
    import httpx

    headers: dict[str, str] = {
        "Accept": "application/json",
        "X-Return-Format": "text",
    }
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.get(
        f"https://s.jina.ai/{quote(query)}",
        headers=headers,
        params={"count": str(max_results)},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text[:8000]

@tool
def read_webpage(url: str) -> str:
    """Read and extract the main content from a webpage URL.
    Returns clean markdown text. Use for reading docs, articles, references.

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
    resp = httpx.get(
        f"https://r.jina.ai/{url}",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text[:12000]


@tool
def shell(command: str, timeout_seconds: int = 120) -> str:
    """Execute a shell command and return its output. Use for running tests, builds, git commands, installing packages, and more.

    Args:
        command: The full shell command to execute
        timeout_seconds: Maximum time to allow the command to run before killing it
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            text=True,
        )
        return result.stdout[:8000]
    except subprocess.CalledProcessError as e:
        return f"Command failed with exit code {e.returncode}:\n{e.output[:8000]}"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_seconds} seconds."



if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    search = "www.python.org"
    print(read_webpage.invoke({"url": search}))