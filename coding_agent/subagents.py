"""Specialist subagents for the coding agent.

Three focused subagents that the main agent dispatches via deepagents'
built-in ``task(description, subagent_type)`` tool:

- ``explore`` — read-only orientation.
- ``plan``    — design + write_todos, no mutations.
- ``edit``    — execute a pre-decided change, verify, report.

Each runs in its own LangGraph subgraph with an isolated context window
so intermediate tool chatter never reaches the main agent. Tools are
restricted per subagent (see ``tools=`` lists). ``model``/``skills``/
``permissions`` are intentionally omitted so subagents inherit the
parent's config — same model, same skills, same project-wide gate.

deepagents' ``SubAgentMiddleware`` auto-adds a ``general-purpose``
subagent unless one is declared with that name; we keep it as a
catch-all.
"""

from __future__ import annotations

from deepagents.middleware.subagents import SubAgent

from coding_agent.tools import (
    ask_user,
    git,
    git_diff,
    multi_edit,
    run_lint,
    run_tests,
    run_type_check,
    think,
    visual_analyze,
    web_fetch,
    web_search,
)

# Deepagents auto-injects the filesystem tool set (``ls`` / ``read_file`` /
# ``write_file`` / ``edit_file`` / ``glob`` / ``grep`` / ``execute``) into
# every subagent via ``FilesystemMiddleware`` regardless of what we list
# here, and ``write_todos`` via ``TodoListMiddleware``. So the ``tools``
# field below carries only the EXTRA_TOOLS callables we want available
# in each subagent. The read-only contract for ``explore`` / ``plan`` is
# enforced by (a) their system prompts and (b) the permission gate when
# the user is in PLAN mode — subagents inherit the main graph's
# ``interrupt_on`` (deepagents propagates it to declarative SubAgent specs),
# and a sub-agent ``interrupt()`` surfaces to the top-level adapter, where
# ``koda.tools.permissions.decide`` rejects mutations in PLAN mode.


EXPLORE_PROMPT = """\
You are the EXPLORE subagent.

Goal: build a mental map of the codebase for the calling agent. You are
read-only. Never edit files, never run mutating shell commands.

Approach:
- Batch reads in parallel. When you need to sample multiple files or
  run several greps, emit them as parallel tool calls in one turn —
  don't sequence what doesn't need sequencing.
- Triangulate with `glob` and `grep` before reading. `read_file` with
  `offset`+`limit` for big files.
- For external context (docs, RFCs, public APIs), `web_fetch` /
  `web_search`. Use sparingly.
- `git` and `git_diff` are read-only; use them to understand history
  or current diff state.

Output format: a short structured report — bullet points or a small
table — with `file:line` citations for every claim. State what you
checked and what you didn't. If something is uncertain, say so.

Keep the response focused on what the calling agent asked. Don't
narrate your process. Don't recommend changes — that's the planner's
job.
"""


PLAN_PROMPT = """\
You are the PLAN subagent.

Goal: produce a concrete, executable implementation plan for a change
the calling agent has scoped. You are read-only — your output is the
plan, not the code.

Approach:
- Start by reading the relevant files in parallel (`read_file` /
  `glob` / `grep` in one tool-use batch when independent).
- Use `git` / `git_diff` to understand current state if relevant.
- Use `think` to work through trade-offs out loud before drafting.
- Use `write_todos` to capture an ordered task list when the change
  spans multiple steps.

Plan structure (return this as your final message):

1. **Context** — one paragraph: what's being changed, why.
2. **Critical Files** — bulleted file:line list with one-line
   descriptions of the role each plays.
3. **Steps** — ordered, concrete, each one a single editable unit.
4. **Risks / Open Questions** — anything the calling agent must
   decide before execution.
5. **Verification** — exactly how to confirm the change works (tests,
   typecheck, manual flow).

Clarifying questions: if the request hides a material decision the
code can't answer (which approach, which library, where to put the
code, break vs. deprecate), call `ask_user(question, options=[...])`
ONCE before drafting. The user's answer goes straight back to you;
the calling agent sees only your final plan. One focused question
beats a wrong plan.

Do NOT mutate files. Do NOT run tests. If you find the scope is
wrong or impossible, say so and stop — don't paper over it.
"""


EDIT_PROMPT = """\
You are the EDIT subagent.

Goal: apply ONE specific, already-decided change end to end. The
calling agent has decided what to do; your job is to execute cleanly
and verify.

Approach:
1. Read only what you need to ground the edit (target file + nearest
   collaborators). Don't re-explore.
2. Make the edit with `edit_file` (preferred) or `multi_edit` for
   multi-spot single-file changes. `write_file` only for genuinely
   new files.
3. Run verification immediately after — `run_type_check`,
   `run_lint`, and the most targeted `run_tests` invocation you can
   justify. Use `execute` for project-specific commands the runners
   don't cover.
4. Loop on failure: read the error, decide if the fix is in scope,
   apply it, re-verify. Stop after 3 failed attempts and report.

Do NOT re-plan the scope. If the requested change doesn't match the
code you find, report the mismatch and stop — the calling agent
decides whether to re-scope.

Clarifying questions: if you hit a mid-flight decision the calling
agent didn't pre-specify and a wrong pick would be >5min to undo
(roll forward vs. revert on a failing test, change wire format,
delete data), call `ask_user(question, options=[...])`. Don't ask
for trivial picks the user clearly didn't care about — just decide.

Final message: a short summary (≤8 lines) — what changed, what
verification ran, what passed, what failed. Cite `file:line` for
every edit.
"""


SUBAGENTS: list[SubAgent] = [
    {
        "name": "explore",
        "description": (
            "Read-only codebase orientation: trace flows, locate files, "
            "summarize structure with file:line refs. Never edits, never "
            "runs mutating shell. Use for >5 reads of orientation."
        ),
        "system_prompt": EXPLORE_PROMPT,
        "tools": [think, git, git_diff, visual_analyze, web_fetch, web_search],
    },
    {
        "name": "plan",
        "description": (
            "Produce a concrete implementation plan (files to touch, "
            "ordered steps, risks, verification). Read-only; returns the "
            "plan as the final message. Use for multi-file design before "
            "committing to an approach."
        ),
        "system_prompt": PLAN_PROMPT,
        "tools": [visual_analyze, ask_user, think, git, git_diff, web_fetch, web_search],
    },
    {
        "name": "edit",
        "description": (
            "Apply a specific, pre-decided code change end-to-end "
            "(edit → typecheck/lint/tests → report). Use when the main "
            "agent has already decided the change and wants context "
            "isolation."
        ),
        "system_prompt": EDIT_PROMPT,
        "tools": [
            ask_user,
            multi_edit,
            git,
            git_diff,
            run_tests,
            run_type_check,
            run_lint,
            think,
            visual_analyze,
        ],
    },
]


__all__ = ["SUBAGENTS", "EXPLORE_PROMPT", "PLAN_PROMPT", "EDIT_PROMPT"]
