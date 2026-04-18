"""Custom tools for KODA's deep agent.

Only the non-default tools live here. Filesystem + `execute` are provided by
deepagents' `FilesystemBackend`, so we do not re-declare them.

  * web_search       — Jina search API
  * read_webpage     — Jina reader API (url -> markdown)
  * show_widget      — render a mermaid diagram to a standalone HTML file
  * write_report_pdf — structured-input → professionally styled PDF report
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


# ── write_report_pdf ────────────────────────────────────────────────
#
# Produces a professionally-styled PDF from structured input instead of
# letting the agent hand-roll its own layout script each run. One code
# path, one look-and-feel, across every report the agent writes.

_PDF_HEADER_BG = (30, 64, 105)   # deep blue header band
_PDF_HEADER_FG = (255, 255, 255) # white text on header
_PDF_ROW_ALT_BG = (240, 244, 248) # zebra-stripe accent
_PDF_BORDER = (210, 216, 224)    # table gridline
_PDF_MARGIN = 12  # mm


_PDF_UNICODE_MAP = {
    "—": "-", "–": "-", "−": "-",
    "…": "...",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "•": "*", "·": "-", "→": "->", "←": "<-", "↳": "->",
    "✓": "v", "✗": "x",
}


def _latin1_safe(s: str) -> str:
    """Replace common Unicode glyphs with ASCII equivalents, then drop
    anything still outside Latin-1. fpdf2's default font is Latin-1 only."""
    for src, dst in _PDF_UNICODE_MAP.items():
        s = s.replace(src, dst)
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _wrap(value: object, max_chars: int = 120) -> str:
    s = "" if value is None else str(value)
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "..."
    return _latin1_safe(s)


def _reports_dir() -> Path:
    root = Path(os.environ.get("KODA_WORKSPACE", Path.cwd() / "agent_workspace")).resolve()
    d = root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


@tool
def write_report_pdf(
    path: str,
    title: str,
    rows: list[list[object]],
    columns: list[str] | None = None,
    subtitle: str | None = None,
    notes: str | None = None,
) -> str:
    """Render a polished PDF report from structured data.

    Produces a PDF with: title band, optional subtitle + generation
    timestamp, styled table (colored header, zebra stripes, auto column
    widths), optional notes section, and page-number footer.

    Prefer this over writing your own PDF-generation script — the styling
    is fixed and consistent. Feed it a list of rows; it handles the rest.

    Args:
        path:     Output path. Relative paths land under /reports/.
        title:    Headline shown in the title band.
        rows:     List of row-lists; each row is a list of cell values
                  (strings, numbers, whatever — they're stringified).
        columns:  Column header labels. If omitted, first row is used as
                  header.
        subtitle: Optional one-liner under the title.
        notes:    Optional paragraph rendered after the table.

    Returns:
        Absolute path to the written PDF, or an error string.
    """
    try:
        from fpdf import FPDF
    except ImportError:
        return "Error: fpdf2 not installed. Run: pip install fpdf2"

    if not rows:
        return "Error: rows is empty — nothing to render"
    if columns is None:
        columns = [str(c) for c in rows[0]]
        data_rows = rows[1:]
    else:
        columns = [str(c) for c in columns]
        data_rows = rows
    if not data_rows:
        return "Error: no data rows (after extracting header)"

    # Resolve output path — anything starting with '/' is workspace-relative.
    p = Path(path.lstrip("/")) if path.startswith("/") else Path(path)
    if not p.is_absolute():
        p = _reports_dir() / p.name if "/" not in path.strip("/") else _reports_dir().parent / p
    p = p.resolve()
    p.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=_PDF_MARGIN + 3)
    pdf.set_margins(_PDF_MARGIN, _PDF_MARGIN, _PDF_MARGIN)
    pdf.add_page()
    page_w = pdf.w - 2 * _PDF_MARGIN

    # ── Title band ────────────────────────────────────────────────
    pdf.set_fill_color(*_PDF_HEADER_BG)
    pdf.set_text_color(*_PDF_HEADER_FG)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(page_w, 12, _wrap(title, 120), border=0, ln=1, align="L", fill=True)

    pdf.set_text_color(90, 90, 90)
    pdf.set_font("Helvetica", "", 9)
    meta = f"Generated {time.strftime('%Y-%m-%d %H:%M')}"
    if subtitle:
        pdf.cell(page_w, 6, _wrap(subtitle, 180), ln=1)
    pdf.cell(page_w, 5, meta, ln=1)
    pdf.ln(3)

    # ── Column widths: natural-log-ish scaling from content lengths ──
    sample = data_rows[:60]
    col_lens: list[int] = [len(c) for c in columns]
    for row in sample:
        for i, cell in enumerate(row):
            if i < len(col_lens):
                col_lens[i] = max(col_lens[i], min(60, len(str(cell))))
    total = sum(col_lens) or 1
    col_widths = [max(18.0, page_w * (cl / total)) for cl in col_lens]
    # Scale down if we overflow the page width
    scale = page_w / sum(col_widths) if sum(col_widths) > page_w else 1.0
    col_widths = [w * scale for w in col_widths]

    # ── Table header ─────────────────────────────────────────────
    pdf.set_fill_color(*_PDF_HEADER_BG)
    pdf.set_text_color(*_PDF_HEADER_FG)
    pdf.set_font("Helvetica", "B", 10)
    for label, w in zip(columns, col_widths):
        pdf.cell(w, 9, _wrap(label, 60), border=0, align="L", fill=True)
    pdf.ln(9)

    # ── Table body ───────────────────────────────────────────────
    pdf.set_text_color(30, 30, 30)
    pdf.set_draw_color(*_PDF_BORDER)
    pdf.set_font("Helvetica", "", 9)
    for i, row in enumerate(data_rows):
        fill = i % 2 == 0
        if fill:
            pdf.set_fill_color(*_PDF_ROW_ALT_BG)
        # Use multi_cell-equivalent via cell with wrap truncation (fpdf2 multi-col)
        for j, w in enumerate(col_widths):
            cell_val = _wrap(row[j] if j < len(row) else "", 60)
            pdf.cell(w, 7, cell_val, border="B", align="L", fill=fill)
        pdf.ln(7)

    # ── Notes ────────────────────────────────────────────────────
    if notes:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(page_w, 6, "Notes", ln=1)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(60, 60, 60)
        pdf.multi_cell(page_w, 5, _latin1_safe(notes.strip()))

    # ── Footer (page numbers) ────────────────────────────────────
    pdf.alias_nb_pages()  # replaces {nb} with total page count
    # fpdf2's default footer hook isn't defined on this instance, so
    # write a footer on every page we've written:
    total_pages = pdf.page_no()
    for n in range(1, total_pages + 1):
        pdf.page = n
        pdf.set_y(-10)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(130, 130, 130)
        pdf.cell(0, 6, f"Page {n} of {total_pages}", align="C")

    try:
        pdf.output(str(p))
    except OSError as e:
        return f"Error writing PDF: {e}"
    return f"PDF written: {p}"


ALL_TOOLS = [web_search, read_webpage, show_widget, write_report_pdf]
