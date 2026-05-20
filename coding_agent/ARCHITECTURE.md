# `coding_agent/` — Architecture

A walkthrough of the `coding_agent/` package: how a prompt becomes LLM
calls + tool invocations, where state lives, how the agent stays
portable across providers, and the deliberate trade-offs behind each
boundary.

Written for an engineer who already knows the basics of a ReAct-style
agent and wants to understand *why* the code is shaped this way, not
just *what* it does.

---

## 1. Design goals

In priority order:

1. **Lean on the framework.** The agent is a thin factory around
   [`deepagents.create_deep_agent`](https://docs.langchain.com/oss/python/deepagents/),
   which itself sits on LangGraph. We do **not** own the orchestration
   loop, the tool-call protocol, the streaming surface, or the
   filesystem-tool semantics. Those are framework concerns. The package
   owns: model resolution, the backend wiring, the tool extras, the
   system prompt, the persistent layout, and tracing.

2. **Provider-portable.** The same factory works against Anthropic,
   OpenAI, Google, Kimi (via Ollama Cloud), local Ollama, and anything
   else LangChain's `init_chat_model` understands. `kimi:` / `ollama:`
   specs are routed eagerly so the endpoint + auth header attach
   correctly; everything else is passed through as a string.

3. **State is durable and partitioned.** Conversations survive process
   restarts via a SQLite checkpointer. Memory survives across threads
   via a LangGraph `BaseStore`. Both are scoped per-project (hash of
   cwd) so different projects don't cross-contaminate.

4. **Storage layers are routed, not bolted on.** Skills, memories, and
   working-tree files all live behind a single backend interface — a
   `CompositeBackend` routes `/skills/`, `/memories/`, and the default
   project tree to different concrete backends. The agent never knows
   the difference; the docs the agent reads via `read_file("/skills/x")`
   and the file it writes via `write_file("/memories/note.md")` are
   served by different stores under the same protocol.

**Non-goals.** Cost optimisation, multi-tenant isolation,
retrieval-augmented context selection, mid-turn model switching, and
sandbox-grade path containment are explicitly *not* in scope. See §9.

---

## 2. Module map

```
coding_agent/
├── __init__.py              # public surface: build_agent, run
├── agent.py                 # factory + invocation config + checkpointer
├── backend.py               # CompositeBackend: default + /memories/ + /skills/
├── model.py                 # resolve_model — provider routing
├── tools.py                 # @tool extras + EXTRA_TOOLS registry
├── tracing.py               # Langfuse handler wiring
├── system_prompt_v2.py      # SYSTEM_PROMPT_V2 — the policy
└── skills/                  # FilesystemBackend root (mounted at /skills/)
```

| File | Role | Stable surface |
|---|---|---|
| `agent.py` | `build_agent()` constructs the compiled graph; `run()` is a one-shot helper; `invocation_config()` builds the per-call config dict (thread_id, callbacks). | `build_agent`, `run`, `invocation_config` |
| `backend.py` | `build_backend(root)` returns `(CompositeBackend, BaseStore)`. Owns the routing table. | `build_backend`, `SKILLS_DIR` |
| `model.py` | `resolve_model(spec)` returns either a passthrough string or an eagerly-built `BaseChatModel` (for `kimi:` / `ollama:`). | `resolve_model`, `DEFAULT_MODEL` |
| `tools.py` | LangChain `@tool` functions layered on top of the deepagents defaults: `think`, `multi_edit`, `web_fetch`, `web_search` (Tavily), read-only `git` + `git_diff`, `run_tests`, `run_type_check`, `run_lint`. | `EXTRA_TOOLS` |
| `tracing.py` | Lazy Langfuse `CallbackHandler`. Returns `[]` when `LANGFUSE_PUBLIC_KEY` is unset. | `langfuse_callbacks` |
| `system_prompt_v2.py` | Static policy: EXPLORE → PLAN → EXECUTE → VERIFY workflow, tool inventory, OS-aware guidance. | `SYSTEM_PROMPT_V2` |
| `skills/` | Filesystem mount for skill markdown the agent loads on-demand. Ships with the package. | (directory) |

Everything in `coding_agent/` is meant to be importable on its own. The
KODA TUI consumes the package through its own adapter
(`koda/adapters/coding_agent.py`); nothing in this package imports
`koda.*`.

---

## 3. Build-time wiring

`build_agent(model, cwd, timeout, inherit_env)` is the single
construction point. The compiled `StateGraph` it returns is fully
self-contained — caller just calls `.invoke(...)` or `.stream(...)`
with a config carrying `configurable.thread_id`.

```
build_agent
  │
  ├─ load_dotenv()                    # best-effort; agent works without it
  │
  ├─ resolve_model(spec)              ─►  str   (most providers)
  │                                       │
  │                                       └─► BaseChatModel  (kimi: / ollama:)
  │
  ├─ build_backend(root)              ─►  CompositeBackend
  │     ├─ default:    LocalShellBackend(root_dir=cwd, virtual_mode=True)
  │     ├─ /memories/: FilesystemBackend(root_dir=<cwd>/.koda/memories/)
  │     └─ /skills/:   FilesystemBackend(root_dir=coding_agent/skills/)
  │
  ├─ _build_checkpointer(root)        ─►  AsyncSqliteSaver at <root>/.koda/checkpoints.db
  │
  ├─ _render_system_prompt(root)      ─►  SYSTEM_PROMPT_V2.format(
  │                                          current_date=…,
  │                                          cwd=…,
  │                                          bootstrap_required=…,
  │                                       )
  │
  └─ create_deep_agent(
         model,             tools=EXTRA_TOOLS,    backend=composite,
         skills=["/skills/"],   memory=["/AGENTS.md"],
         system_prompt=<rendered>,
         checkpointer=sqlite,
         name="coding_agent",
     )                                ─►  CompiledStateGraph
```

Three things are worth explicit calling-out here:

- **`/memories/` is now project-local disk, not a `BaseStore`.** Every
  write under `/memories/<name>.md` lands at
  `<cwd>/.koda/memories/<name>.md`. Switching projects (running `koda`
  in a different cwd) automatically gets a fresh memories tree — no
  cross-project leakage. The on-disk files are `cat`/`git diff`-able,
  which the prior `BaseStore` design wasn't.

- **The system prompt is rendered per session, not static.**
  `_render_system_prompt` substitutes `{current_date}`, `{cwd}`, and
  `{bootstrap_required}` into the `SYSTEM_PROMPT_V2` template at
  `build_agent` time. Session-start granularity (not per-turn) so the
  prompt cache stays hot across turns.

- **`memory=["/AGENTS.md"]` is a deepagents-side feature, not a backend
  route.** `MemoryMiddleware` reads that path through whatever backend
  serves it (the default `LocalShellBackend` here) and injects the
  content into the system prompt under `<agent_memory>`. It's
  read-only context relative to the framework; the agent can still
  `edit_file`/`write_file` to update it. When the file is missing or
  whitespace-only, `_needs_bootstrap` flips `bootstrap_required=true`
  in the prompt and the model writes one on its first turn.

---

## 4. Runtime control flow

A single turn flows like this:

```
caller          .invoke({"messages": [{"role":"user","content":"…"}]},
                         config=invocation_config(thread_id=tid))
   │
   ▼
LangGraph       resume from checkpoint(thread_id) │ or initialise
   │
   ▼
deepagents      MemoryMiddleware → reads /AGENTS.md, injects
loop            SkillsMiddleware → resolves /skills/, injects on demand
                model.bind_tools(...).astream(...)
                  │
                  ├─ text deltas / tool_call chunks → graph state
                  │
                  └─ each tool call dispatches to:
                        execute / read_file / write_file / edit_file /
                        ls / glob / grep / write_todos / task        ──►  backend
                        think / multi_edit / web_fetch / web_search /
                        git / git_diff /
                        run_tests / run_type_check / run_lint        ──►  python @tool fns
   │
   ▼
checkpoint      SqliteSaver writes graph state after every super-step
   │
   ▼
caller          receives final state dict (or streamed events)
```

What this package controls vs. delegates:

| Concern | Owned by `coding_agent/` | Owned by `deepagents` / LangGraph |
|---|---|---|
| Which model is called | yes (`model.py`) | — |
| Which tools exist | partially (`tools.py` + framework defaults) | the built-ins |
| Filesystem semantics | partially (`backend.py` *routes*) | the backend protocol + implementations |
| Tool-call dispatch order, retry, streaming | — | yes |
| System prompt content | yes | — |
| AGENTS.md / skills injection mechanism | — (just declares paths) | yes (`MemoryMiddleware`, `SkillsMiddleware`) |
| Checkpoint persistence | yes (`SqliteSaver` choice + path) | the protocol |
| Tracing | yes (Langfuse callbacks) | — |

If you find yourself wanting to change something not in the left column,
look upstream — `deepagents` and `langgraph` are the source of truth.

---

## 5. The backend mesh

The composite backend is the *only* filesystem the agent sees. Every
read, write, edit, glob, and grep the LLM issues — including the
deepagents built-ins — goes through `CompositeBackend.<op>` first.

```
                  ┌──────────────────────────────────┐
                  │      CompositeBackend            │
                  │                                  │
agent tool call ──►   route by longest-prefix match  │
                  │                                  │
                  │   /skills/*    ──►  Filesystem   ──►  coding_agent/skills/
                  │   /memories/*  ──►  Filesystem   ──►  <cwd>/.koda/memories/
                  │   (default)    ──►  LocalShell   ──►  cwd  (+ subprocess execute)
                  └──────────────────────────────────┘
```

| Route | Backend | Persistence | Mutability | Use |
|---|---|---|---|---|
| default (everything else) | `LocalShellBackend(root_dir=cwd, virtual_mode=True)` | the real filesystem | read/write/execute | the project the agent is operating on |
| `/skills/` | `FilesystemBackend(root_dir=coding_agent/skills/)` | package directory | read-mostly | skill markdown loaded by the framework's `SkillsMiddleware` |
| `/memories/` | `FilesystemBackend(root_dir=<cwd>/.koda/memories/)` | project-local disk | read/write | durable notes the agent authors; survive process restarts |

Two design choices that matter:

**Skills mount is package-local, not project-local.** Skills travel
with the agent, not with the project. Drop a `.md` into
`coding_agent/skills/` and every project gets it. Project-specific
guidance lives in `AGENTS.md` at the project root and is loaded via the
`memory=["/AGENTS.md"]` declaration, not via `/skills/`.

**Memories are project-local files on disk.** Anything the agent
writes under `/memories/<name>.md` lands at
`<cwd>/.koda/memories/<name>.md`. Different projects → isolated
trees automatically (no namespace logic needed). On-disk markdown
beats the prior `BaseStore` design for three reasons: you can
`cat`/`git diff`/share the files, durability is free, and the agent
itself can `read_file`/`edit_file` them through the same interface it
uses for project code.

The `BackendProtocol` is documented at
<https://docs.langchain.com/oss/python/deepagents/backends>. Adding a
new route is one line in `backend.py`.

---

## 6. State & persistence

Three independent persistence surfaces. Each can fail or be wiped
without taking the others down.

### 6.1 LangGraph checkpoints — `<root>/.koda/checkpoints.db`

`AsyncSqliteSaver` (over `aiosqlite`) keyed by `thread_id`. Every
super-step writes the full graph state. `_thread_id_for(root)` derives
a stable thread id from the project cwd hash, so running the agent
twice in the same directory resumes the same conversation. A different
directory is a fresh thread.

`check_same_thread=False` on the sqlite connection — LangGraph may
invoke from different threads under an async loop. The connection is
process-long-lived; we don't `.close()` it. Construction binds to the
running event loop, so `build_agent` is async and the TUI adapter
defers graph construction to its first async path.

### 6.2 Memories — `<root>/.koda/memories/*.md`

For durable notes the agent writes to remember between turns and
sessions. The `/memories/` route in the composite backend points at
`<cwd>/.koda/memories/` via `FilesystemBackend`, so each write under
`/memories/<name>.md` is a real on-disk file in the project.
Different projects get isolated trees by virtue of being in different
directories — no namespace bookkeeping. Markdown is `cat`/`git diff`-
friendly and the agent can `read_file`/`edit_file` them through the
same interface it uses for project source.

### 6.3 AGENTS.md — project context, auto-bootstrapped

`memory=["/AGENTS.md"]` on the deepagents factory wires
`MemoryMiddleware` into the loop. Each turn it re-reads the file (via
the default `LocalShellBackend`) and injects the contents into the
system prompt under `<agent_memory>`.

**Bootstrap.** At `build_agent` time, `_needs_bootstrap(root)` checks
whether `<cwd>/AGENTS.md` is missing or whitespace-only. If so, the
template sets `<env>.bootstrap_required = true` in the rendered system
prompt. The model reads that flag and — before answering the first
user turn — announces "Building understanding of this project — one
moment…", runs an EXPLORE pass (read `pyproject.toml` / `package.json`
/ `README.md`, sample a few source files), then `write_file`s a
populated `AGENTS.md` with YAML frontmatter (`last_updated`,
`version`, `generated_by`). Subsequent sessions see the file is
populated, `bootstrap_required = false`, and the bootstrap block in
the prompt becomes a no-op.

