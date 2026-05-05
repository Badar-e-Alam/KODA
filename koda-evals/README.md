# KODA Coding Agent Evals

Reproducible eval harness for the KODA coding agent. Ships 10 self-contained
tasks, runs them on every PR, scores each in Langfuse, posts results back to
the PR.

## How it integrates with your existing setup

KODA already has Langfuse tracing baked in (`@observe` on every run,
`start_as_current_observation` per tool call, `session_id` + `user_id`
propagation). This eval harness sits **on top of** that — it doesn't
re-instrument anything.

```
                        ┌─────────────────────────────────────────────────┐
                        │  Langfuse                                       │
                        │  ┌───────────────────────────────────────────┐  │
                        │  │ Dataset: koda-coding-evals-v1             │  │
                        │  │   Run: pr-feature-x-a3b4c5d6              │  │
   eval/runner.py ─────▶│  │     Trace: task_01_fix_bug  ✅ pass=1.0   │  │
   creates trace        │  │       └─ KodaAgent._run_traced (@observe) │  │
                        │  │           ├─ chat.completions.create      │  │
   sets session_id ────▶│  │           ├─ tool: read_file              │  │
   on agent call        │  │           ├─ tool: edit_file              │  │
                        │  │           └─ chat.completions.create      │  │
                        │  │     Trace: task_02_add_feature  ❌ pass=0 │  │
                        │  │       └─ ...                              │  │
                        │  └───────────────────────────────────────────┘  │
                        └─────────────────────────────────────────────────┘
```

Each eval run becomes a **dataset run** in Langfuse. Each task becomes a
**trace** scored `pass=1.0/0.0` and `agent_latency_s`. KODA's existing
`@observe` decorators nest naturally inside each task trace because we set
the same `session_id` before invoking the agent.

## Quick start

### 1. Install
```bash
git clone <this-repo>
cd koda_evals
pip install -r requirements.txt

# Install KODA itself (so the import adapter can find it)
pip install -e /path/to/your/KODA-checkout
# or
pip install koda  # if you publish it

cp .env.example .env
# Fill in: LANGFUSE_PUBLIC_KEY/SECRET, OPENAI_API_KEY (or OLLAMA_*), KODA_MODEL
```

### 2. Verify the adapter signature
Open `eval/agent_adapter.py` and find the lines marked `# CHECK:`. You need to
confirm three things match your actual code:

1. **Import path**: `_KODA_IMPORT_PATH = "koda.adapters.coding_agent"` ← from your doc
2. **Class name**: `_KODA_CLASS_NAME = "KodaAgent"` ← from your doc
3. **Method signature**: the adapter tries `.run(prompt, cwd, session_id, user_id)` first.
   If your `KodaAgent.run()` has different kwargs, edit the call.

A 10-second sanity check:
```bash
python -c "from koda.adapters.coding_agent import KodaAgent; print(KodaAgent)"
```
Should print the class without errors.

### 3. Push the dataset to Langfuse (once)
```bash
python -m eval.upload_dataset
```
Creates dataset `koda-coding-evals-v1` with all 10 tasks as items.

### 4. Run the suite
```bash
# Full suite
python -m eval.runner

# One task
python -m eval.runner --task task_01_fix_bug

# Custom run name
python -m eval.runner --run-name "tweak-prompt-$(date +%s)"
```

You'll get console output, `results.json`, `results.md`, and a Langfuse dataset run.

## Running modes

### `EVAL_AGENT_MODE=import` (default, recommended)
Imports `KodaAgent` directly. **Faster, traces nest properly.** Requires KODA
installed in the same Python environment.

### `EVAL_AGENT_MODE=subprocess`
Runs the `koda` CLI as a child process. Same as a user would. Useful when:
- KODA isn't installable as a library
- You want to test the CLI itself, not the Python API
- Different Python versions for evals vs agent

Set via:
```bash
EVAL_AGENT_MODE=subprocess EVAL_AGENT_CMD="koda --cwd {workdir} --model {model}" python -m eval.runner
```

## What you see in Langfuse

After running, open Langfuse → **Datasets → koda-coding-evals-v1 → Runs**.

- Each run named like `branch-sha8-model` (CI) or whatever you pass to `--run-name`
- Compare two runs side-by-side: which tasks regressed, which improved
- Click into any task → full agent trace tree (LLM calls, tool calls, latencies, costs)
- Filter scores by `pass` to count exact pass/fail
- Filter by `agent_latency_s` to find slow tasks

## CI setup (GitHub Actions)

`.github/workflows/evals.yml` runs the suite on every PR.

### Required GitHub secrets
| Secret | Purpose |
|--------|---------|
| `OPENAI_API_KEY` | KODA inner client (if using OpenAI) |
| `OLLAMA_API_KEY` / `OLLAMA_BASE_URL` | KODA inner client (if using Ollama Cloud) |
| `LANGFUSE_PUBLIC_KEY` | From your Langfuse project |
| `LANGFUSE_SECRET_KEY` | From your Langfuse project |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` or self-hosted |
| `KODA_GITHUB_TOKEN` | Only if KODA repo is private |

### What happens on every PR
1. Workflow checks out both this evals repo + your KODA repo at the right ref
2. Installs KODA via `pip install -e .`
3. Runs the eval suite
4. Each task → one Langfuse trace tagged with PR + branch + SHA
5. Sticky comment on the PR with the score table
6. **Job fails if any task regresses** — wire it in as a required status check

### Model matrix
The workflow has a `strategy.matrix` so you can run against multiple models in
parallel (e.g., `openai:gpt-4o-mini` baseline + `ollama:qwen2.5-coder:7b` for
free-tier comparison). Each model posts its own PR comment.

To add models: edit `.github/workflows/evals.yml`:
```yaml
matrix:
  model:
    - openai:gpt-4o-mini
    - openai:gpt-4o
    - ollama:qwen2.5-coder:7b
