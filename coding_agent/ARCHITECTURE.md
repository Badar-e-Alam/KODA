# KODA Coding Agent — Architecture

A comprehensive walkthrough of the `coding_agent/` package: how a user turn
becomes LLM calls and tool invocations, how context is kept under control,
how the agent is plugged into the KODA TUI vs. run standalone, and the
deliberate trade-offs behind each boundary.

This document is written for a reader who already knows the basics of
ReAct-style agents and wants to understand *why* the code is shaped the way
it is, not just *what* it does.

---

## 1. Design goals

The agent is built around three goals, in order:

1. **Provider-portability.** The same loop must work against Ollama Cloud,
   OpenAI-compatible endpoints, and any other Chat-Completions-shaped API.
   We avoid provider-specific features (assistants, code-interpreter,
   built-in retrieval) and lean on plain `chat.completions` + tool-calling.
2. **Two surfaces, one agent.** The agent has to be embeddable in KODA's
   Textual TUI (event-driven, async, streaming) *and* runnable as a CLI
   one-shot for offline experimentation. We get this by sharing the prompt,
   tool registry, and model config across two thin run drivers.
3. **Predictable token budgets.** A long session must not OOM the context
   window or quietly drift past the model's hard limit. Compaction lives at
   a single seam (the adapter for the TUI; an optional middleware for the
   standalone), so token pressure has one well-defined response.

Non-goals: cost optimisation, multi-tenant isolation, retrieval-augmented
context, multi-model routing inside a single turn.

---

## 2. Module map

| File | Role |
|------|------|
| `agent.py` | (a) module-level SDK `Agent` for the TUI, (b) `CodingAgent` class for the standalone driver, (c) `AGENTS.md` discovery + git-snapshot prompt seeding |
| `tools.py` | 16 `@function_tool` callables — files, search, edits, git, tests, shell, web, planning, scratchpad — plus the global approval-mode switch |
| `system_prompt.py` | `SYSTEM_PROMPT` (full Codex-style policy), `LOOP_SYSTEM_PROMPT` (compact ReAct prompt), `AGENTS_INIT_PROMPT` (used during AGENTS.md bootstrap) |
| `summarizer.py` | `Summarizer` — wraps a `CodingAgent` for one-shot / batch text summarisation. Used by the offload pipeline and the per-tool `_maybe_summarize` hook |
| `context_engineering.py` | `ContextAgent` + `OffloadMiddleware` + `SummarizeMiddleware` — a deepagents-style pluggable context manager (currently optional; both runners can adopt it) |
| `message.py` | `Message` dataclass shared by the summariser and the middleware framework; `__len__` returns content length so token-budget heuristics work |
| `subagent.py` | Read-only explorer subagent + `dispatch_subagent` function tool. Spawns a sandboxed `Agent` with only `read_file` + `grep`. Currently *not* wired into `_TOOLS` — see §11 |
| `clients.py` | Thin client constructors (Ollama, OpenAI) used by ad-hoc scripts |
| `main.py` | Tiny REPL using `Runner.run_sync` — kept for local smoke testing |
| `koda/adapters/coding_agent.py` | The KODA-side adapter: history reshaping, compaction, streaming-event extraction, cancellation, model swap on `/model` |

The TUI side lives in `koda/`; everything inside `coding_agent/` is meant to
be importable on its own without pulling in KODA's UI stack.

---

## 3. The two execution paths

```
                             coding_agent/
                             ┌──────────────────────────────┐
        TUI           ┌────► │  module-level `coding_agent`  │
   (koda --agent      │      │  (openai-agents SDK Agent)    │
    coding_agent)     │      └──────────────────────────────┘
                      │                  ▲
                      │                  │ shared: SYSTEM_PROMPT
                      │                  │ shared: _TOOLS
                      │                  │ shared: AsyncOpenAI client
                      ▼                  │
   CodingAgentAdapter (KODA-side)        │
        │                                │
        │ Runner.run_streamed(...)       │
        │                                │
        ▼                                │
   stream_events() → TextDelta / ToolStart / ToolResult
                                         │
                                         │
        CLI                              │
   (python coding_agent/agent.py "...") ─┘
                      │
                      ▼
   CodingAgent class (`agent.py`)
        │ hand-written think/act/observe loop
        │ direct chat.completions.create(stream=True)
        ▼
   stdout (streaming text + log diagnostics)
```

