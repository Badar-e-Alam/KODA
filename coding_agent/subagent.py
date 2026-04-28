"""Read-only explorer subagent + dispatch_subagent function tool.

The subagent runs in its own LLM context — its conversation does NOT pollute
the main agent's window. Use it when the question would otherwise force the
main agent to pull a large number of files into its own context.
"""

from __future__ import annotations

from agents import Agent, Runner, function_tool

from tools import grep, read_file


_EXPLORER_PROMPT = """You are an explorer subagent that investigates a codebase
and returns a concise summary to the calling agent.

You have read-only tools (read_file, grep). You cannot edit, write, or run
shell commands.

Investigate thoroughly, then return a brief summary (1-3 short paragraphs)
that answers the calling agent's question. Cite specific files and line
numbers (path:line). Quote only the lines that matter — do NOT dump full
files back."""


_FALLBACK_MODEL = "gpt-4o-mini"


def _resolve_model() -> str:
    """Track the main agent's model so the subagent doesn't silently call a
    different provider than the one the user selected. Falls back to a small
    model if for any reason we can't read it."""
    try:
        from agent import coding_agent  # local to avoid import cycle
        model = getattr(coding_agent, "model", None)
        if model:
            return str(model)
    except Exception:
        pass
    return _FALLBACK_MODEL


@function_tool
async def dispatch_subagent(task: str) -> str:
    """Spawn a read-only subagent to investigate or research something. The
    subagent has its own context — its conversation does NOT pollute yours.

    Use this for:
      - "Find all places X is used across the repo."
      - "Understand how module Y works."
      - "Check if pattern Z exists anywhere in the codebase."
    Don't use this for:
      - Reading one known file (use read_file).
      - Editing or running anything (the subagent is read-only).

    Returns a short summary, not raw file contents.
    """
    explorer = Agent(
        name="Explorer",
        instructions=_EXPLORER_PROMPT,
        model=_resolve_model(),
        tools=[read_file, grep],
    )
    result = await Runner.run(explorer, task, max_turns=20)
    return str(result.final_output)
