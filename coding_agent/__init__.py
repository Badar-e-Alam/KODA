"""coding_agent — a deepagents-backed coding agent.

The agent is built via :func:`coding_agent.agent.build_agent`, which
wires a ``LocalShellBackend`` and KODA's extra tools (see
:mod:`coding_agent.tools`) into ``deepagents.create_deep_agent``.
"""

from coding_agent.agent import build_agent, run

__all__ = ["build_agent", "run"]