**Updates.** The prompt also carries an update policy: after a change
that contradicts a fact in `AGENTS.md` (renamed a build command, moved
a module, introduced a convention), the agent `edit_file`s the
specific line and bumps the `last_updated` frontmatter to today's date
(`<env>.current_date`) plus the `version` integer. Trivial fact
changes are autonomous; deletions/restructures should be confirmed
with the user first.

The split between the three is:

- **Checkpoint** = "where were we?" — conversation log.
- **Memory store** = "what did I write down?" — agent-authored notes.
- **AGENTS.md** = "what does the user want me to remember about this
  project?" — human-authored guidance.

---

## 7. Model resolution (`model.py`)

`resolve_model(spec)` returns either:

- the raw string spec (the common case — `create_deep_agent` does its
  own provider lookup via `init_chat_model`), or
- a fully-built `BaseChatModel` (for `kimi:` and `ollama:` specs,
  because they need an endpoint + auth header attached).

Resolution table for Ollama-family endpoints (`kimi:` or `ollama:`):

| Env signal | Effective endpoint | Client class |
|---|---|---|
| `OLLAMA_BASE_URL=https://example.com/v1` | as-is | `ChatOpenAI` (OpenAI-shape) |
| `OLLAMA_BASE_URL=http://localhost:11434` | as-is | `ChatOllama` (native shape) |
| `OLLAMA_HOST=somehost` | `http://somehost` | `ChatOllama` |
| `OLLAMA_API_KEY` set, no host | `https://ollama.com/v1` | `ChatOpenAI` |
| nothing | `http://localhost:11434` | `ChatOllama` |

