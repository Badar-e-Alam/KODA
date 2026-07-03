"""
Session tree for KODA.

Sessions are stored as JSONL files with a tree structure (id/parentId),
enabling in-place branching without creating new files.

Inspired by PI coding agent's /tree feature.

Each line in the JSONL file is a SessionEntry with:
  - id:        8-char hex identifier
  - parent_id: reference to parent entry (null for root)
  - timestamp: ISO datetime
  - type:      header | message | branch_summary | compaction
  - role:      user | assistant | system | None
  - content:   the message text
  - metadata:  extra data (usage stats, etc.)
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


def _short_id() -> str:
    """Generate an 8-char hex ID."""
    return uuid.uuid4().hex[:8]


# ── Entry ─────────────────────────────────────────────────────────────


@dataclass
class SessionEntry:
    """A single node in the session tree."""

    id: str
    parent_id: str | None
    timestamp: str
    type: str                          # header, message, branch_summary, compaction
    role: str | None                   # user, assistant, system, None
    content: str
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> SessionEntry:
        data = json.loads(raw)
        return cls(**data)


# ── Tree ──────────────────────────────────────────────────────────────


class SessionTree:
    """
    Tree-structured session with JSONL persistence.

    Every message is a node. When you navigate to a past node and send
    a new message, a branch is created automatically. The full history
    is preserved in a single file.
    """

    def __init__(self, path: Path | None = None, session_id: str | None = None) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._children: dict[str | None, list[str]] = {}
        self._leaf_id: str | None = None
        self._path = path
        # Caller-supplied id lets the filename and session_id match
        # (Claude-Code-style ~/.koda/projects/<slug>/<session-id>.jsonl).
        self._session_id = session_id or uuid.uuid4().hex[:12]

        if path and path.exists():
            self._load()
        elif path:
            path.parent.mkdir(parents=True, exist_ok=True)
            header = SessionEntry(
                id=_short_id(),
                parent_id=None,
                timestamp=datetime.now().isoformat(),
                type="header",
                role=None,
                content="",
                metadata={"version": "v1", "session_id": self._session_id},
            )
            self._add_entry(header)
            self._leaf_id = header.id
            self._save_entry(header)

    # ── Properties ────────────────────────────────────────────────────

    @property
    def leaf_id(self) -> str | None:
        return self._leaf_id

    @property
    def entries(self) -> dict[str, SessionEntry]:
        return self._entries

    @property
    def session_id(self) -> str:
        return self._session_id

    # ── Mutators ──────────────────────────────────────────────────────

    def add_message(
        self, role: str, content: str, metadata: dict | None = None
    ) -> SessionEntry:
        """Append a message as a child of the current leaf."""
        entry = SessionEntry(
            id=_short_id(),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            type="message",
            role=role,
            content=content,
            metadata=metadata or {},
        )
        self._add_entry(entry)
        self._leaf_id = entry.id
        self._save_entry(entry)
        return entry

    def add_branch_summary(
        self, summary: str, from_leaf_id: str
    ) -> SessionEntry:
        """Record a summary of an abandoned branch."""
        entry = SessionEntry(
            id=_short_id(),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            type="branch_summary",
            role=None,
            content=summary,
            metadata={"from_leaf_id": from_leaf_id},
        )
        self._add_entry(entry)
        self._save_entry(entry)
        return entry

    def add_compaction(
        self,
        summary: str,
        source_message_count: int,
        kept_message_count: int = 1,
    ) -> SessionEntry:
        """
        Add a compaction node that summarizes earlier context.

        When a compaction node is on the active path, agent history generation
        can collapse all prior messages into this summary.
        """
        entry = SessionEntry(
            id=_short_id(),
            parent_id=self._leaf_id,
            timestamp=datetime.now().isoformat(),
            type="compaction",
            role=None,
            content=summary,
            metadata={
                "source_message_count": source_message_count,
                "kept_message_count": kept_message_count,
            },
        )
        self._add_entry(entry)
        self._leaf_id = entry.id
        self._save_entry(entry)
        return entry

    def navigate_to(self, target_id: str) -> SessionEntry | None:
        """Move the active leaf to a different node."""
        if target_id not in self._entries:
            return None
        self._leaf_id = target_id
        return self._entries[target_id]

    # ── Queries ───────────────────────────────────────────────────────

    def get_active_path(self) -> list[SessionEntry]:
        """Walk from root to current leaf."""
        path: list[SessionEntry] = []
        current_id = self._leaf_id
        while current_id is not None:
            entry = self._entries.get(current_id)
            if entry is None:
                break
            path.append(entry)
            current_id = entry.parent_id
        path.reverse()
        return path

    def get_children(self, parent_id: str | None) -> list[SessionEntry]:
        child_ids = self._children.get(parent_id, [])
        return [self._entries[cid] for cid in child_ids if cid in self._entries]

    def get_branch_count(self, entry_id: str) -> int:
        return len(self._children.get(entry_id, []))

    def is_on_active_path(self, entry_id: str) -> bool:
        return entry_id in {e.id for e in self.get_active_path()}

    def has_branches(self) -> bool:
        return any(len(ch) > 1 for ch in self._children.values())

    def message_count(self) -> int:
        return sum(1 for e in self._entries.values() if e.type == "message")

    def get_messages_for_agent(self) -> list[dict[str, str]]:
        """
        Active path as [{role, content}, ...] for the agent backend.

        A `compaction` entry acts as a context reset point: all prior messages
        are replaced by a single system summary represented by the compaction.
        """
        messages: list[dict[str, str]] = []
        for e in self.get_active_path():
            if e.type == "compaction":
                messages = [{"role": "system", "content": e.content}]
                continue
            if e.type == "message" and e.role in ("user", "assistant"):
                messages.append({"role": e.role, "content": e.content})
        return messages

    def get_common_ancestor(self, id_a: str, id_b: str) -> str | None:
        """Find the nearest common ancestor of two nodes."""
        ancestors_a: set[str] = set()
        cur = id_a
        while cur is not None:
            ancestors_a.add(cur)
            entry = self._entries.get(cur)
            cur = entry.parent_id if entry else None

        cur = id_b
        while cur is not None:
            if cur in ancestors_a:
                return cur
            entry = self._entries.get(cur)
            cur = entry.parent_id if entry else None
        return None

    def get_abandoned_path(
        self, old_leaf_id: str, new_leaf_id: str
    ) -> list[SessionEntry]:
        """Entries on the old branch back to the common ancestor."""
        ancestor_id = self.get_common_ancestor(old_leaf_id, new_leaf_id)
        path: list[SessionEntry] = []
        cur = old_leaf_id
        while cur is not None and cur != ancestor_id:
            entry = self._entries.get(cur)
            if entry is None:
                break
            path.append(entry)
            cur = entry.parent_id
        path.reverse()
        return path

    # ── Persistence ───────────────────────────────────────────────────

    def _add_entry(self, entry: SessionEntry) -> None:
        self._entries[entry.id] = entry
        self._children.setdefault(entry.parent_id, []).append(entry.id)

    def _save_entry(self, entry: SessionEntry) -> None:
        if self._path:
            existed = self._path.exists()
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
            if not existed:
                try:
                    os.chmod(self._path, 0o600)
                except OSError:
                    pass

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = SessionEntry.from_json(line)
                    self._add_entry(entry)
                except (json.JSONDecodeError, TypeError):
                    continue

        # Find the leaf: most-recent entry that has no children
        ids_with_children = {
            pid for pid, cids in self._children.items() if pid is not None and cids
        }
        leaves = [
            eid for eid in self._entries if eid not in ids_with_children
        ]
        if leaves:
            self._leaf_id = max(
                leaves, key=lambda eid: self._entries[eid].timestamp
            )
        elif self._entries:
            self._leaf_id = max(
                self._entries, key=lambda eid: self._entries[eid].timestamp
            )

        # Recover session_id from header
        for entry in self._entries.values():
            if entry.type == "header":
                self._session_id = entry.metadata.get(
                    "session_id", self._session_id
                )
                break
