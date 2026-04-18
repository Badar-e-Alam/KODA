"""
Completers — build suggestion lists for the ChatInput popup.

Three triggers:
  /          → slash commands (filtered by prefix)
  /model ... → available models (filtered by substring)
  @...       → files in the project (filtered by substring)
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

_log = logging.getLogger("koda.tui.completers")


@dataclass(frozen=True)
class Suggestion:
    insert: str          # what replaces the trigger fragment in the input
    label: str           # primary text shown in the popup
    description: str = ""  # dim secondary text

    def display(self) -> str:
        if self.description:
            return f"{self.label}  [dim]{self.description}[/]"
        return self.label


# ── Entry point ────────────────────────────────────────────────────

def complete(
    value: str, cursor: int
) -> tuple[list[Suggestion], tuple[int, int], str] | None:
    """Return (suggestions, (replace_start, replace_end), title) for the active
    trigger. `title` is the category shown in the popup header
    (e.g. "Commands", "Models", "Files"). None if nothing triggers.
    """
    # /model <fragment>
    if value.startswith("/model ") or value.startswith("/model\t"):
        frag = value[7:]
        return (_complete_models(frag.strip()), (7, len(value)), "Models")

    # /theme <fragment>
    if value.startswith("/theme ") or value.startswith("/theme\t"):
        frag = value[7:]
        return (_complete_themes(frag.strip()), (7, len(value)), "Themes")

    # /<fragment>  (bare slash command)
    if value.startswith("/"):
        frag = value[1:].split(" ", 1)[0]
        end = 1 + len(frag)
        return (_complete_commands(frag), (0, end), "Commands")

    # @<fragment>  — find the @word that contains the cursor
    at_range = _find_at_token(value, cursor)
    if at_range is not None:
        start, end = at_range
        frag = value[start + 1 : end]
        return (_complete_files(frag), (start, end), "Files")

    return None


def _complete_themes(fragment: str) -> list[Suggestion]:
    """Theme names. The replace_range for `/theme ` completions covers only
    the argument (from index 7 onwards), so ``insert`` must be the bare name
    — prepending "/theme " would duplicate the prefix.
    """
    from koda.tui.theme import THEMES

    frag = fragment.lower()
    return [
        Suggestion(insert=name, label=name, description="theme")
        for name in sorted(THEMES.keys())
        if frag in name.lower()
    ]


# ── Slash commands ─────────────────────────────────────────────────

def _complete_commands(fragment: str) -> list[Suggestion]:
    from koda.tui.commands import _HELP

    frag = fragment.lower()
    out: list[Suggestion] = []
    for name, (_handler, desc) in sorted(_HELP.items()):
        # Skip exact matches — that command is already fully typed
        if name == frag:
            continue
        if name.startswith(frag):
            # Commands that take args get a trailing space
            trailing = " " if name in ("model", "theme") else ""
            out.append(
                Suggestion(
                    insert=f"/{name}{trailing}",
                    label=f"/{name}",
                    description=desc,
                )
            )
    return out


# ── Model completion ───────────────────────────────────────────────

def _complete_models(fragment: str) -> list[Suggestion]:
    """``provider:model`` suggestions. As with /theme, replace_range starts at
    7 (past "/model "), so ``insert`` must not include that prefix — otherwise
    it gets duplicated into "/model /model provider:model".
    """
    from koda.model_config import get_available_models

    frag = fragment.lower()
    out: list[Suggestion] = []
    try:
        available = get_available_models()
    except Exception as e:  # model discovery is best-effort
        _log.warning("model discovery failed: %s", e)
        available = {}
    for provider, models in sorted(available.items()):
        for m in models:
            full = f"{provider}:{m}"
            if frag in full.lower():
                out.append(
                    Suggestion(insert=full, label=full, description=provider)
                )
    return out[:60]


# ── File completion ────────────────────────────────────────────────

_FILES_CACHE: list[str] = []
# Directories we never descend into for the os.walk fallback
_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
    ".idea", ".vscode", ".DS_Store", "target", ".next", ".cache",
}
_MAX_WALK_FILES = 5000


def _all_files() -> list[str]:
    """Tracked + untracked files via git, or a pruned os.walk fallback.

    Result is cached process-wide; call ``invalidate_files_cache()`` to refresh.
    """
    global _FILES_CACHE
    if _FILES_CACHE:
        return _FILES_CACHE
    files = _git_ls_files()
    if not files:
        files = _walk_files()
    _FILES_CACHE = files
    return files


def _git_ls_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [f for f in result.stdout.splitlines() if f]
    except Exception as e:
        _log.debug("git ls-files unavailable: %s", e)
        return []


def _walk_files() -> list[str]:
    """Fallback: walk the CWD with ignore-dir pruning, capped at _MAX_WALK_FILES."""
    import os

    root = os.getcwd()
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            out.append(rel.replace(os.sep, "/"))
            if len(out) >= _MAX_WALK_FILES:
                return out
    return out


def invalidate_files_cache() -> None:
    global _FILES_CACHE
    _FILES_CACHE = []


def _complete_files(fragment: str) -> list[Suggestion]:
    files = _all_files()
    frag = fragment.lower()
    # Substring match on path, but rank by name-prefix > name-substring > path-substring
    scored: list[tuple[int, str]] = []
    for f in files:
        name = f.rsplit("/", 1)[-1].lower()
        path = f.lower()
        if not frag:
            scored.append((0, f))
        elif name.startswith(frag):
            scored.append((0, f))
        elif frag in name:
            scored.append((1, f))
        elif frag in path:
            scored.append((2, f))
    scored.sort()
    # No trailing space: a file ref is self-contained, so a single Enter
    # submits the message. Users who want multiple @refs can type a space
    # themselves between them.
    return [
        Suggestion(insert=f"@{path}", label=path, description="")
        for _, path in scored[:40]
    ]


def _find_at_token(value: str, cursor: int) -> tuple[int, int] | None:
    """Return (start, end) of an @... token containing the cursor, or None."""
    if not value:
        return None
    cursor = max(0, min(cursor, len(value)))
    # Walk back from cursor to find '@' or whitespace/start
    start = cursor
    while start > 0 and value[start - 1] not in " \t\n":
        start -= 1
        if value[start] == "@":
            break
    else:
        if value[start : start + 1] != "@":
            return None
    if start >= len(value) or value[start] != "@":
        return None
    # Walk forward to next whitespace
    end = cursor
    while end < len(value) and value[end] not in " \t\n":
        end += 1
    return (start, end)
