# PI, Amp, KODA: prompt/tool/loop notes

Written on 2026-04-15.

## Status

- `pi` is installed locally.
- `amp` is not installed locally on this machine (`where.exe amp` returned `none`), so the Amp section below uses official public docs instead of local source.
- I treated your `agent_workpspace` spelling as the existing repo folder `agent_workspace/`.

## Short take

- `KODA` is a custom prompt wrapped around `deepagents`.
- `deepagents` is middleware-driven: prompt layers and tool surfaces are assembled by the graph and middleware stack.
- `PI` is prompt-builder-driven: it explicitly rebuilds the system prompt from active tools, guidelines, context files, skills, and cwd/date.
- `Amp` publicly documents modes, AGENTS.md, permissions, MCP, toolboxes, subagents, Oracle, Librarian, Painter, queueing, and handoff, but I could not find the hidden built-in coding-agent system prompt or built-in tool JSON schemas in public docs.

## 1. KODA

### Prompt

KODA's main system prompt is handwritten in `koda/agents/deep.py`. It defines:

- identity: terminal coding teammate, not chatbot
- behavior: direct, concise, proactive
- workflow: break work into steps, read before edit, run tests after changes
- safety: ask before destructive commands
- workspace rule: read from the project, but write to `../agent_workspace`

Key refs:

- `koda/agents/deep.py:24`
- `koda/agents/deep.py:25`
- `koda/agents/deep.py:37`
- `koda/agents/deep.py:44`
- `koda/agents/deep.py:54`
- `koda/agents/deep.py:55`

The prompt is parameterized with live environment info:

- `koda/agents/deep.py:66`
- `koda/agents/deep.py:70`

### Tools

KODA itself adds only two custom tools:

- `web_search`
- `read_webpage`

Refs:

- `koda/agents/deep.py:81`
- `koda/agents/deep.py:82`
- `koda/agents/deep.py:109`
- `koda/agents/deep.py:110`
- `koda/agents/deep.py:156`
- `koda/agents/deep.py:158`

But KODA also uses `LocalShellBackend`, so the effective tool surface is larger than those two tools. Filesystem tools, shell execution, subagent tools, and summarization come from upstream `deepagents`.

Refs:

- `koda/agents/deep.py:159`
- `koda/agents/deep.py:160`
- `koda/agents/deep.py:162`

### Loop and context

KODA does not implement its own low-level LLM turn loop here; it delegates to `deepagents`.

What KODA does add locally is PI-like session-tree context management:

- tree-shaped JSONL session
- entry types `header | message | branch_summary | compaction`
- active-path compaction replaces earlier history with one system summary

Refs:

- `koda/session.py:4`
- `koda/session.py:7`
- `koda/session.py:13`
- `koda/session.py:127`
- `koda/session.py:144`
- `koda/session.py:153`
- `koda/session.py:211`
- `koda/session.py:215`
- `koda/session.py:220`
- `koda/session.py:221`

## 2. deepagents under KODA

### Prompt layering

The most important upstream fact is in `deepagents/graph.py`:

- `BASE_AGENT_PROMPT` exists upstream
- `create_deep_agent(...)` appends that base prompt after the caller's `system_prompt`

Refs:

- `.venv/Lib/site-packages/deepagents/graph.py:49`
- `.venv/Lib/site-packages/deepagents/graph.py:588`
- `.venv/Lib/site-packages/deepagents/graph.py:591`
- `.venv/Lib/site-packages/deepagents/graph.py:594`
- `.venv/Lib/site-packages/deepagents/graph.py:600`

So KODA's effective prompt is roughly:

1. KODA custom prompt
2. deepagents base prompt
3. middleware-appended prompt fragments

### Middleware stack

The default deep-agent stack includes:

- `TodoListMiddleware`
- `FilesystemMiddleware`
- `SubAgentMiddleware`
- summarization middleware
- `PatchToolCallsMiddleware`
- optional async subagents
- optional HITL and permissions middleware

Refs:

- `.venv/Lib/site-packages/deepagents/graph.py:540`
- `.venv/Lib/site-packages/deepagents/graph.py:546`
- `.venv/Lib/site-packages/deepagents/graph.py:550`
- `.venv/Lib/site-packages/deepagents/graph.py:560`
- `.venv/Lib/site-packages/deepagents/graph.py:561`
- `.venv/Lib/site-packages/deepagents/graph.py:568`
- `.venv/Lib/site-packages/deepagents/graph.py:583`
- `.venv/Lib/site-packages/deepagents/graph.py:586`

### Filesystem / shell tool schema

Deepagents defines explicit schemas for:

- `ls`
- `read_file`
- `write_file`
- `edit_file`
- `glob`
- `grep`
- `execute`