Rule: any base URL ending in `/v1` (or containing `/v1/`) is treated as
OpenAI-shaped and dispatched through `ChatOpenAI` with the api key as
bearer. Everything else goes through `ChatOllama` and the native HTTP
API.

The default spec is `anthropic:claude-sonnet-4-6`, overridable via
`KODA_DEFAULT_MODEL`. The default Ollama model id (used when the user
writes a bare `kimi:` or `ollama:` with no model name) is `kimi-k2.6`.

---

## 8. Tools

The total tool surface is the **deepagents built-ins** plus the
**`EXTRA_TOOLS`** registered here.

| From `deepagents` | From `coding_agent/tools.py` |
|---|---|
| `execute` (shell), `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`, `write_todos`, `task` | `think`, `multi_edit`, `web_fetch`, `web_search`, `git`, `git_diff`, `run_tests`, `run_type_check`, `run_lint` |

Three semantics worth pinning:

**`edit_file` is strict, `multi_edit` is atomic.** Both come from
either the framework (`edit_file`) or this package (`multi_edit`) with
the rule that `old` must match *exactly once* — zero or multiple
matches return a structured error instead of silently editing. For
`multi_edit`, all edits succeed or none are written; the file is left
untouched on first failure.

**`web_search` uses Tavily.** Reads `TAVILY_API_KEY` from env. The
tool requests `search_depth="advanced"` and
`include_answer="advanced"`, so the response carries both a synthesised
answer (rendered first when present) and per-source snippets. Returns
`[error] TAVILY_API_KEY is not set in the environment` when the key is
missing — never silently degrades. A hard 20 s timeout (overridable via
`KODA_WEB_SEARCH_TIMEOUT`) prevents a slow query from wedging a turn.

