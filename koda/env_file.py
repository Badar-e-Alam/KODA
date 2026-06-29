"""Read/update the project ``.env`` file in place.

Used by the onboarding flow to persist API keys and host/cloud choices the
user enters so they don't have to retype them next launch. We deliberately
do a line-oriented update (not a full rewrite via ``dotenv``) to preserve
the user's existing comments, ordering, and untouched keys.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["update_env_file", "default_env_path"]


def default_env_path() -> Path:
    """The ``.env`` next to the process CWD (where ``load_dotenv`` reads)."""
    return Path(os.getcwd()) / ".env"


def _format_line(key: str, value: str) -> str:
    # Quote only when the value would otherwise be ambiguous (spaces, '#',
    # or leading/trailing whitespace). Tokens and URLs need no quoting.
    if value != value.strip() or " " in value or "#" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    return f"{key}={value}"


def update_env_file(updates: dict[str, str], path: str | Path | None = None) -> Path:
    """Apply ``updates`` to the ``.env`` at ``path`` (CWD's .env by default).

    - Existing uncommented ``KEY=…`` lines are replaced in place.
    - New keys are appended under a managed header.
    - Empty-string values are skipped (we never write blank credentials).
    - Comments, blank lines, and unrelated keys are left untouched.

    Returns the path written.
    """
    target = Path(path) if path is not None else default_env_path()
    updates = {k: v for k, v in updates.items() if v != ""}
    if not updates:
        return target

    try:
        existing = target.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        existing = []

    remaining = dict(updates)
    out: list[str] = []
    for line in existing:
        stripped = line.lstrip()
        # Only touch real assignments, never comments.
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(_format_line(key, remaining.pop(key)))
                continue
        out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# Added by KODA onboarding")
        for key, value in remaining.items():
            out.append(_format_line(key, value))

    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    return target
