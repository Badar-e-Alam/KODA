"""Custom tools for KODA's deep agent.

Only the non-default tools live here. Filesystem + `execute` are provided by
deepagents' `FilesystemBackend`, so we do not re-declare them.

  * web_search   — Jina search API
  * read_webpage — Jina reader API (url -> markdown)
  * show_widget  — render a mermaid diagram to a standalone HTML file
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from langchain.tools import tool

# ── Jina web tools ───────────────────────────────────────────────────────

_JINA_SEARCH = "https://s.jina.ai/"
_JINA_READER = "https://r.jina.ai/"
_HTTP_TIMEOUT = 30.0


def _jina_headers(**extra: str) -> dict[str, str]:
    headers = {"Accept": "application/json", **extra}
    if key := os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _safe_public_url(url: str) -> str | None:
    """Return an error string if `url` is unsafe, else None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "Error: malformed URL"
    if parsed.scheme not in {"http", "https"}:
        return f"Error: only http/https allowed (got {parsed.scheme!r})"
    if not parsed.hostname:
        return "Error: URL missing host"
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return f"Error: cannot resolve host {parsed.hostname!r}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return f"Error: refusing to fetch internal address ({ip})"
    return None


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via Jina. Returns a text digest of the top results.

    Args:
        query: Search string.
        max_results: Number of hits to return (1..20).
    """
    max_results = max(1, min(20, max_results))
    headers = _jina_headers(**{"X-Return-Format": "text", "X-Max-Results": str(max_results)})
    try:
        resp = httpx.get(_JINA_SEARCH + quote(query), headers=headers, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"Error: web_search failed — {e}"
    return resp.text[:8000]


@tool
def read_webpage(url: str) -> str:
    """Fetch a URL and return its main content as markdown.

    Args:
        url: Full http(s) URL.
    """
    if err := _safe_public_url(url):
        return err
    headers = _jina_headers(**{
        "Accept": "text/markdown",
        "X-Return-Format": "markdown",
        "X-Skip-Images": "true",
        "X-Skip-Scripts": "true",
    })
    try:
        resp = httpx.get(_JINA_READER + url, headers=headers, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return f"Error: read_webpage failed — {e}"
    return resp.text[:12000]


# ── show_widget ──────────────────────────────────────────────────────────

_WIDGET_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; }}
  h1 {{ font-weight: 500; color: #222; }}
  .mermaid {{ background: #fff; border: 1px solid #eee; padding: 1rem; border-radius: 8px; }}
</style>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true, theme: "default" }});
</script>
</head>
<body>
<h1>{title}</h1>
<pre class="mermaid">
{diagram}
</pre>
</body>
</html>
"""

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-") or "widget"


def _widgets_dir() -> Path:
    root = Path(os.environ.get("KODA_WORKSPACE", Path.cwd() / "agent_workspace")).resolve()
    out = root / "widgets"
    out.mkdir(parents=True, exist_ok=True)
    return out


@tool
def show_widget(title: str, mermaid: str) -> str:
    """Render an interactive diagram to a standalone HTML file.

    Use this to draw flowcharts, sequence diagrams, Gantt charts, class
    diagrams, state machines, pie charts, etc. Pass valid Mermaid syntax
    — see https://mermaid.js.org for the grammar.

    Args:
        title: Short human-readable title for the diagram.
        mermaid: Valid Mermaid source (e.g., 'graph TD; A-->B; B-->C;').

    Returns:
        Absolute path to the rendered HTML file. Opening it in a browser
        renders the diagram client-side.
    """
    if not mermaid.strip():
        return "Error: mermaid source is empty"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = _widgets_dir() / f"{stamp}-{_slugify(title)}.html"
    try:
        out.write_text(
            _WIDGET_HTML.format(title=title, diagram=mermaid.strip()),
            encoding="utf-8",
        )
    except OSError as e:
        return f"Error: could not write widget — {e}"
    return f"Widget saved: {out}"


ALL_TOOLS = [web_search, read_webpage, show_widget]
