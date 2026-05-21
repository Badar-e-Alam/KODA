# coding_agent

Coding agent built on deepagent SDK. **Working.**

**Goal:** a coding agent that can solve **long-horizon tasks** — multi-file refactors, end-to-end feature work, debugging that spans the codebase.

**Inspiration:** Anthropic's **Pi** and Sourcegraph's **Amp**.

## TODO

- [x] Agent integrated, tools added
- [x] Loop (the agent is missing an outer control loop
- [ ] Proactive action for the prompt
- [ ] Human-in-the-loop
- [ ] Plan mode
- [ ] Edit mode
- [ ] Auto-fly mode (accept all changes)
- [ ] Code execution Docker environment
- [ ] Mature the compaction
- [ ] Mature the subagent
- [ ] Optimize tool calling (RLM)