### 3.1 TUI path — the OpenAI-Agents SDK driver

Used by default (`koda --agent coding_agent`). The SDK takes care of the
ReAct loop, JSON-schema generation for tools, tool-call dispatch, and
streaming-event plumbing. We keep the loop logic out of our codebase here;
our job is purely to feed history in and translate events out.

Boot sequence:

```
koda CLI
  │  --agent coding_agent (default in koda/__main__.py)
  ▼
_build_adapter_factory("coding_agent")
  │  returns: lambda model, thread_id: create_coding_agent_adapter(...)
  ▼
CodingAgentAdapter.__init__              # koda/adapters/coding_agent.py
  │  _ensure_agent_importable()          # adds coding_agent/ to sys.path
  │  from agent import coding_agent      # imports the global SDK Agent
  │  set_approval_mode("yolo")           # TUI cannot prompt on stdin
  │  rewire .model from --model spec     # see §6
  ▼
ready — TUI streams user turns through adapter.stream(...)
```

A user turn (`adapter.stream(message, history)`):

1. **Reshape history.** Filter to `{role, content}` dicts; append the new
   user message.
2. **Compact when needed** (`_maybe_compact`). The OpenAI Agents SDK does
   not auto-compact, so we estimate tokens with a `len(content)//4`
   heuristic and, above the threshold, replace everything older than the
   last `_KEEP_RECENT` turns with one summary `system` message produced by
   `koda.summarizer.summarize_messages`.
3. **Run.** `Runner.run_streamed(self._agent, sdk_input, max_turns=200)`.
   The SDK now owns the loop: send conversation + tool schemas to the
   model; if `tool_calls` come back, invoke each `@function_tool`, push
   results back as `tool` messages; repeat until a final text answer or
   `max_turns`.
4. **Translate events.** `_extract_event` maps SDK stream events to KODA's
   six-event protocol:

   | SDK event | KODA event |
   |---|---|
   | `raw_response_event` with `data.type == "response.output_text.delta"` | `TextDelta(content)` |
   | `run_item_stream_event` `name="tool_called"` | `ToolStart(tool_id, name, arguments)` |
   | `run_item_stream_event` `name="tool_output"` | `ToolResult(tool_id, output, is_error)` |

5. **Cancellation.** `BaseAdapter.interrupt()` sets `_cancel`; the override
   in `CodingAgentAdapter.interrupt` also calls `result.cancel()` so the
   SDK aborts mid-run.

### 3.2 Standalone path — the hand-written loop

`CodingAgent` in `agent.py` is the canonical reference loop: no SDK magic,
every step visible. It exists for three reasons: (a) debugging the loop
without the SDK abstraction in the way, (b) embedding in non-TUI scripts,
(c) acting as the model owner for the `Summarizer` (which avoids the SDK
to keep summarisation cheap and sync).

The loop, with the recent hardening:

```
for step in 1..max_steps:
    stream = self._stream_with_retry(           # 3-try exp backoff on
        chat.completions.create,                # APIConnectionError /
        stream=True,                            # APITimeoutError /
        temperature=0.2,                        # RateLimitError /
        stream_options={include_usage: True}    # InternalServerError
    )
    accumulate {content, tool_calls, usage} from chunks
    append assistant message to history
    if no tool_calls: return content            # final answer
    for tc in tool_calls:
        with langfuse span(name=tc.name, as_type="tool"):
            result = invoke_tool(tc.name, tc.args)
            result = maybe_summarize(tc.name, result)   # opt-in summarise
        append {role:"tool", tool_call_id, content:result}
return "[max steps reached]"
```

Key design points:

- **Streaming is mandatory**, not optional. We always pass `stream=True`
  and accumulate deltas. This gives the standalone CLI a live token feed
  (the only `print()` left in the loop) and lets us collect per-step
  `usage` data from the trailing usage chunk.
- **Tool-call accumulator.** Tool calls arrive as deltas indexed by
  `tc_delta.index`; we accumulate `id`, `function.name`, and
  `function.arguments` slot-by-slot, then materialise once the stream
  ends. This is the OpenAI-style streaming-tool-call protocol; Ollama
  Cloud follows the same shape.
