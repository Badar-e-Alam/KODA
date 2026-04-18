"""
Web tools (Jina-backed search + webpage reader).

These are the only non-filesystem tools KODA ships by default. Both honor
the `JINA_API_KEY` env var for higher rate limits.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import quote, urlparse

import httpx
from langchain.tools import tool


def _validate_public_url(url: str) -> str | None:
    """Return an error string if url is not a safe public http(s) URL, else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "Error: invalid URL"
    if parsed.scheme not in ("http", "https"):
        return f"Error: only http/https URLs allowed (got {parsed.scheme!r})"
    host = parsed.hostname
    if not host:
        return "Error: URL missing host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"Error: cannot resolve host {host!r}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return f"Error: refusing to fetch private/internal address ({ip})"
    return None


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for documentation, articles, and current information.

    Args:
        query: Search query string.
        max_results: Max hits to return (1-20).
    """
    headers: dict[str, str] = {
        "Accept": "application/json",
        "X-Return-Format": "text",
        "X-Max-Results": str(max_results),
    }
    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.get(f"https://s.jina.ai/{quote(query)}", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text[:8000]


@tool
def read_webpage(url: str) -> str:
    """Fetch a webpage and return its main content as markdown.

    Args:
        url: Full http(s) URL.
    """
    err = _validate_public_url(url)
    if err:
        return err
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