Refs:

- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:121`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:127`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:141`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:148`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:160`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:167`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:179`

The tool descriptions are very opinionated:

- use `ls` before deep file work
- paginate reads
- read before edit
- prefer editing existing files
- use absolute paths starting with `/`

Refs:

- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:189`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:194`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:216`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:218`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:300`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py:325`

### Subagents and compaction tools

Deepagents also exposes:

- `task` via `TaskToolSchema`
- async subagent tools like `start_async_task`, `check_async_task`, `update_async_task`
- `compact_conversation`

Refs:

- `.venv/Lib/site-packages/deepagents/middleware/subagents.py:140`
- `.venv/Lib/site-packages/deepagents/middleware/subagents.py:152`
- `.venv/Lib/site-packages/deepagents/middleware/subagents.py:262`
- `.venv/Lib/site-packages/deepagents/middleware/subagents.py:394`
- `.venv/Lib/site-packages/deepagents/middleware/async_subagents.py:129`
- `.venv/Lib/site-packages/deepagents/middleware/async_subagents.py:164`
- `.venv/Lib/site-packages/deepagents/middleware/async_subagents.py:361`
- `.venv/Lib/site-packages/deepagents/middleware/summarization.py:94`
- `.venv/Lib/site-packages/deepagents/middleware/summarization.py:98`
- `.venv/Lib/site-packages/deepagents/middleware/summarization.py:1283`

### Upstream CLI prompt template

The upstream DeepAgents CLI ships a separate prompt template in `deepagents_cli/system_prompt.md`. It strongly pushes:

- no preamble
- use specialized tools over shell
- parallel tool calls when possible
- absolute paths
- read before edit
- paginate large reads

Refs:

- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md:7`
- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md:57`
- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md:61`
- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md:67`
- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md:91`
- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md:100`
- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md:108`

The CLI code builds that prompt differently for interactive vs headless mode:

- interactive: keep user informed, ask if ambiguous
- headless: do not ask, make assumptions, use non-interactive commands

Refs:

- `.venv/Lib/site-packages/deepagents_cli/agent.py:472`
- `.venv/Lib/site-packages/deepagents_cli/agent.py:512`
- `.venv/Lib/site-packages/deepagents_cli/agent.py:520`
- `.venv/Lib/site-packages/deepagents_cli/agent.py:533`
- `.venv/Lib/site-packages/deepagents_cli/agent.py:544`

### Security note

KODA uses `LocalShellBackend(..., virtual_mode=False)`, and upstream docs explicitly warn that this backend provides unrestricted host shell execution.

Refs:

- `.venv/Lib/site-packages/deepagents/backends/local_shell.py:1`
- `.venv/Lib/site-packages/deepagents/backends/local_shell.py:28`
- `.venv/Lib/site-packages/deepagents/backends/local_shell.py:80`
- `.venv/Lib/site-packages/deepagents/backends/local_shell.py:219`
- `.venv/Lib/site-packages/deepagents/backends/local_shell.py:223`

## 3. PI

Local install:

- package `@mariozechner/pi-coding-agent`
- version `0.67.1`
- in the shorthand refs below, `.../` means the local PI install root under `&lt;pi-install-dir&gt;`

Refs:

- `&lt;pi-install-dir&gt;\package.json:3`
- `&lt;pi-install-dir&gt;\package.json:4`

### Prompt construction

PI's system prompt builder is unusually explicit.

If a custom prompt exists, PI:

- uses it
- appends extra system prompt text
- appends project context files
- appends skills when `read` is available
- appends current date and cwd last

If no custom prompt exists, PI builds a default prompt that includes:

- identity as an expert coding assistant inside PI
- visible active tools
- tool-derived guidelines
- PI docs paths for PI-specific questions
- context files, skills, date, cwd

Refs:

- `.../dist/core/system-prompt.js:15`
- `.../dist/core/system-prompt.js:20`
- `.../dist/core/system-prompt.js:28`
- `.../dist/core/system-prompt.js:33`
- `.../dist/core/system-prompt.js:44`
- `.../dist/core/system-prompt.js:63`
- `.../dist/core/system-prompt.js:67`
- `.../dist/core/system-prompt.js:79`
- `.../dist/core/system-prompt.js:81`
- `.../dist/core/system-prompt.js:86`
- `.../dist/core/system-prompt.js:89`
- `.../dist/core/system-prompt.js:112`

Two especially important PI prompt facts:

- default active tools are `read,bash,edit,write`
- prompt guidelines change depending on the active tool mix

Refs:

- `.../dist/core/system-prompt.js:44`
- `.../dist/core/system-prompt.js:64`
- `.../dist/core/system-prompt.js:67`

### Tool schema