- **Diagnostics go through `logging`.** The bookkeeping `--- step N: ... ---`
  lines are now `_log.info(...)` so they don't fight the TUI for the
  terminal when the module is imported in non-CLI contexts.
- **Retry boundary is the request, not the stream.** Mid-stream failures
  are not retried (the partial assistant turn is already half-recorded);
  we retry only the initial `create()` call, which is where 5xx, rate
  limits, and connection resets typically surface.

---

## 4. Prompt assembly

The system prompt the model actually sees is composed at startup from three
sources, glued by `_compose_instructions`:

```
SYSTEM_PROMPT
   │
   ├── "# Project context (from AGENTS.md)"    ← if AGENTS.md exists
   │       <verbatim contents>
   │
   └── "# Git context (snapshot at session start)"  ← if .git exists
           Branch: <name>
           Status: <git status --short>
           Recent commits: <git log -5>
```

- **`SYSTEM_PROMPT`** is the static policy: tool catalogue, autonomy rules,
  edit constraints, plan-depth requirements, formatting rules. After the
  recent cleanup it advertises only tools that are actually registered.
- **`AGENTS.md`** is the per-project knowledge file (overview, tech stack,
  setup commands, conventions, gotchas). The `CodingAgent` class can
  bootstrap it on first run via `_bootstrap_agents_md`, which swaps the
  prompt to `AGENTS_INIT_PROMPT` and asks the model to write the file.
- **Git snapshot** seeds the conversation with branch/status/log so the
  agent doesn't have to spend three tool calls answering "where am I?"
  on every session.

The TUI path takes this snapshot at *module import time* (via the global
`coding_agent`), so it reflects the cwd when KODA started, not the cwd at
turn time. That is intentional: KODA is single-project per session.

---

## 5. The tool layer

All tools live in `tools.py` and are registered as `@function_tool`s, which
generates an OpenAI-shaped JSON schema from the Python signature and
docstring.

### 5.1 Tool catalogue

| Category | Tools |
|---|---|
| Files | `read_file`, `write_file`, `edit_file`, `multi_edit`, `glob_files` |
| Search | `grep` |
| Git | `git_status`, `git_diff`, `git_log`, `git_blame` |
| Tests | `run_tests` (auto-detects pytest / jest / cargo / go / npm-test) |
| Shell | `run_shell` (gated by approval mode) |
| Web | `web_fetch` (HTML stripped to body text) |
| Planning | `todo_write`, `todo_update` |
| Reasoning | `think` (no-op scratchpad — the act of writing structures the trace) |
| Approval | `set_approval_mode` (not registered; called by adapter at startup) |

Total: 16 in the registry.

### 5.2 Edit semantics

Edits are deliberately strict to make hallucinations fail fast:

- `edit_file(path, old, new)` requires `old` to match **exactly once**. If
  it matches zero or multiple times, the tool returns a structured error
  telling the model *not* to retry the same `old` and instead widen with
  surrounding context or fall back to `multi_edit`.
- `multi_edit(path, edits)` is **atomic**: each edit's `old` must be
  unique in the file *as of the prior edits in the batch*. Any failure
  rolls back the entire batch — no partial writes.
- `write_file` is the only "blast radius" tool: full overwrite, no
  pre-image check. The system prompt tells the model to reserve it for
  new files or full rewrites.

### 5.3 Approval modes (`run_shell`)

`run_shell` is the one tool that can do arbitrary harm. It honours a
process-wide mode flag:

| Mode | Behaviour |
|---|---|
| `yolo` | Run anything. Default in TUI (set by adapter at startup, since the TUI cannot prompt on stdin). |
| `auto` | Run only commands matching a regex allowlist (read-only `ls/cat/git status/rg/...`). Anything else returns a `[blocked]` message instructing the model to narrow or ask the user to flip mode. |
| `ask` | Block all commands. Used when humans want explicit per-command consent. |

The mode is a module global (`_APPROVAL_MODE` in `tools.py`), which is the
single biggest *known* weakness of the current design — see §11.

### 5.4 Environment hardening

`run_shell` and `run_tests` use `_enriched_env()` to prepend version-
manager bin dirs (`.nvm/versions/node/*/bin`, `.cargo/bin`,
`.pyenv/shims`, `.local/bin`, `.bun/bin`, `.deno/bin`) to `PATH`. Without
this, `subprocess.run(..., shell=True)` invokes `/bin/sh` which never
sources `~/.bashrc`, so freshly installed tools are invisible by default.