**`git` is whitelisted.** A single read-only entry-point with a fixed
allowlist of subcommands (`status`, `log`, `blame`, `show`, `branch`,
`tag`, `ls-files`, `rev-parse`, `rev-list`, `describe`, `remote`,
`shortlog`, `reflog`, `config`). Anything that could mutate the repo is
rejected — the model is pushed toward `execute` with explicit intent
for those. `git_diff` is kept as its own tool because its flag shape
(`--cached`, `-- <path>`) is the one models get wrong most often
through a generic interface.

**Runner tools share a shape.** `run_tests` (pytest / jest / cargo / go /
npm-test), `run_type_check` (mypy / pyright / tsc), and `run_lint`
(ruff / eslint) all auto-detect the framework from project files, run
with a hard timeout (600 / 300 / 180 s respectively), and return a
small structured header plus the *tail* of the output (~4 KB) so
failure detail lands in the model's context without bloating it.
Subshells inherit a `_enriched_env()` PATH that prepends
version-manager bin dirs (`.nvm/versions/node/*/bin`, `.cargo/bin`,
`.pyenv/shims`, `.local/bin`, `.bun/bin`, `.deno/bin`) so the agent
can use toolchains it just installed without sourcing rc files.

The framework owns the `execute` (shell) tool; this package doesn't
implement its own. Process-wide approval gating is whatever
`deepagents.backends.LocalShellBackend` provides via `virtual_mode`.

