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
    from koda.tui.theme import THEMES

    frag = fragment.lower()
    return [
        Suggestion(insert=f"/theme {name}", label=name, description="theme")
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
                    Suggestion(insert=f"/model {full}", label=full, description=provider)
                )
    return out[:60]


# ── File completion ────────────────────────────────────────────────

_FILES_CACHE: list[str] = []


def _all_files() -> list[str]:
    """Return tracked + untracked files via git (cheap, excludes .gitignore)."""
    global _FILES_CACHE
    if _FILES_CACHE:
        return _FILES_CACHE
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        files = [f for f in result.stdout.splitlines() if f]
    except Exception as e:
        _log.warning("git ls-files failed: %s", e)
        files = []
    _FILES_CACHE = files
    return files


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
    return [
        Suggestion(insert=f"@{path} ", label=path, description="")
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
