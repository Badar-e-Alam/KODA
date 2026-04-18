"""Hermes-style skill synthesis tool.

Lets the agent crystallize a reusable procedure from its current turn
into a new skill under ``<workspace>/skills/<name>/SKILL.md``. That file
is picked up on the next agent build by ``discover_skills`` (same logic
the koda_agent example uses), so the agent carries the skill forward
across sessions — the persistent-memory / self-improving spirit of the
Hermes Agent framework.

No network, no machine learning tricks — just a single filesystem tool
the model invokes when it notices "I should save this for later."
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from langchain.tools import tool

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")


def _slugify(s: str) -> str:
    s = _SLUG_RE.sub("-", s.lower()).strip("-")
    return s[:64] or "skill"


def _workspace() -> Path:
    return Path(os.environ.get("KODA_WORKSPACE", Path.cwd() / "agent_workspace")).resolve()


_TEMPLATE = """\
---
name: {name}
description: {description}
---

# {title}

{instructions}
"""


@tool
def author_skill(name: str, description: str, instructions: str) -> str:
    """Persist a reusable procedure as a skill under /skills/<name>/SKILL.md.

    Use this at the end of a task when you realize the approach you just
    used would help on similar future requests — crystallize it into a
    named procedure so later sessions can load it by name.

    Args:
        name: Short slug, lowercase letters/digits/hyphens, ≤64 chars.
              Examples: 'sql-query-optimizer', 'csv-cleanup'.
        description: One sentence describing WHEN to use the skill (the
              LLM reads this to decide relevance), max ~200 chars.
        instructions: The full how-to body of the skill (markdown).
              Include steps, examples, and failure modes the agent
              should watch for.

    Returns:
        Path to the written SKILL.md, or an error message.
    """
    slug = _slugify(name)
    if not _NAME_RE.match(slug):
        return f"Error: invalid skill name {name!r} (got slug {slug!r})"
    description = description.strip()
    if not description:
        return "Error: description is required"
    if len(description) > 1024:
        description = description[:1020] + "..."

    target = _workspace() / "skills" / slug / "SKILL.md"
    if target.exists():
        return f"Error: skill {slug!r} already exists at {target} — edit that file directly if you want to update it"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        _TEMPLATE.format(
            name=slug,
            description=description,
            title=slug.replace("-", " ").title(),
            instructions=instructions.strip() or "(no instructions)",
        ),
        encoding="utf-8",
    )
    return f"Skill saved: {target}"


__all__ = ["author_skill"]