```

## Cost expectations

Per full-suite run:
- `openai:gpt-4o-mini`: **~$0.20–$1.00** depending on agent verbosity
- `openai:gpt-4o`: ~$2–$10
- `claude-haiku`: ~$0.30–$1.50
- `ollama:qwen2.5-coder:7b` (local): **$0**

The 10 tasks are small (5K–30K input tokens each). The cost varies mostly with
how many tool-call rounds your agent does — that's also a useful signal in
Langfuse (look at trace depth).

## Tips for KODA specifically

- **Disable AGENTS.md bootstrap in evals.** Otherwise every task triggers a
  bootstrap LLM call, doubling cost. The adapter sets
  `KODA_DISABLE_BOOTSTRAP=1`. If your code reads that env var differently,
  rename it in `agent_adapter.py`.

- **Watch the offload middleware.** Eval tasks shouldn't hit the
  `OffloadMiddleware` threshold (they're tiny). If you see offload markers in
  the Langfuse traces, your context budget is set too aggressively for evals
  and you're paying tokens you don't need to.

- **The `_check_health` ping costs you ~10 calls/run.** Acceptable, but if
  you ever want to optimize: reuse a single agent instance across tasks
  (currently the adapter constructs a new one per task to ensure clean state).

## Running SWE-bench Lite (hard real-world problems)

The 64 tasks in `tasks/` are synthetic and run in seconds — great for the
inner loop, but they don't tell you whether the agent can fix real-world bugs
in million-line codebases. For that, `eval/swebench_runner.py` runs SWE-bench
Lite (300 real GitHub issues from django/sympy/sklearn/etc.), graded by the
official Docker harness.

### Discipline: dev split vs full split
Don't iterate against the same instances you report on, or you'll silently
overfit to those repos.

```
SWE-bench Lite (300)
├── dev-20    → swebench/dev_split.json    ← iterate against this
└── eval-280  → everything else            ← only run on PRs to main
```

### One-time setup
```bash
pip install -r requirements.txt          # installs swebench + datasets
python -m swebench.pick_dev_split        # writes swebench/dev_split.json (commit it!)
```

### Daily inner loop (dev-20, ~$0.50–$5/run depending on model)
```bash
# Full pipeline: clone repos → run agent → grade with Docker
python -m eval.swebench_runner

# Just produce predictions, grade later on a beefier box
python -m eval.swebench_runner --no-grade

# Debug one instance
python -m eval.swebench_runner --instance django__django-11099
```

### Release-gate run (full SWE-bench Lite, ~hours, ~$50–200)
```bash
python -m eval.swebench_runner --split full --run-name release-v0.5
```

### How it integrates
- Same `eval.agent_adapter.run_agent` as the synthetic-tasks runner — model
  selection, `EVAL_AGENT_MODE`, and Langfuse `session_id` propagation work
  identically.
- Each instance becomes one Langfuse trace tagged with `instance_id`, repo,
  and base_commit. Scores: `patch_nonempty`, `agent_latency_s`.
- Inference output is `predictions.jsonl` in the standard SWE-bench schema —
  you can ship it directly to the SWE-bench leaderboard.

### Requirements
- **Docker daemon** running (only for grading, not inference). Pulls one
  base image per repo, ~2-5 GB total.
- **~10 GB disk** for clones + Docker images. The runner cleans up clones
  but Docker images persist for caching.

## Adding tasks specific to your codebase

The 10 included tasks are generic (Python, no dependencies, fast graders).
Once they're stable, add 5–10 KODA-specific tasks:

```
tasks/task_11_koda_tool/
  prompt.txt          # "Add a new tool to KODA that does X"
  repo/               # snapshot of KODA source
  test.sh             # exits 0 if the new tool is registered & works
  solution_hint.md
```

Then `python -m eval.upload_dataset` to push the new tasks to Langfuse.

## Troubleshooting

**`ImportError: No module named koda.adapters.coding_agent`**
KODA isn't installed in this environment. Run `pip install -e /path/to/KODA`.

**`KodaAgent has no .run / .invoke / .__call__ method`**
The adapter's method probing failed. Edit `_run_via_import` in
`eval/agent_adapter.py` to call your actual entry method.

**Traces don't nest under the eval trace**
KODA's `@observe` reads `session_id` from somewhere (env var, kwarg, or
context). The adapter passes it as both a kwarg AND env var
(`KODA_SESSION_ID`). Whichever your code reads, one will work. If neither,
add the propagation in your `_run_traced` method.

**Grader times out (task 10)**
That's intentional — the perf test enforces a 2s budget. If KODA's solution
is correct it should complete instantly. If it times out, the agent didn't
fix the O(n²) bug.

## Layout
```
.
├── tasks/                       64 self-contained synthetic eval tasks
├── eval/
│   ├── agent_adapter.py         ← KODA-specific (verify CHECK comments)
│   ├── runner.py                synthetic-tasks orchestration
│   ├── swebench_runner.py       SWE-bench Lite orchestration (dev/full splits)
│   ├── langfuse_reporter.py     traces, scores, dataset linking
│   └── upload_dataset.py        one-time: tasks → Langfuse
├── swebench/
│   ├── pick_dev_split.py        deterministic dev-20 sampler
│   └── dev_split.json           frozen list of dev instances (commit it)
├── .github/workflows/evals.yml  CI with model matrix
├── requirements.txt
└── .env.example
```
