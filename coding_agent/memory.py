"""Project-local persistent memory for the coding agent.

Memory layout (lives in the project at ``<root>/.koda/memory/``)::

    .koda/memory/
      MEMORY.md         ← one-line index, always loaded into the system prompt
      user_role.md      ← per-fact files with frontmatter, loaded on-demand
      feedback_no_mocks.md
      project_q1_freeze.md

Each per-fact file uses a small YAML-ish frontmatter block::

    ---
    name: User role
    description: data scientist, observability focus
    type: user
    ---

    User is a data scientist currently focused on observability/logging.

Why a module-level store: the tool callables in ``tools.py`` are bare
functions registered with ``@function_tool``, so they cannot see ``self``
on the ``CodingAgent``. ``CodingAgent.__init__`` calls
:func:`set_memory_root` once per session to anchor the store, mirroring
how ``tools.set_approval_mode`` already works.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

MEMORY_DIRNAME = ".koda/memory"
INDEX_FILENAME = "MEMORY.md"
VALID_TYPES = ("user", "feedback", "project", "reference")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n+(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class MemoryEntry:
    name: str
    type: str
    description: str
    body: str
    path: Path


def _slugify(name: str) -> str:
    """Deterministic, filesystem-safe slug for a memory name."""
    s = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    return s or "memory"


def _format_frontmatter(name: str, type_: str, description: str) -> str:
    # One-line values only; we don't need full YAML escaping.
    name_safe = name.replace("\n", " ").strip()
    desc_safe = description.replace("\n", " ").strip()
    return (
        "---\n"
        f"name: {name_safe}\n"
        f"description: {desc_safe}\n"
        f"type: {type_}\n"
        "---\n"
    )


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Return ``(meta, body)``. ``meta`` is ``{}`` if no frontmatter present."""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    meta_block, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for line in meta_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


class MemoryStore:
    """File-backed memory keyed by slugified name.

    Operations are guarded by a single re-entrant lock — agent turns are
    serial today, but the compaction handoff (Phase 2.4) calls the model
    inline, so the store must stay consistent if a save lands while the
    index is being rewritten.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.dir = (self.project_root / MEMORY_DIRNAME).resolve()
        self.index_path = self.dir / INDEX_FILENAME
        self._lock = threading.RLock()

    # ── reads ──────────────────────────────────────────────────────────
    def exists(self) -> bool:
        return self.dir.exists()

    def read_index(self) -> str:
        """Return ``MEMORY.md`` contents (stripped) or '' if absent/empty."""
        try:
            return self.index_path.read_text().strip()
        except OSError:
            return ""

    def list_entries(self) -> list[MemoryEntry]:
        """Return all per-fact entries currently on disk (excluding the index)."""
        if not self.dir.exists():
            return []
        out: list[MemoryEntry] = []
        for path in sorted(self.dir.glob("*.md")):
            if path.name == INDEX_FILENAME:
                continue
            try:
                raw = path.read_text()
            except OSError:
                continue
            meta, body = _parse_frontmatter(raw)
            out.append(
                MemoryEntry(
                    name=meta.get("name", path.stem),
                    type=meta.get("type", "project"),
                    description=meta.get("description", ""),
                    body=body.strip(),
                    path=path,
                )
            )
        return out

    # ── writes ─────────────────────────────────────────────────────────
    def save(self, name: str, type_: str, description: str, content: str) -> Path:
        """Write a new memory file (overwrites if slug collides)."""
        if type_ not in VALID_TYPES:
            raise ValueError(f"type must be one of {VALID_TYPES}, got {type_!r}")
        if not name.strip():
            raise ValueError("name must be non-empty")
        if not content.strip():
            raise ValueError("content must be non-empty")
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"{_slugify(name)}.md"
            text = _format_frontmatter(name, type_, description) + "\n" + content.strip() + "\n"
            _atomic_write(path, text)
            self._rebuild_index_locked()
            return path

    def update(self, name: str, content: str) -> Path:
        """Replace the body of an existing memory; frontmatter is preserved."""
        if not content.strip():
            raise ValueError("content must be non-empty")
        with self._lock:
            path = self.dir / f"{_slugify(name)}.md"
            if not path.exists():
                raise FileNotFoundError(f"no memory named {name!r} (looked for {path})")
            raw = path.read_text()
            meta, _ = _parse_frontmatter(raw)
            if not meta:
                # No frontmatter? Treat the whole file as body and re-attach defaults.
                meta = {"name": name, "type": "project", "description": ""}
            text = (
                _format_frontmatter(meta.get("name", name), meta.get("type", "project"),
                                    meta.get("description", ""))
                + "\n"
                + content.strip()
                + "\n"
            )
            _atomic_write(path, text)
            self._rebuild_index_locked()
            return path

    def delete(self, name: str) -> bool:
        """Remove a memory file. Returns True if a file was removed."""
        with self._lock:
            path = self.dir / f"{_slugify(name)}.md"
            if not path.exists():
                return False
            path.unlink()
            self._rebuild_index_locked()
            return True

    # ── internal ───────────────────────────────────────────────────────
    def _rebuild_index_locked(self) -> None:
        """Rewrite ``MEMORY.md`` from the on-disk per-fact files.

        Index is the source of agent context — keep it small (one line per
        entry) so it fits in the system prompt without crowding out the
        actual base instructions.
        """
        entries = self.list_entries()
        if not entries:
            # Index gone if no entries — keeps an empty .koda/memory/ tidy.
            try:
                self.index_path.unlink()
            except FileNotFoundError:
                pass
            return
        lines = ["# KODA memory index\n"]
        # Group by type so the agent can scan: feedback first (rules), then
        # user (who they are), project (what's happening), reference (where to look).
        order = ("feedback", "user", "project", "reference")
        by_type: dict[str, list[MemoryEntry]] = {t: [] for t in order}
        for e in entries:
            by_type.setdefault(e.type, []).append(e)
        for t in order:
            bucket = by_type.get(t) or []
            if not bucket:
                continue
            lines.append(f"## {t}")
            for e in bucket:
                rel = e.path.name
                desc = e.description or "(no description)"
                lines.append(f"- [{e.name}]({rel}) — {desc}")
            lines.append("")  # blank line between groups
        text = "\n".join(lines).rstrip() + "\n"
        _atomic_write(self.index_path, text)


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a tmpfile + rename so partial writes don't poison the index."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# ── module-level singleton ─────────────────────────────────────────────
#
# Agent calls ``set_memory_root(project_root)`` once in __init__; tools and
# the composer fetch the singleton via ``get_memory_store()``. None until
# initialized — callers must handle that case so unit tests of pure
# helpers don't need to set up a project root.
_store: MemoryStore | None = None


def set_memory_root(project_root: Path) -> MemoryStore:
    global _store
    _store = MemoryStore(project_root)
    return _store


def get_memory_store() -> MemoryStore | None:
    return _store