---

## 9. Observability — `tracing.py`

A single LangChain `CallbackHandler` is the entire integration.
`invocation_config()` injects it into the per-call `config` dict so
every LLM call, tool call, and chain step gets traced without graph
changes.

```
LANGFUSE_PUBLIC_KEY set?  ─yes─►  CallbackHandler() (lazy, cached)  ─►  [handler]
                          └─no──►  []  (no-op)
```

The handler is `@lru_cache`'d for process lifetime — `CallbackHandler`
holds a shared Langfuse client and per-call instantiation fragments
traces. Missing/broken Langfuse install logs a `debug` and returns
`None`; the agent runs normally.

`LANGSMITH_TRACING` is handled by LangChain itself and is independent
of Langfuse. Setting `LANGSMITH_TRACING=false` in `.env` disables
LangSmith tracing globally; a stale shell-exported `LANGSMITH_TRACING=true`
will override `.env` unless you `Remove-Item Env:LANGSMITH_TRACING`
or restart your shell.

---

## 10. Configuration surface

Grouped by what they affect.

### Model / endpoint

| Var | Default | Effect |
|---|---|---|
| `KODA_DEFAULT_MODEL` | `anthropic:claude-sonnet-4-6` | Spec used when `build_agent(model=None)` |
| `OLLAMA_BASE_URL` | unset → `http://localhost:11434` or `https://ollama.com/v1` | Where `ollama:` / `kimi:` post |
| `OLLAMA_HOST` | unset | Alternative host (coerced to `http://`) |
| `OLLAMA_API_KEY` | unset | Bearer for Ollama Cloud / OpenAI-shape endpoints |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | unset | Per-provider creds, read by LangChain |

### Tools