PI's built-in tool surface is:

- `read`
- `bash`
- `edit`
- `write`
- `grep`
- `find`
- `ls`

Official README refs:

- `.../README.md:521`
- `.../README.md:524`

Local schema refs:

- `.../dist/core/tools/read.js:13`
- `.../dist/core/tools/bash.js:23`
- `.../dist/core/tools/edit.js:11`
- `.../dist/core/tools/edit.js:17`
- `.../dist/core/tools/write.js:11`
- `.../dist/core/tools/grep.js:13`
- `.../dist/core/tools/find.js:16`
- `.../dist/core/tools/ls.js:10`

The prompt-facing effect of those definitions is very PI-like:

- `read` over `cat`
- `read` paginates/truncates
- `edit` is exact-string replacement with multiple disjoint edits in one call
- `write` is for new files or total rewrites
- `grep/find/ls` are first-class exploration tools

### Agentic loop

PI's low-level loop in `pi-agent-core/dist/agent-loop.js` is clear and worth copying conceptually.

From the prompt point of view:

1. inject pending steering messages
2. stream assistant response
3. detect tool calls from assistant content
4. execute tool calls
5. append tool results
6. re-check steering queue
7. if the agent would stop, check follow-up queue
8. continue until no tool work and no follow-ups remain

Refs:

- `.../pi-agent-core/dist/agent-loop.js:77`
- `.../pi-agent-core/dist/agent-loop.js:80`
- `.../pi-agent-core/dist/agent-loop.js:82`
- `.../pi-agent-core/dist/agent-loop.js:85`
- `.../pi-agent-core/dist/agent-loop.js:103`
- `.../pi-agent-core/dist/agent-loop.js:111`
- `.../pi-agent-core/dist/agent-loop.js:115`
- `.../pi-agent-core/dist/agent-loop.js:122`
- `.../pi-agent-core/dist/agent-loop.js:125`

At the model-call boundary PI does:

- optional `transformContext`
- `convertToLlm(messages)`
- then builds `{ systemPrompt, messages, tools }`

Refs:

- `.../pi-agent-core/dist/agent-loop.js:140`
- `.../pi-agent-core/dist/agent-loop.js:143`
- `.../pi-agent-core/dist/agent-loop.js:147`
- `.../pi-agent-core/dist/agent-loop.js:149`

Tool execution is configurable:

- parallel or sequential
- default is parallel
- optional `beforeToolCall`
- optional `afterToolCall`

Refs:

- `.../pi-agent-core/dist/agent.js:124`
- `.../pi-agent-core/dist/agent-loop.js:222`
- `.../pi-agent-core/dist/agent-loop.js:224`
- `.../pi-agent-core/dist/agent-loop.js:302`
- `.../pi-agent-core/dist/agent-loop.js:358`

PI also has explicit queue APIs:

- `steer(message)`
- `followUp(message)`

Refs:

- `.../pi-agent-core/dist/agent.js:163`
- `.../pi-agent-core/dist/agent.js:167`
- `.../dist/core/agent-session.js:875`
- `.../dist/core/agent-session.js:882`
- `.../dist/core/agent-session.js:891`
- `.../dist/core/agent-session.js:898`

### Compaction and tree

PI has first-class `/tree` and `/compact`.

- `/tree` lets you navigate a session tree and optionally summarize abandoned branches
- `/compact` is lossy, but full JSONL history remains

Refs:

- `.../README.md:177`
- `.../README.md:179`
- `.../README.md:239`
- `.../README.md:255`
- `.../README.md:259`
- `.../docs/tree.md:79`
- `.../docs/compaction.md:3`

Its compaction system uses a separate summarizer prompt:

- system prompt: context summarization assistant
- structured checkpoint summary
- update prompt when a previous summary exists

Refs:

- `.../dist/core/compaction/utils.js:150`
- `.../dist/core/compaction/compaction.js:358`
- `.../dist/core/compaction/compaction.js:390`

## 4. Amp

### What I could verify

I could verify Amp's public architecture from official docs, but not the hidden built-in agent prompt.

Useful public docs:

- `https://ampcode.com/manual`
- `https://ampcode.com/models`
- `https://ampcode.com/chronicle`
- `https://ampcode.com/news/oracle`

### Public prompt surfaces

Amp exposes several prompt-facing control surfaces publicly:

- `AGENTS.md`
- referenced files via `@`
- conditional guidance via `globs`
- skills
- MCP loaded through skills or config
- queueing messages
- handoff to a new thread

Important manual refs:

- `https://ampcode.com/manual` lines 109-127
- `https://ampcode.com/manual` lines 176-204
- `https://ampcode.com/manual` lines 273-275
- `https://ampcode.com/manual` lines 215-226

