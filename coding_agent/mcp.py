"""MCP (Model Context Protocol) tool integration for the coding agent.

KODA consumes external MCP servers as LangGraph tools via the official
``langchain-mcp-adapters`` package (LangChain-authored, the recommended
path documented by both LangGraph and Deep Agents — see
https://docs.langchain.com/oss/python/deepagents/tools). The pattern is:

    async with MultiServerMCPClient(config) as client:
        tools = await client.get_tools()          # List[BaseTool]
        agent = create_deep_agent(..., tools=tools)

This module keeps all MCP wiring in one place: it loads the server config
from ``.mcp.json`` at the project root (the standard convention used by
Claude Code, Cursor, VS Code, and the Deep Agents repo itself), builds a
:class:`~langchain_mcp_adapters.client.MultiServerMCPClient`, and returns
the merged tool list. ``build_agent`` merges those tools with the built-in
``EXTRA_TOOLS`` before calling ``create_deep_agent``.

Everything is **optional and non-fatal**:

  * If ``langchain-mcp-adapters`` isn't installed → return ``[]`` (log once).
  * If no ``.mcp.json`` exists → return ``[]`` (silent — MCP is opt-in).
  * If a server is unreachable / times out → log a warning, skip it, and
    return whatever tools the *other* servers provided. One bad server never
    breaks the agent.

Context7 (https://github.com/upstash/context7) is the default server: it
exposes up-to-date, version-specific library documentation so the agent can
fetch fresh API docs before implementing with an unfamiliar library instead
of relying on (possibly stale) training data. Its two tools are
``resolve-library-id`` and ``query-docs``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger("koda.mcp")

# Default Context7 config — used when .mcp.json doesn't exist but the user
# has set CONTEXT7_API_KEY (or wants the anonymous, rate-limited endpoint).
# The API key is optional; without it Context7 still works but with lower
# rate limits. We never hardcode a key — it comes from the environment.
_DEFAULT_CONTEXT7_URL = "https://mcp.context7.com/mcp"


def _load_mcp_config(root: Path) -> dict[str, dict[str, Any]]:
    """Load MCP server definitions from ``<root>/.mcp.json``.

    The file uses the standard ``{"mcpServers": {name: spec, ...}}`` shape
    that Claude Code / Cursor / VS Code all share. Specs are translated to
    the form ``MultiServerMCPClient`` expects:

      * stdio  -> ``{"transport": "stdio", "command": ..., "args": [...]}``
      * http   -> ``{"transport": "http", "url": ..., "headers": {...}}``
      * sse    -> ``{"transport": "sse", "url": ..., "headers": {...}}``

    Returns ``{}`` (no servers) when the file is absent or unparseable, so
    the agent still starts cleanly without MCP configured.
    """
    config_path = root / ".mcp.json"
    if not config_path.is_file():
        # No config file — fall back to Context7 via env if an API key is set.
        api_key = os.environ.get("CONTEXT7_API_KEY", "").strip()
        if api_key:
            _log.info("no .mcp.json found; using Context7 with CONTEXT7_API_KEY from env")
            return {
                "context7": {
                    "transport": "http",
                    "url": _DEFAULT_CONTEXT7_URL,
                    "headers": {"CONTEXT7_API_KEY": api_key},
                }
            }
        _log.info("no .mcp.json and no CONTEXT7_API_KEY — MCP tools disabled")
        return {}

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("failed to parse %s: %s — MCP tools disabled", config_path, exc)
        return {}

    servers_raw = raw.get("mcpServers") or raw.get("mcp_servers") or {}
    if not isinstance(servers_raw, dict):
        _log.warning(".mcp.json 'mcpServers' is not an object — MCP tools disabled")
        return {}

    servers: dict[str, dict[str, Any]] = {}
    for name, spec in servers_raw.items():
        if not isinstance(spec, dict):
            _log.warning("skipping MCP server %r: spec is not an object", name)
            continue
        translated = _translate_spec(name, spec)
        if translated is not None:
            servers[name] = translated
    return servers


def _translate_spec(name: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a ``.mcp.json`` server spec to ``MultiServerMCPClient`` form.

    Recognizes both the ``"type": "http"|"sse"`` key (Claude Code style)
    and the bare ``"url"`` / ``"command"`` keys (Cursor style).
    """
    transport = spec.get("transport") or spec.get("type")

    # HTTP / streamable-HTTP
    if transport in ("http", "streamable-http", "streamable_http"):
        url = spec.get("url")
        if not url:
            _log.warning("skipping MCP server %r: http transport missing 'url'", name)
            return None
        out: dict[str, Any] = {"transport": "http", "url": url}
        if spec.get("headers"):
            out["headers"] = spec["headers"]
        return out

    # SSE (deprecated by the MCP spec but still supported)
    if transport == "sse":
        url = spec.get("url")
        if not url:
            _log.warning("skipping MCP server %r: sse transport missing 'url'", name)
            return None
        out = {"transport": "sse", "url": url}
        if spec.get("headers"):
            out["headers"] = spec["headers"]
        return out

    # stdio — spawn a local subprocess
    if transport == "stdio" or "command" in spec:
        command = spec.get("command")
        if not command:
            _log.warning("skipping MCP server %r: stdio transport missing 'command'", name)
            return None
        out = {
            "transport": "stdio",
            "command": command,
            "args": spec.get("args", []),
        }
        if spec.get("env"):
            out["env"] = spec["env"]
        return out

    _log.warning(
        "skipping MCP server %r: unrecognized spec (need 'url'/'command' or 'transport')", name
    )
    return None


async def load_mcp_tools(root: Path) -> list:
    """Load and return all tools from configured MCP servers.

    Returns a flat ``list`` of LangChain ``BaseTool`` objects ready to pass
    to ``create_deep_agent(tools=...)``. Returns ``[]`` when MCP is
    unavailable or unconfigured — the agent then runs with its built-in
    tools only. Never raises: every failure path degrades to ``[]`` so a
    missing/failed MCP server can't block agent startup.
    """
    servers = _load_mcp_config(root)
    if not servers:
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        _log.warning(
            "langchain-mcp-adapters not installed — MCP tools disabled. "
            "Install with: pip install langchain-mcp-adapters"
        )
        return []

    _log.info("loading MCP tools from servers: %s", ", ".join(sorted(servers)))
    client = MultiServerMCPClient(servers)
    try:
        tools = await client.get_tools()
    except Exception as exc:
        # A server may be unreachable, auth may fail, etc. Log and continue
        # with zero MCP tools rather than crashing the whole agent build.
        _log.warning("MCP get_tools() failed (%s: %s) — returning no MCP tools", type(exc).__name__, exc)
        return []

    _log.info("loaded %d MCP tool(s): %s", len(tools), ", ".join(t.name for t in tools))
    return list(tools)
