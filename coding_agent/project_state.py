"""Project-state tracking for signal-driven AGENTS.md refresh.

We snapshot mtimes of "signal files" — the files most likely to indicate
a project's tech stack or structure has shifted enough to warrant
updating AGENTS.md — into ``.koda/state.json``. On startup, the agent
compares the current snapshot to the saved one and decides:

  - SKIP: no signals changed, AGENTS.md still valid → use it as-is.
  - DELTA: some signals changed, AGENTS.md exists → run a *delta* update
    (read just the changed files + current AGENTS.md, produce an updated
    one). Cheaper than re-exploring the whole repo.
  - FULL: AGENTS.md absent → run the original full bootstrap.

The agent owns the actual bootstrap loop; this module only owns the
state file and the routing decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from memory import MEMORY_DIRNAME

STATE_FILENAME = "state.json"
STATE_VERSION = 1

# Signal *file globs* — relative to project root. Anything matching these
# patterns at the project root counts as a tech-stack signal. Top-level
# dir presence is tracked separately (see ``_top_level_dirs``).
_SIGNAL_GLOBS = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
    "requirements-*.txt",
    "setup.py",
    "setup.cfg",
    "Gemfile",
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "Makefile",
    "README.md",
    "README.rst",
)


class BootstrapAction(str, Enum):
    SKIP = "skip"      # AGENTS.md present + no signals changed
    DELTA = "delta"    # AGENTS.md present + signals changed
    FULL = "full"      # AGENTS.md absent (entire cascade empty)


@dataclass(frozen=True)
class StateSnapshot:
    """One snapshot of the project's signal-file mtimes + top-level dirs."""
    version: int
    signals: dict[str, float]   # path (relative) → mtime
    top_dirs: list[str]          # sorted list of top-level dir names
    agents_md_mtime: float       # mtime of project-root AGENTS.md (0 if absent)

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "signals": self.signals,
                "top_dirs": self.top_dirs,
                "agents_md_mtime": self.agents_md_mtime,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "StateSnapshot | None":
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(d, dict) or d.get("version") != STATE_VERSION:
            return None
        return cls(
            version=STATE_VERSION,
            signals={str(k): float(v) for k, v in (d.get("signals") or {}).items()},
            top_dirs=sorted(str(x) for x in (d.get("top_dirs") or [])),
            agents_md_mtime=float(d.get("agents_md_mtime") or 0.0),
        )


def state_path(project_root: Path) -> Path:
    """``.koda/state.json`` (sibling of the memory dir)."""
    return project_root / MEMORY_DIRNAME.split("/")[0] / STATE_FILENAME


def _top_level_dirs(project_root: Path) -> list[str]:
    """Return sorted top-level dir names, excluding hidden + common noise."""
    if not project_root.is_dir():
        return []
    skip_prefixes = (".",)
    skip_names = {
        "node_modules", "__pycache__", "dist", "build", "target",
        "venv", ".venv", "env", "site-packages",
    }
    out: list[str] = []
    try:
        for p in project_root.iterdir():
            if not p.is_dir():
                continue
            if p.name.startswith(skip_prefixes) or p.name in skip_names:
                continue
            out.append(p.name)
    except OSError:
        return []
    return sorted(out)


def collect_snapshot(project_root: Path) -> StateSnapshot:
    """Build a fresh snapshot of the project's signals."""
    signals: dict[str, float] = {}
    for pattern in _SIGNAL_GLOBS:
        for path in project_root.glob(pattern):
            if not path.is_file():
                continue
            try:
                mt = path.stat().st_mtime
            except OSError:
                continue
            try:
                rel = path.relative_to(project_root).as_posix()
            except ValueError:
                rel = path.name
            signals[rel] = mt
    agents_md = project_root / "AGENTS.md"
    agents_md_mt = 0.0
    try:
        agents_md_mt = agents_md.stat().st_mtime
    except OSError:
        pass
    return StateSnapshot(
        version=STATE_VERSION,
        signals=signals,
        top_dirs=_top_level_dirs(project_root),
        agents_md_mtime=agents_md_mt,
    )


def load_snapshot(project_root: Path) -> StateSnapshot | None:
    """Read the persisted snapshot, or None if absent/corrupt/wrong-version."""
    try:
        raw = state_path(project_root).read_text()
    except OSError:
        return None
    return StateSnapshot.from_json(raw)


def save_snapshot(project_root: Path, snap: StateSnapshot) -> None:
    """Persist ``snap`` to ``.koda/state.json``. Creates the dir if needed."""
    p = state_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(snap.to_json())
    import os as _os
    _os.replace(tmp, p)


def changed_signals(prev: StateSnapshot | None, current: StateSnapshot) -> list[str]:
    """Return paths whose mtimes differ from the previous snapshot.

    First-run case (``prev`` is None) returns an empty list — there's
    nothing to compare against; the bootstrap router will decide based on
    AGENTS.md presence alone.
    """
    if prev is None:
        return []
    out: list[str] = []
    prev_sig = prev.signals
    for path, mt in current.signals.items():
        if prev_sig.get(path) != mt:
            out.append(path)
    # Removed signals also count as a change.
    for path in prev_sig:
        if path not in current.signals:
            out.append(path)
    # Top-level dir set changes (new/removed package).
    if prev.top_dirs != current.top_dirs:
        added = sorted(set(current.top_dirs) - set(prev.top_dirs))
        removed = sorted(set(prev.top_dirs) - set(current.top_dirs))
        for d in added:
            out.append(f"{d}/  (new)")
        for d in removed:
            out.append(f"{d}/  (removed)")
    return sorted(set(out))


def decide_bootstrap_action(
    project_root: Path,
    *,
    agents_md_present: bool,
) -> tuple[BootstrapAction, list[str], StateSnapshot]:
    """Decide whether to skip, run a delta update, or do a full bootstrap.

    Returns ``(action, changed_paths, current_snapshot)``. The caller is
    expected to pass ``current_snapshot`` to :func:`save_snapshot` after
    a successful (delta or full) bootstrap so subsequent runs have a
    fresh baseline.

    ``agents_md_present`` is the *cascade* answer — True iff any
    AGENTS.md in the cascade has content. When False we always need a
    full bootstrap regardless of state file contents.
    """
    current = collect_snapshot(project_root)
    if not agents_md_present:
        return BootstrapAction.FULL, [], current
    prev = load_snapshot(project_root)
    changed = changed_signals(prev, current)
    if prev is None:
        # First run with an existing AGENTS.md — establish the baseline,
        # don't trigger an update purely because we have no history.
        return BootstrapAction.SKIP, [], current
    if not changed:
        return BootstrapAction.SKIP, [], current
    return BootstrapAction.DELTA, changed, current
