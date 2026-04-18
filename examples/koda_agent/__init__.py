"""KODA deep-agent example — clean modular assembly.

Launch from the repo root:

    koda --agent examples.koda_agent --model anthropic:claude-sonnet-4-6

File layout
-----------
  prompt.py       system prompt template + environment stamp
  tools.py        web_search, read_webpage, show_widget
  skills.py       scans <workspace>/skills/* for local SKILL.md bundles
  deep_agent.py   wires everything into `create_deep_agent`

`build(model=...)` is the factory KODA's `--agent` resolver calls.
Skills are loaded from whatever is present under `agent_workspace/skills/`
— no downloads, no network access.
"""

from .deep_agent import build

__all__ = ["build"]