---

## 6. Model abstraction & switching

The agent supports three "model families" through a single
`OpenAIChatCompletionsModel` wrapper:

```
            ┌────────────────────────────────────────────────┐
            │           OpenAIChatCompletionsModel           │
            │   .model: str  ← e.g. "qwen3-coder:480b"      │
            │   .openai_client: AsyncOpenAI                  │
            │       .base_url: e.g. https://ollama.com/v1   │
            │       .api_key:  OLLAMA_API_KEY                │
            └────────────────────────────────────────────────┘
                              ▲
                              │ shared by SDK Agent + Summarizer
```

`/model <provider:name>` in the TUI re-builds the adapter, whose
`__init__` mutates the global `coding_agent` in place:

| Spec | Effect |
|---|---|
| `openai:NAME` | replace `.model` with the bare string `NAME`; the SDK falls back to its default OpenAI client |
| `ollama:NAME` | swap only `.model.model = NAME`, **preserving** the existing `AsyncOpenAI` client (so `base_url` + API key are not rebuilt) |
| anything else | leave `.model` untouched, just update the displayed model name |

The Ollama path is the hot path; preserving the client keeps the keep-alive
connection pool warm across model switches.

---

## 7. Context engineering

There are three *independent* layers of context control. They compose, but
each can be turned off without breaking the others.

### 7.1 Layer A — per-tool-result truncation (`_maybe_summarize`)

Lives in `CodingAgent._maybe_summarize`. If a `Summarizer` is attached and
a tool's output exceeds `summarize_threshold` chars (default 4 KB), the
result is replaced inline with `[summarized output of <name>]\n<summary>`
before being appended as a `tool` message. This caps blast radius from
"agent did `cat` on a 2 MB file" before it hits the message history.

Default: **off** (summariser is `None`). Recommended: turn it on with a
small/cheap model for any session that touches large repos.

### 7.2 Layer B — pre-turn compaction (TUI adapter)

Lives in `koda/adapters/coding_agent.py:_maybe_compact`. Runs *once per
turn*, before `Runner.run_streamed` is called:

```
if estimate_tokens(history) > _COMPACT_THRESHOLD_TOKENS (default 80k):
    head = history[:-_KEEP_RECENT]      # default 6
    tail = history[-_KEEP_RECENT:]
    summary = summarize_messages(head)
    history = [system("[Earlier conversation summary]\n" + summary), *tail]
```

Token estimation is `len(content) // 4`, which under-counts for code.
Tune `KODA_CODING_AGENT_COMPACT_TOKENS` if you hit context-window errors.

This is the only compaction the TUI gets; the OpenAI Agents SDK has no
hook for in-loop compaction, so we trim *between* turns rather than
inside them. Inside one turn, the SDK can still grow the window
unboundedly via tool results — Layer A is the answer to that.

### 7.3 Layer C — `ContextAgent` middleware framework

`context_engineering.py` provides a deepagents-style middleware chain:

```
ContextAgent
  ├── message_history: list[Message]
  ├── tool_responses:  list[Message]
  ├── offloaded_paths: list[Path]
  └── middleware: list[Callable[[ContextAgent], None]]

after every .add(role, content) or .add_tool_response(content):
    for mw in middleware: mw(self)
```

Two middlewares ship out of the box:

- **`OffloadMiddleware(max_chars, keep_recent, offload_dir)`** — when total
  history exceeds `max_chars`, the older slice is written to a JSONL file
  under `offload_dir/offload-<ts>.jsonl` and replaced in-place with a
  marker message `[offloaded:<filename>]`. The file path is recorded in
  `ctx.offloaded_paths` so a downstream middleware can pick it up.
- **`SummarizeMiddleware(summarizer, chunk_chars, batch=False)`** — for
  every offloaded file, reads it back in `chunk_chars` blocks, runs each
  through the summariser, and replaces the corresponding marker message
  with one summary message (`[summary of <filename>]\n<combined>`).

The two compose: write-old-out → summarise-on-demand. This is
deliberately split — offloading is cheap and synchronous; summarising
hits the LLM and can be deferred or parallelised.

