# KODA — Implementation Snapshot (2026-06-26)

A record of every change implemented across recent sessions. Each entry
lists the commit, the files touched, a short description, and the exact
revert command. Use this file to roll back any individual change without
affecting the others.

All commits are on `origin/main`. Reverts are safe to apply in any order —
no commit depends on another (each is self-contained).

---

## 1. Permission system — auto-approve shell in ACCEPT-EDITS (except `rm`)

**Commit:** `ee75ea6`
**Message:** `feat(permissions): auto-approve shell in ACCEPT-EDITS except rm; honor session-allow first`

**What it does:**
- In ACCEPT-EDITS mode, file edits are auto-approved; shell commands are
  auto-approved **except** `rm` (and `rm -rf`), which still asks.
- A session "always allow" decision now takes priority over the ACCEPT-EDITS
  `rm` guard — so an explicit "always allow" for a tool persists even in
  edits mode.
- Added `_is_rm_command()` helper + `_RM_CMD_PATTERN` regex that detects
  `rm` as a command verb (not as an argument like `grep rm`).

**Files changed:**
- `koda/tools/permissions.py` (+50 lines) — new helper, reordered `decide()`
- `tests/test_permission_modal.py` (+25 lines) — updated assertions

**Revert:**
```
git revert ee75ea6 --no-edit
```
Or to drop without a revert commit:
```
git checkout ee75ea6^ -- koda/tools/permissions.py tests/test_permission_modal.py
```

---

## 2. Skills — bundle official Anthropic skills

**Commit:** `6e83eb4`
**Message:** `feat(skills): bundle official Anthropic skills (frontend-design, mcp-builder, webapp-testing)`

**What it does:**
- Downloaded 3 official Anthropic skills from `github.com/anthropics/skills`
  into `coding_agent/skills/`:
  - `frontend-design/SKILL.md` + `LICENSE.txt` (Apache-2.0)
  - `mcp-builder/SKILL.md` + `LICENSE.txt`
  - `webapp-testing/SKILL.md` + `LICENSE.txt`
- deepagents `SkillsMiddleware` was already wired (`skills=["/skills/"]`),
  so these are drop-in.

**Files added (new):**
- `coding_agent/skills/frontend-design/SKILL.md`, `LICENSE.txt`
- `coding_agent/skills/mcp-builder/SKILL.md`, `LICENSE.txt`
- `coding_agent/skills/webapp-testing/SKILL.md`, `LICENSE.txt`

**Revert:**
```
git revert 6e83eb4 --no-edit
```
Or to remove the files outright:
```
git rm -r coding_agent/skills/frontend-design coding_agent/skills/mcp-builder coding_agent/skills/webapp-testing
git commit -m "revert: drop bundled Anthropic skills"
```

---

## 3. MCP — Context7 integration via langchain-mcp-adapters

**Commit:** `322c2fe`
**Message:** `feat(mcp): add Context7 MCP integration via langchain-mcp-adapters`

**What it does:**
- New `coding_agent/mcp.py` (187 lines) — MCP tool loader using
  `langchain-mcp-adapters`' `MultiServerMCPClient`. Reads `.mcp.json` from
  project root; translates stdio/http/sse specs. Fully graceful: missing
  package / no config / server down → returns `[]`; never blocks startup.
  Falls back to Context7 via `CONTEXT7_API_KEY` env var when no `.mcp.json`.
- Wired into `build_agent` (`coding_agent/agent.py`): `mcp_tools = await
  load_mcp_tools(root)` merged before `create_deep_agent`.
- Added `.mcp.json` at project root with Context7 remote HTTP config
  (`https://mcp.context7.com/mcp`).
- Replaced stale "Playwright MCP" block in system prompt with Context7
  tool guidance (`resolve-library-id` / `query-docs`).
- Added `langchain-mcp-adapters>=0.3,<0.4` to `pyproject.toml`.

**Files changed:**
- `.mcp.json` (new, +8 lines)
- `coding_agent/agent.py` (+14 lines) — MCP wiring
- `coding_agent/mcp.py` (new, +187 lines)
- `coding_agent/system_prompt_v2.py` (+25/-18 lines) — Context7 guidance
- `pyproject.toml` (+3 lines) — dependency

