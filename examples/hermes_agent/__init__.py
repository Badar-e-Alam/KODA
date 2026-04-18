"""Hermes-style KODA backend example.

Second reference implementation alongside ``examples.koda_agent``.
Same workspace, same tools, different agent framework underneath.

Launch from the repo root:

    koda --agent examples.hermes_agent --model openai:gpt-5

Compare with the deepagents-based example:

    koda --agent examples.koda_agent --model openai:gpt-5

File layout
-----------
  skill_author.py   `author_skill` tool — crystallizes a procedure into
                    a new SKILL.md under the shared /skills/ directory
  agent.py          Wires shared prompt + shared tools + AGENTS.md into
                    a plain LangGraph react-agent (no deepagents middleware)

Prompt, tools, skill-discovery, and workspace are all imported from
``examples.koda_agent`` — the only thing unique to this backend is the
agent framework (plain react-agent vs. deepagents) and the
``author_skill`` tool that closes the self-improvement loop.
"""

from .agent import build

__all__ = ["build"]