| Var | Default | Effect |
|---|---|---|
| `TAVILY_API_KEY` | unset | Required for `web_search`; tool errors out cleanly if missing |

### Observability

| Var | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | unset | Enables Langfuse tracing; absent = no-op |
| `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | unset | Read by Langfuse SDK directly |
| `LANGSMITH_TRACING` | unset | LangChain-side LangSmith toggle (independent of Langfuse) |

### Constants worth knowing

- Shell timeout: **180 s** (passed to `LocalShellBackend`).
- LangGraph recursion limit: **9 999** (deepagents default; `invocation_config` doesn't override).
- Checkpoint location: `<cwd>/.koda/checkpoints.db`.
- Thread id: `sha256(cwd_resolved)[:16]`.
- Memory namespace: `("coding_agent", "memories", sha256(cwd)[:16])`.
- Skills mount: `coding_agent/skills/` (package-local).

---

## 11. Public surface

```python
from coding_agent import build_agent, run

# One-shot:
state = await run("read README and summarise", cwd="/path/to/repo")

# Long-running / interactive:
graph = await build_agent(model="ollama:kimi-k2.6", cwd="/path/to/repo")
config = invocation_config(thread_id="my-thread")
state = await graph.ainvoke({"messages": [{"role": "user", "content": "…"}]}, config=config)
# or stream:
async for chunk in graph.astream(..., config=config):
    ...