**Revert:**
```
git revert 322c2fe --no-edit
```
Then optionally remove the installed dependency:
```
.venv/bin/pip uninstall langchain-mcp-adapters
```

---

## 4. TUI — double-click to copy + clickable hyperlinks

**Commit:** `717ec68`
**Message:** `feat(tui): double-click to copy messages + clickable hyperlinks`

**What it does:**
- Double-click any message copies its full text to the OS clipboard via
  `pyperclip` (fallback: Textual OSC 52). A toast confirms "Copied N chars".
- Bare URLs in assistant and user messages are wrapped in Rich
  `[link=url]url[/]` markup → modern terminals render them as OSC 8
  hyperlinks that are natively clickable (no app-side handler needed).
- `_linkify()` helper + `_URL_RE` regex (excludes `[`/`]` to avoid crossing
  Textual markup tag boundaries).

**Files changed:**
- `koda/tui/widgets/messages.py` (+67/-4 lines) — `_linkify`, `_URL_RE`,
  `BaseMessage.on_click`, linkified `AssistantMessage._flush`/`set_text`,
  `UserMessage.__init__`

**Revert:**
```
git revert 717ec68 --no-edit
```

---

## 5. TUI — indeterminate progress bar during /compact

**Commit:** `25028a5`
**Message:** `feat(tui): show indeterminate progress bar during /compact`

**What it does:**
- `/compact` now mounts a Textual `LoadingIndicator` (animated indeterminate
  bar) into the message stream while the summary model runs, so the user
  sees compaction is in progress (it can take several seconds).
- The bar + label are removed in a `finally` block — success or failure —
  so the UI never leaves a dangling spinner.
- A real percentage bar isn't possible (compaction is a single LLM call
  with no progress callbacks); the indeterminate bar is the correct UX.

**Files changed:**
- `koda/tui/commands.py` (+38/-3 lines) — `_compact` rewritten
- `koda/tui/app.tcss` (+9 lines) — `#messages LoadingIndicator` style

**Revert:**
```
git revert 25028a5 --no-edit
```

---

## 6. Fix — token usage from on_chat_model_end for local models

**Commit:** `f5b43ea`
**Message:** `fix(usage): capture token usage from on_chat_model_end for local models`

**What it does:**
- The status bar's token counters (input/output/cache) stayed at 0 for
  every non-Anthropic backend (Ollama, vLLM, LM Studio, OpenAI-compatible)
  because the adapter only read `usage_metadata` from `on_chat_model_stream`
  chunks — but those backends never populate it on stream chunks. The field
  only lands on the final `AIMessage` from `on_chat_model_end`.
- Added `_extract_chat_model_end` extractor that reads `usage_metadata` from
  the final `AIMessage` on `on_chat_model_end` and emits a `Usage` event.
- For Anthropic (which already emits usage on stream chunks), this is a
  harmless no-op: `merge_usage` uses max-ish semantics, no double counting.

**Files changed:**
- `koda/adapters/langgraph.py` (+35 lines) — new extractor + registration

**Revert:**
```
git revert f5b43ea --no-edit
```

---

## Revert everything (full rollback)

To roll back ALL six changes in one shot, reverting newest-first:

```
git revert f5b43ea 25028a5 717ec68 322c2fe 6e83eb4 ee75ea6 --no-edit
```

Or, to reset the working tree to the state before all of them
(`9b498ac` is the last commit before this work began):

```
git reset --hard 9b498ac
```
⚠️ `git reset --hard` is destructive and discards uncommitted work — only
use it if you're sure you want to discard everything from these sessions.

---

## Verification status

Every change was verified before commit:
| # | Change                          | Verification                                                  |
|---|---------------------------------|---------------------------------------------------------------|
| 1 | Permissions                     | Permission test suite (assertions updated)                     |
| 2 | Skills                          | Drop-in; `SkillsMiddleware` already wired                      |
| 3 | MCP                             | Live smoke test — Context7 returns 2 tools                     |
| 4 | Copy + links                    | 15 unit tests + live Textual render test                       |
| 5 | /compact progress bar           | 3 cases: happy, error, unsupported                            |
| 6 | Token usage fix                 | 3 cases: Ollama-style, Anthropic-style, no-usage               |

All scratch verification files were deleted after passing. No secrets in
any committed file (`.env` is gitignored and never tracked).
