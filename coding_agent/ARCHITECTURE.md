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
| `tools.py` | LangChain `@tool` functions layered on top of the deepagents defaults: `think`, `multi_edit`, `web_fetch`, `web_search` (Tavily), git read-only, `run_tests`. | `EXTRA_TOOLS` |
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
  ├─ build_backend(root)              ─►  (CompositeBackend, BaseStore)
  │     ├─ default:    LocalShellBackend(root_dir=cwd, virtual_mode=True)
  │     ├─ /memories/: StoreBackend(namespace=per-project)
  │     └─ /skills/:   FilesystemBackend(root_dir=coding_agent/skills/)
  │
  ├─ _build_checkpointer(root)        ─►  SqliteSaver at <root>/.koda/checkpoints.db
  │
  └─ create_deep_agent(
         model,             tools=EXTRA_TOOLS,    backend=composite,
         skills=["/skills/"],   memory=["/AGENTS.md"],
         system_prompt=SYSTEM_PROMPT_V2,
         checkpointer=sqlite, store=base_store,
         name="coding_agent",
     )                                ─►  CompiledStateGraph
```

Two things are worth explicit calling-out here:

- **The `store` passed to `create_deep_agent` is the same instance
  returned by `build_backend`.** `StoreBackend` doesn't hold its own
  store handle — it pulls one off the LangGraph `Runtime` at tool-call
  time. If you wired a different store into `create_deep_agent`, writes
  under `/memories/` would land in a different namespace tree than the
  one `_project_namespace` set up. The factory keeps them in sync.

- **`memory=["/AGENTS.md"]` is a deepagents-side feature, not a backend
  route.** `MemoryMiddleware` reads that path through whatever backend
  serves it (the default `LocalShellBackend` here) and injects the
  content into the system prompt under `<agent_memory>`. It's
  read-only context, not a writable surface. The `/memories/` route in
  the composite backend is the writable counterpart.

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
                        git_status / git_diff / git_log / git_blame /
                        run_tests                                    ──►  python @tool fns
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
                  │   /memories/*  ──►  StoreBackend ──►  BaseStore (per-project ns)
                  │   (default)    ──►  LocalShell   ──►  cwd  (+ subprocess execute)
                  └──────────────────────────────────┘
```

| Route | Backend | Persistence | Mutability | Use |
|---|---|---|---|---|
| default (everything else) | `LocalShellBackend(root_dir=cwd, virtual_mode=True)` | the real filesystem | read/write/execute | the project the agent is operating on |
| `/skills/` | `FilesystemBackend(root_dir=coding_agent/skills/)` | package directory | read-mostly | skill markdown loaded by the framework's `SkillsMiddleware` |
| `/memories/` | `StoreBackend(namespace=…)` | LangGraph `BaseStore` (in-memory by default) | append-mostly | durable notes the agent authors across turns and projects |

Two design choices that matter:

**Skills mount is package-local, not project-local.** Skills travel
with the agent, not with the project. Drop a `.md` into
`coding_agent/skills/` and every project gets it. Project-specific
guidance lives in `AGENTS.md` at the project root and is loaded via the
`memory=["/AGENTS.md"]` declaration, not via `/skills/`.

**Memory namespace is hashed cwd.** `_project_namespace` returns
`("coding_agent", "memories", sha256(cwd)[:16])`. Same project → same
slice of the store; different projects → isolated slices. The store
itself defaults to `InMemoryStore` (lives for the process lifetime);
swap for a Postgres/Redis-backed `BaseStore` to get true cross-process
durability.

The `BackendProtocol` is documented at
<https://docs.langchain.com/oss/python/deepagents/backends>. Adding a
new route is one line in `backend.py`.

---

## 6. State & persistence

Three independent persistence surfaces. Each can fail or be wiped
without taking the others down.

### 6.1 LangGraph checkpoints — `<root>/.koda/checkpoints.db`

`SqliteSaver` keyed by `thread_id`. Every super-step writes the full
graph state. `_thread_id_for(root)` derives a stable thread id from
the project cwd hash, so running the agent twice in the same directory
resumes the same conversation. A different directory is a fresh thread.

`check_same_thread=False` on the sqlite connection — LangGraph may
invoke from different threads under an async loop. The connection is
process-long-lived; we don't `.close()` it.

### 6.2 Memory store — `/memories/...` via `StoreBackend`

For notes the agent writes to remember between turns. Backed by
whatever `BaseStore` instance was passed into `build_backend`. Default
`InMemoryStore` is fine for local dev; swap in
`langgraph.store.postgres.PostgresStore` (or similar) for durability.
Namespace partitions per-project so memories don't leak between repos.

### 6.3 AGENTS.md — read-only context injection

`memory=["/AGENTS.md"]` on the deepagents factory wires
`MemoryMiddleware` into the loop. Each turn it re-reads the file (via
the backend) and injects the contents into the system prompt under
`<agent_memory>`. A missing file is silently skipped. **Edits the
agent makes to `/AGENTS.md` write to the real `<project>/AGENTS.md`**
through the default `LocalShellBackend`.

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
| `execute` (shell), `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`, `write_todos`, `task` | `think`, `multi_edit`, `web_fetch`, `web_search`, `git_status`, `git_diff`, `git_log`, `git_blame`, `run_tests` |

Two semantics worth pinning:

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
missing — never silently degrades.

**`run_tests` auto-detects** pytest / jest / cargo / go / npm-test and
pipes the output through a small summariser. Subshells inherit a
`_enriched_env()` PATH that prepends version-manager bin dirs
(`.nvm/versions/node/*/bin`, `.cargo/bin`, `.pyenv/shims`, `.local/bin`,
`.bun/bin`, `.deno/bin`) so the agent can use toolchains it just
installed without sourcing rc files.

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
state = run("read README and summarise", cwd="/path/to/repo")

# Long-running / interactive:
graph = build_agent(model="ollama:kimi-k2.6", cwd="/path/to/repo")
config = invocation_config(thread_id="my-thread")
state = graph.invoke({"messages": [{"role": "user", "content": "…"}]}, config=config)
# or stream:
for chunk in graph.stream(..., config=config):
    ...
```

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
| Swap the memory store | Pass `store=YourStore()` into `build_backend` (and propagate the same instance into `create_deep_agent(store=…)` in `agent.py`). |
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

2. **`InMemoryStore` is the default for `/memories/`.** Memory survives
   between threads within a single process but not across restarts.
   Pass a durable `BaseStore` into `build_backend` to fix this — the
   factory plumbs it through correctly.

3. **`SqliteSaver` connection is never closed.** Process-long-lived;
   relies on OS cleanup. Fine for a TUI lifetime, would matter inside
   a long-running daemon hosting many graphs.

4. **AGENTS.md cascading is single-file.** The deepagents
   `MemoryMiddleware` reads the listed paths in order. We declare only
   `/AGENTS.md` (project root). If you want a user-level
   `~/.koda/AGENTS.md` or ancestor traversal, extend the `memory=[…]`
   list in `build_agent`.

5. **No mid-turn model switching.** A new model = a new `build_agent`
   call. The compiled graph holds a bound model; rebuilding is cheap
   (no LLM call, just object construction).

6. **`KODA_DISABLE_BOOTSTRAP` and other historical envs are gone.**
   The earlier hand-rolled `CodingAgent` had project-bootstrap logic,
   character-based compaction, and depth-1 explore subagents. Those
   responsibilities now live in `deepagents` / LangGraph and are
   configured via their primitives (`memory=`, the recursion limit,
   `task` / subagents). If you need to tweak compaction or context
   budgets, that's an upstream knob.

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