This framework is **wired but optional**; neither runner uses it by
default. Layer B (adapter compaction) covers the TUI; the standalone
loop currently has no compaction. Adopting Layer C in the standalone
loop would replace the ad-hoc Layer A and give a single, observable
context-management strategy.

---

## 8. Observability

Every LLM call and every tool invocation lands in Langfuse as a span,
grouped under a session id.

| Path | What gets traced |
|---|---|
| TUI (SDK Agent) | Every LLM call → generation span (auto, via `langfuse.openai.AsyncOpenAI` drop-in) |
| Standalone (`CodingAgent.run`) | Outer agent span (`@observe(name="coding_agent.run", as_type="agent")`) + per-tool spans (`start_as_current_observation(as_type="tool")`) + LLM generations + `session_id` / `user_id` propagation |

Sessions are grouped via `propagate_attributes(session_id=..., user_id=...)`
wrapped around the run. Session id falls back to `KODA_SESSION_ID` env, then
to a fresh `uuid4()` per run.

Required env (in `.env`):

```
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=...        # mapped to LANGFUSE_HOST at import time
KODA_SESSION_ID=...          # optional; default = uuid4 per run
```

The `LANGFUSE_BASE_URL → LANGFUSE_HOST` shim happens at module import in
`agent.py`; this is intentional, because Langfuse v4 reads `LANGFUSE_HOST`
but our `.env` standard uses `LANGFUSE_BASE_URL`.

---

## 9. Configuration

| Var | Default | Where |
|---|---|---|
| `OLLAMA_BASE_URL` | (none — required) | `agent.py` AsyncOpenAI client |
| `OLLAMA_API_KEY` | (none — required for hosted Ollama) | `agent.py` AsyncOpenAI client |
| `KODA_CODING_AGENT_MAX_TURNS` | `200` | `koda/adapters/coding_agent.py` |
| `KODA_CODING_AGENT_COMPACT_TOKENS` | `80000` | adapter compaction threshold |
| `KODA_CODING_AGENT_KEEP_RECENT` | `6` | turns kept verbatim past compaction |
| `KODA_SESSION_ID` | `uuid4().hex` | Langfuse session grouping |
| `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`/`BASE_URL` | — | Langfuse |

Hard-coded knobs worth knowing about:

- `temperature=0.2` (was 0.7 — high temperature was producing tool-name
  typos and plan drift; 0.2 matches the convention used by Codex / Claude
  Code / Cursor for tool-using coding agents).
- `max_steps=200` in the standalone driver, `max_turns=200` in the SDK
  driver. Same idea, named differently by the two stacks.
- `summarize_threshold=4_000` chars in `Summarizer` (Layer A).
- `chunk_chars=50_000` in `SummarizeMiddleware` (Layer C).

---

## 10. Concurrency & cancellation

- The SDK driver is fully async. `Runner.run_streamed` returns a streaming
  result whose `stream_events()` is iterated with `async for`. Cancellation
  is wired through `BaseAdapter._cancel` (an `asyncio.Event`) and
  `result.cancel()`.
- The standalone driver is **synchronous**. Each tool invocation runs
  through `asyncio.run(tool.on_invoke_tool(...))`, which spins up a fresh
  event loop per call. This is fine for the CLI but breaks if the class
  is used inside an existing event loop. Converting the loop to async is
  on the roadmap; the current shape was chosen to keep the standalone
  driver readable.
- `web_fetch` is currently synchronous (`httpx.get`). Async-ifying it is
  a small, isolated change.
- `subprocess.run` in `run_shell` and `run_tests` is synchronous and
  blocks the calling thread. In the TUI this happens inside the SDK's
  thread pool, which the SDK manages.

---

## 11. Known limitations & non-goals

These are explicit trade-offs, not bugs.

1. **Module-level mutable state.** The approval mode (`_APPROVAL_MODE`),
   the todo list (`_TODOS`), and the SDK `coding_agent` instance are
   process-wide globals. This is fine for the single-session TUI, but
   two adapters in one process would share state. The fix is to push
   these into the SDK's per-run `context` argument; deferred until
   multi-session is on the roadmap.
2. **Path sandboxing.** File tools accept arbitrary absolute paths. There
   is no `project_root` clamp. A misled agent can read or overwrite files
   outside the project. Acceptable for a developer-controlled CLI;
   not acceptable for any deployed-to-others scenario.