Interesting public artifact: the manual page itself begins with an `INSTRUCTIONS FOR LLMs` block. That is not Amp's private coding-agent system prompt, but it is a real public prompt artifact.

### Modes, subagents, and tools

The clearest official summary is on the Models page:

- mode = `System Prompt + Tools + Model`
- current public modes on 2026-04-15:
  - `smart`: Claude Opus 4.6
  - `rush`: Claude Haiku 4.5
  - `deep`: GPT-5.4
- specialized subagents/models:
  - `Search`: Gemini 3 Flash
  - `Oracle`: GPT-5.4
  - `Librarian`: Claude Sonnet 4.6
  - `Painter`: Gemini 3 Pro Image
  - `Handoff`: Gemini 3 Flash

Ref:

- `https://ampcode.com/models` lines 8-35

The public docs also say:

- built-in tools can be inspected with `amp tools list`
- built-in permission allowlists can be inspected with `amp permissions list --builtin`
- toolboxes can emit line-based descriptions or JSON schema
- MCP `includeTools` controls which tools are visible

Refs:

- `https://ampcode.com/manual` lines 276-290
- `https://ampcode.com/manual` lines 291-330
- `https://ampcode.com/manual` lines 383-417
- `https://ampcode.com/manual` lines 510-520

Amp subagents are public and prompt-facing:

- Task tool subagents have their own context windows
- Oracle is an explicit tool
- Librarian is a remote-code research subagent

Refs:

- `https://ampcode.com/manual` lines 418-425
- `https://ampcode.com/manual` lines 426-438
- `https://ampcode.com/manual` lines 439-448

### Context strategy

Amp's biggest conceptual difference from PI/KODA is context strategy:

- Amp explicitly prefers small threads
- Amp uses `handoff` instead of compaction
- Amp can reference/search old threads to rehydrate context

Refs:

- `https://ampcode.com/manual` lines 215-226
- `https://ampcode.com/manual` lines 227-248

### What I could not verify

I could not verify any of these from local source or public docs:

- the real built-in coding-agent system prompt
- the exact built-in tool JSON schemas
- the exact low-level per-turn execution loop

So the right conclusion is:

- Amp's public prompt architecture is visible
- Amp's hidden implementation details are not

## 5. Comparison

| System | Prompt style | Tool style | Context style |
| --- | --- | --- | --- |
| KODA | local handwritten prompt | 2 custom tools + inherited deepagents tools | PI-like tree + compaction |
| deepagents | base prompt + middleware prompt fragments | explicit schemas in middleware | middleware summarization / HITL / permissions |
| PI | explicit prompt builder from active tools and context | explicit tool schemas, snippets, guidelines | `/tree` + `/compact` |
| Amp | public AGENTS/skills/MCP/modes, hidden core prompt | built-ins public at CLI level, schemas not public | handoff + thread references/search, no compaction |

## 6. My main synthesis

- If you want the cleanest example of "tool schema and prompt builder are the product", PI is the best specimen.
- If you want the cleanest example of "prompt is assembled by middleware layers", deepagents is the best specimen.
- KODA currently sits closer to deepagents than to PI: custom local prompt on top, but inherited runtime/tool behavior underneath.
- Amp is the least inspectable from source here, but the public docs still show the major design direction very clearly: AGENTS.md, skills, MCP, subagents, permissions, and handoff rather than compaction.

## 7. Primary sources

Local:

- `koda/agents/deep.py`
- `koda/session.py`
- `.venv/Lib/site-packages/deepagents/graph.py`
- `.venv/Lib/site-packages/deepagents/middleware/filesystem.py`
- `.venv/Lib/site-packages/deepagents/middleware/subagents.py`
- `.venv/Lib/site-packages/deepagents/middleware/async_subagents.py`
- `.venv/Lib/site-packages/deepagents/middleware/summarization.py`
- `.venv/Lib/site-packages/deepagents/backends/local_shell.py`
- `.venv/Lib/site-packages/deepagents_cli/system_prompt.md`
- `.venv/Lib/site-packages/deepagents_cli/agent.py`
- `&lt;pi-install-dir&gt;\README.md`
- `&lt;pi-install-dir&gt;\dist\core\system-prompt.js`
- `&lt;pi-install-dir&gt;\dist\core\agent-session.js`
- `&lt;pi-install-dir&gt;\node_modules\@mariozechner\pi-agent-core\dist\agent.js`
- `&lt;pi-install-dir&gt;\node_modules\@mariozechner\pi-agent-core\dist\agent-loop.js`

Web:

- `https://ampcode.com/manual`
- `https://ampcode.com/models`
- `https://ampcode.com/chronicle`
- `https://ampcode.com/news/oracle`