```

`build_agent` and `run` are async because the `AsyncSqliteSaver`
checkpointer binds to the running event loop at construction. From a
sync caller, wrap with `asyncio.run(...)`.

**Project selection.** The cwd you pass to `build_agent` (or the
shell cwd if you don't) is the project. From `koda`, use
`koda --cwd /path/to/project` to target a project without `cd`-ing
into it, or just `cd` + run `koda` as usual.

Everything below `build_agent` (the checkpointer, the composite
backend, the model router, the tracing handler) is implementation
detail. Callers should not import directly from `backend.py`,
`model.py`, or `tracing.py` unless they're extending the package.

---

## 12. Extension points

| You want to… | Touch this |
|---|---|
| Add a tool | `tools.py` — add a `@tool` function, append to `EXTRA_TOOLS`, document it in `SYSTEM_PROMPT_V2`. |
| Add a model provider | If LangChain's `init_chat_model` already knows it, *do nothing* — pass the spec through. Only add a branch to `model.py` if the provider needs eager endpoint/auth wiring (like Ollama Cloud). |
| Add a backend route | `backend.py` — extend the `routes={…}` dict on `CompositeBackend`. Longest-prefix wins. |
| Change the system prompt | `system_prompt_v2.py`. |
| Move `/memories/` to a different store | Edit the `/memories/` entry in `backend.py:build_backend`. To go back to a LangGraph `BaseStore`, swap `FilesystemBackend(...)` for `StoreBackend(namespace=…)` and thread the store through to `create_deep_agent(store=…)` in `agent.py`. |
| Swap the checkpointer | Replace `_build_checkpointer` in `agent.py` with a different `BaseCheckpointSaver` (e.g. `PostgresSaver`). |
| Add a callback / metric | `tracing.py` — append handlers to `langfuse_callbacks()`'s return list, or build a sibling function and merge in `invocation_config`. |
| Ship a skill | Drop a `.md` into `coding_agent/skills/`. It will be visible at `/skills/<name>.md`. |

---

## 13. Known limitations & non-goals

Explicit trade-offs, not bugs.

1. **No path sandboxing.** The default `LocalShellBackend` accepts
   arbitrary paths inside its `root_dir`; outside paths are blocked by
   `virtual_mode=True` but the `execute` tool can do anything the
   process can do. Acceptable for a developer-owned CLI; not acceptable
   for a multi-tenant deployment.

2. **AsyncSqlite connection is never closed.** Process-long-lived;
   relies on OS cleanup. Fine for a TUI lifetime, would matter inside
   a long-running daemon hosting many graphs.

3. **AGENTS.md cascading is single-file.** The deepagents
   `MemoryMiddleware` reads the listed paths in order. We declare only
   `/AGENTS.md` (project root). If you want a user-level
   `~/.koda/AGENTS.md` or ancestor traversal, extend the `memory=[…]`
   list in `build_agent`.

4. **Bootstrap is prompt-driven, not enforced.** `bootstrap_required`
   is a flag in the system prompt; the model is *instructed* to write
   `AGENTS.md` on its first turn. There's no Python-side guard that
   refuses to answer until the file exists. If the model ignores the
   instruction (or you `Ctrl+C` mid-bootstrap), the file just won't get
   written that turn. Re-launching with no AGENTS.md will retry.

5. **No mid-turn model switching.** A new model = a new `build_agent`
   call. The compiled graph holds a bound model; rebuilding is cheap
   (no LLM call, just object construction).

6. **Session-start date, not per-turn.** `<env>.current_date` is
   captured once at `build_agent` time. A session crossing midnight
   has a stale date until the next launch. Per-turn injection would
   invalidate the prompt cache for a 1-bit win.

---

## 14. End-to-end picture

```
                  ┌─────────────────────────────────────┐
                  │                .env                  │
                  │  ANTHROPIC/OPENAI/GOOGLE_API_KEY     │
                  │  OLLAMA_BASE_URL / OLLAMA_API_KEY    │
                  │  TAVILY_API_KEY                      │
                  │  LANGFUSE_PUBLIC_KEY / SECRET_KEY    │
                  │  LANGSMITH_TRACING=false             │
                  │  KODA_DEFAULT_MODEL                  │
                  └────────────────┬─────────────────────┘
                                   │  load_dotenv() in build_agent
                                   ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                  coding_agent/agent.py                       │
   │                                                              │
   │   build_agent(model, cwd, timeout, inherit_env):             │
   │     model     = resolve_model(spec)                          │  ← model.py
   │     backend,                                                 │
   │       store   = build_backend(root, ...)                     │  ← backend.py
   │     ckpt      = SqliteSaver(<root>/.koda/checkpoints.db)     │
   │     return create_deep_agent(                                │
   │       model, backend, store, checkpointer=ckpt,              │
   │       tools=EXTRA_TOOLS,        ─────────────────────────►   │  ← tools.py
   │       memory=["/AGENTS.md"],                                 │
   │       skills=["/skills/"],      ─────────────────────────►   │  ← backend.py → FilesystemBackend
   │       system_prompt=SYSTEM_PROMPT_V2,  ──────────────────►   │  ← system_prompt_v2.py
   │     )                                                        │
   │                                                              │
   │   invocation_config(thread_id):                              │
   │     {"callbacks": langfuse_callbacks(),   ──────────────►    │  ← tracing.py
   │      "configurable": {"thread_id": tid}}                     │
   └──────────────────────────────────┬───────────────────────────┘
                                      │
                                      ▼
                          CompiledStateGraph (deepagents + LangGraph)
                                      │
                              ┌───────┼────────┐
                              ▼       ▼        ▼
                         MemoryMW  SkillsMW   ToolNode + LLM
                              │       │        │
                              ▼       ▼        ▼
                          /AGENTS.md /skills/  composite backend ops
                                                + Python @tool fns
                                                  (think / web /
                                                   git / tests)
                                      │
                                      ▼
                          SqliteSaver writes after every step
```

---

## 15. Where to look first

- Adding a tool → `tools.py` (decorate, append to `EXTRA_TOOLS`,
  document in `SYSTEM_PROMPT_V2`).
- Changing model routing → `model.py:resolve_model`.
- Adding a backend route or swapping a store → `backend.py:build_backend`.
- Changing prompt policy → `system_prompt_v2.py`.
- Adding tracing / observability → `tracing.py:langfuse_callbacks` and
  the `callbacks` merge in `agent.py:invocation_config`.
- Changing where conversations or memories persist →
  `agent.py:_build_checkpointer` and `backend.py:build_backend(store=…)`.
- Adding a skill → drop a `.md` into `coding_agent/skills/`.