3. **`dispatch_subagent` is dead code.** Defined in `subagent.py` and
   designed to spawn a read-only explorer with its own context window,
   but never registered in `_TOOLS`. Either wire it up (and fix
   `_resolve_model` to pass the model object instead of `str(model)`) or
   remove the file.
4. **Standalone loop has no compaction.** The TUI adapter compacts
   between turns; the standalone loop does not. Long-running CLI sessions
   will eventually hit the context-window error. Layer C
   (`ContextAgent` middleware) is the intended fix.
5. **Token counting is character-based.** `len(content) // 4` over- or
   under-counts depending on language; for code-heavy contexts it
   under-counts. Good enough for compaction *triggering*, not good enough
   for hard-budget enforcement.
6. **AGENTS.md is loaded once at module import.** The TUI does not pick
   up changes to `AGENTS.md` mid-session and does not run the bootstrap
   if the file is missing — only the standalone `CodingAgent` class
   bootstraps. A small refactor (move composition into the adapter) fixes
   both.
7. **Compaction injects a second `system` message.** Some providers
   tolerate this; some merge it; some reject it. The current setup works
   on OpenAI and Ollama Cloud. Folding the summary into the existing
   system block would be more portable.

---

## 12. End-to-end picture

```
                 ┌─────────────────────────────┐
                 │           .env              │
                 │  OLLAMA_BASE_URL/API_KEY    │
                 │  LANGFUSE_*                 │
                 └──────────────┬──────────────┘
                                │ load_dotenv at agent.py import
                                ▼
   ┌────────────────────────────────────────────────────────────┐
   │                       agent.py                             │
   │                                                            │
   │   _async_client = AsyncOpenAI(base_url, api_key)           │
   │                                                            │
   │   coding_agent = Agent(                                    │
   │       instructions=_compose_instructions(SYSTEM_PROMPT,    │
   │                                          cwd),             │
   │       model=OpenAIChatCompletionsModel(                    │
   │           model="qwen3-coder:480b",                        │
   │           openai_client=_async_client),                    │
   │       model_settings=ModelSettings(temperature=0.2),       │
   │       tools=_TOOLS  (16))                                  │
   │                                                            │
   │   class CodingAgent:                                       │
   │       run() → propagate_attributes → _run_traced           │
   │           loop:                                             │
   │             stream = _stream_with_retry(...)               │
   │             accumulate deltas                              │
   │             dispatch tools through Langfuse spans          │
   │             apply _maybe_summarize on each result          │
   └────────────────────┬─────────────────────┬─────────────────┘
                        │                     │
    KODA TUI path       │                     │   standalone CLI
                        ▼                     ▼
   ┌──────────────────────────────┐    ┌───────────────────────┐
   │ CodingAgentAdapter (KODA)    │    │   __main__ in         │
   │   _ensure_agent_importable   │    │   agent.py            │
   │   set_approval_mode("yolo")  │    │   logging.basicConfig │
   │   rewire .model from spec    │    │   CodingAgent(...)    │
   │   stream(message, history):  │    │   .run(query, ...)    │
   │     reshape history          │    └───────────────────────┘
   │     _maybe_compact (Layer B) │
   │     Runner.run_streamed      │
   │     async for ev:            │
   │       _extract_event(ev) →   │
   │         TextDelta /          │
   │         ToolStart /          │
   │         ToolResult           │
   └──────────────────────────────┘
                        │
                        ▼
                  KODA TUI renders
```

---

## 13. Where to look first

- Adding a tool → `tools.py` (decorate with `@function_tool`, add to
  `_TOOLS` in `agent.py`, document it in `SYSTEM_PROMPT`).
- Changing the prompt policy → `system_prompt.py`.
- Changing how history is trimmed in the TUI → `_maybe_compact` in
  `koda/adapters/coding_agent.py`.
- Plugging in a new context strategy → write a middleware in
  `context_engineering.py` and pass it to `ContextAgent`.
- Adding a new event type to the TUI stream → add a dataclass in
  `koda/agent_api.py` and a branch in `_extract_event`.
- Pointing at a different LLM → swap the `OpenAIChatCompletionsModel`
  args, or pass `--model provider:name` (handled in the adapter).
