"""
Claude-Code-style session store.

Sessions live at ``~/.koda/projects/<project-slug>/<session-id>.jsonl`` —
mirroring Claude Code's ``~/.claude/projects/…`` layout:

  * project-slug: the working directory with every non-alphanumeric character
    replaced by ``-`` (so ``/Users/x/Desktop/KODA`` → ``-Users-x-Desktop-KODA``).
  * session-id:   a UUID; the filename IS the session id.
  * file format:  KODA's branchable ``SessionTree`` JSONL (append-only; richer
    than a flat log — it supports /tree branching).

``list_sessions`` powers the ``/resume`` picker; ``find_session`` resolves a
(possibly abbreviated) session id back to its file.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from koda.session import SessionTree


def project_slug(cwd: str | Path | None = None) -> str:
    """Claude-style slug: non-alphanumerics → '-' (keeps the leading dash)."""
    raw = str(Path(cwd).resolve() if cwd else Path.cwd().resolve())
    return re.sub(r"[^A-Za-z0-9]", "-", raw)


def projects_dir(cwd: str | Path | None = None) -> Path:
    d = Path.home() / ".koda" / "projects" / project_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_session(cwd: str | Path | None = None) -> SessionTree:
    """Create a fresh session whose filename == session_id (a UUID)."""
    sid = str(uuid.uuid4())
    path = projects_dir(cwd) / f"{sid}.jsonl"
    return SessionTree(path=path, session_id=sid)


def find_session(session_id: str, cwd: str | Path | None = None) -> Path | None:
    """Resolve a full or abbreviated session id to its file (prefix match)."""
    frag = session_id.strip().lower()
    if not frag:
        return None
    candidates = [
        p for p in projects_dir(cwd).glob("*.jsonl")
        if p.stem.lower().startswith(frag)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    # Ambiguous prefix — newest wins only on an exact match.
    exact = [p for p in candidates if p.stem.lower() == frag]
    return exact[0] if exact else None


def load_session(path: Path) -> SessionTree:
    """Open an existing session file for appending (resume)."""
    return SessionTree(path=path)


@dataclass(frozen=True)
class SessionInfo:
    id: str
    path: str
    started: str        # ISO timestamp of the header
    modified: float     # file mtime (sort key)
    messages: int
    preview: str        # first user message, truncated


def list_sessions(cwd: str | Path | None = None, limit: int = 20) -> list[SessionInfo]:
    """Recent sessions for this project, newest first, empty ones skipped."""
    out: list[SessionInfo] = []
    files = sorted(
        projects_dir(cwd).glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in files:
        if len(out) >= limit:
            break
        started = ""
        messages = 0
        preview = ""
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") == "header" and not started:
                    started = str(e.get("timestamp", ""))[:19]
                elif e.get("type") == "message":
                    messages += 1
                    if not preview and e.get("role") == "user":
                        preview = " ".join(str(e.get("content", "")).split())[:70]
        except OSError:
            continue
        if messages == 0:
            continue  # header-only husks (aborted launches) aren't resumable
        out.append(
            SessionInfo(
                id=p.stem,
                path=str(p),
                started=started,
                modified=p.stat().st_mtime,
                messages=messages,
                preview=preview,
            )
        )
    return out
