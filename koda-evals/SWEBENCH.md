# Running KODA's coding agent on SWE-bench Lite

Two-phase harness (`eval/swebench_runner.py`):

1. **INFER** — clone each instance's repo at `base_commit` into a tempdir,
   hand the problem statement to the coding agent, capture `git diff` as the
   predicted patch → `predictions*.jsonl`.
2. **GRADE** — run the official `swebench.harness.run_evaluation` Docker
   harness against those predictions → `swebench_results*.json`.

The agent is reached through `eval/agent_adapter.py:run_agent`, which (in
default `import` mode) instantiates `koda.adapters.coding_agent.CodingAgentAdapter`
and consumes its `stream(prompt, [])`.

---

## TL;DR — the command you probably want

Run **5%** of SWE-bench Lite against **three models in parallel**
(kimi-k2.6, glm-5.1, deepseek-v4-pro), inference only, with a generous
per-instance timeout:

```bash
cd koda-evals
source ../.venv/bin/activate            # project venv (has datasets + swebench)

python -m eval.swebench_runner \
  --fraction 0.05 \
  --timeout 1200 \
  --no-grade \
  --models "kimi:kimi-k2.6,ollama:glm-5.1,ollama:deepseek-v4-pro"
```

This writes one predictions file per model:

- `predictions.kimi-kimi-k2.6.jsonl`
- `predictions.ollama-glm-5.1.jsonl`
- `predictions.ollama-deepseek-v4-pro.jsonl`

Then grade them (Docker daemon must be running):

```bash
python -m eval.swebench_runner --grade-only --predictions predictions.kimi-kimi-k2.6.jsonl
python -m eval.swebench_runner --grade-only --predictions predictions.ollama-glm-5.1.jsonl
python -m eval.swebench_runner --grade-only --predictions predictions.ollama-deepseek-v4-pro.jsonl
```

> The three model IDs above are the exact strings Ollama Cloud serves
> (`curl https://ollama.com/v1/models`). Other available ones include
> `glm-4.6`, `glm-4.7`, `glm-5`, `kimi-k2.5`, `deepseek-v3.2`,
> `deepseek-v4-flash`. Swap freely — the spec is just `ollama:<served-id>`
> (or the special-cased `kimi:<id>`).

---

## The three knobs

Everything that varies between runs is a flag. None of it is hard-coded.

| Knob | Flag | Default | Notes |
|---|---|---|---|
| **Sample size** | `--fraction 0.05` | dev-20 split | Stratified by repo, seeded. Generates `swebench/dev<pct>_split.json` on first use and **reuses** it after, so repeat runs stay comparable. `--seed N` changes the draw (default 42). |
| **Model(s)** | `--model` *or* `--models` | `$KODA_MODEL` | `--model X` runs one in-process. `--models "X,Y,Z"` runs **1, 2, 3 or more** models, each as its own parallel subprocess with separate output files. |
| **Timeout / threshold** | `--timeout 1200` | 600s | Per-instance seconds before the agent is abandoned with an empty patch. Heavy repos (astropy, django, sympy) + slower models need ≥1200. Overrides `$EVAL_AGENT_TIMEOUT`; propagated to every child subprocess. |

### How many models?

```bash
--models "kimi:kimi-k2.6"                                   # 1 model
--models "kimi:kimi-k2.6,ollama:glm-5.1"                    # 2, in parallel
--models "kimi:kimi-k2.6,ollama:glm-5.1,ollama:deepseek-v4-pro"   # 3, in parallel
```

`--models` and `--model` are mutually exclusive. Each model in `--models`
gets its own predictions file, report file, and Langfuse run name
(`<run>-<model-slug>`), so nothing collides on disk.

Why subprocess-per-model and not one process? The import-mode adapter
`os.chdir`s into each instance's workdir and model selection rides on the
process-global `KODA_MODEL` env var — two models in one process would
trample each other.

---

## Model routing (Ollama Cloud)

`coding_agent/model.py` resolves specs:

- `kimi:<id>` and `ollama:<id>` → routed to the Ollama-family endpoint.
- When `OLLAMA_BASE_URL` ends in `/v1` (Ollama Cloud is OpenAI-shaped), the
  request goes through `ChatOpenAI` with the `OLLAMA_API_KEY` attached.
- Other specs (`anthropic:…`, `openai:…`) pass through to `create_deep_agent`.

> **Gotcha that cost a day:** `OLLAMA_BASE_URL` **must end in `/v1`**. Without
> it, requests fall through to `ChatOllama` *unauthenticated* and hang until
> the timeout. The `.env` in this folder is already set correctly.

---

## Permissions — why headless runs used to hang

The coding agent gates every mutating tool (`edit_file`, `write_file`,
`execute`, …) through a human-in-the-loop permission check. In a headless
eval there is no human to answer, so the agent emitted a `PermissionRequest`
and **blocked until the timeout** with an empty patch — every instance died
identically.

Fix: the eval adapter calls `koda.tools.permissions.set_auto_approve(True)`
before streaming, which makes `decide()` approve every gated tool (safe — the
agent runs in disposable tempdir clones). This is the same switch that
`koda --no-tui --auto-approve` now flips for unattended CLI runs.

You don't need to do anything — it's automatic in `import` mode.

---

## Env vars the runner / adapter read

| Var | Default | What it does |
|---|---|---|
| `KODA_MODEL` | `ollama:qwen2.5-coder:7b` | Model spec. `--model`/`--models` win over this. |
| `EVAL_AGENT_MODE` | `import` | `import` runs in-process; `subprocess` shells out to `koda --no-tui`. Stick with `import`. |
| `EVAL_AGENT_TIMEOUT` | `600` | Per-instance timeout. `--timeout` wins over this. |
| `KODA_DISABLE_BOOTSTRAP` | `1` (in `.env`) | Skip the per-workdir AGENTS.md bootstrap (saves one LLM call/instance). |
| `OLLAMA_BASE_URL` | `https://ollama.com/v1` | Ollama-family endpoint. **Keep the `/v1`.** |
| `OLLAMA_API_KEY` / `KIMI_API_KEY` | — | Auth header. |
| `HF_TOKEN` | — | HuggingFace token for the dataset pull. Read from `koda-evals/.env` (also accepts the `HUGGING_FACE_HUB_TOKEN` alias) and passed to `load_dataset`. Optional — silences the unauthenticated-HF-Hub warning and dodges anonymous rate limits. |
| `SWEBENCH_MAX_WORKERS` | `4` | Docker grading parallelism. |
| `SWEBENCH_TIMEOUT` | `1800` | Per-instance grading timeout. |
| `LANGFUSE_*` | — | If all three keys are set, each instance is traced into Langfuse. |

---

## One-time setup

```bash
# from repo root
python -m pip install -e ".[anthropic]"      # or your provider extra
source .venv/bin/activate
pip install datasets swebench python-dotenv   # eval deps (already in .venv here)
```

Copy `koda-evals/.env` is already populated with the Ollama Cloud key and
Langfuse keys. To authenticate the dataset pull, set `HF_TOKEN` in that
`.env` (a free read token from <https://huggingface.co/settings/tokens>) — the
runner loads it from `koda-evals/.env` automatically and passes it to
`load_dataset`. Leaving it blank falls back to anonymous (rate-limited) access.

---

## Other run shapes

```bash
# One specific instance (ignores --fraction/--split), no grading
python -m eval.swebench_runner --instance django__django-11099 --no-grade --model kimi:kimi-k2.6

# The frozen dev-20 split (66 instances, 20%)
python -m eval.swebench_runner --no-grade --model kimi:kimi-k2.6

# The full 300-instance SWE-bench Lite — release runs only
python -m eval.swebench_runner --split full --no-grade --model kimi:kimi-k2.6

# Infer now, grade later on a Docker box
python -m eval.swebench_runner --fraction 0.05 --no-grade --models "kimi:kimi-k2.6,ollama:glm-5.1"
# ...later...
python -m eval.swebench_runner --grade-only --predictions predictions.kimi-kimi-k2.6.jsonl
```

---

## Output files

- `predictions.<model-slug>.jsonl` — one row/instance: `instance_id`,
  `model_name_or_path`, `model_patch`, `agent_elapsed_s`, `agent_error`,
  `session_id`. (Single-model runs without `--models` write plain
  `predictions.jsonl`.)
- `swebench_results.<model-slug>.json` — run summary + nested grading report.
- `<run_id>.<predictions-stem>.json` — official harness report with
  `resolved_instances` / `unresolved_instances`.
- Docker harness logs under `logs/run_evaluation/<run_id>/...`.

---

## Things that will bite you

- **Docker daemon required for grading.** `--no-grade` skips it so inference
  runs on a laptop. The harness builds per-instance images and runs each
  repo's full test suite inside them — django/sympy/astropy images are heavy;
  budget disk and time.
- **Slow models + heavy repos hit the timeout.** kimi-k2.6 routinely needs
  >600s on astropy/django/sympy; raise `--timeout` (1200 is a good start) or
  its score is deflated by empty-patch timeouts, not capability.
- **Inference is serial per model** (3 models run concurrently, but each works
  through its 18-ish instances one at a time). Grading parallelises via
  `SWEBENCH_MAX_WORKERS`.
- **Each instance clones the full repo** at `base_commit` then `rmtree`s it.

## Non-fatal warnings you can ignore

- `Warning: You are sending unauthenticated requests to the HF Hub.` — set
  `HF_TOKEN` to silence.
- `[langfuse] could not fetch dataset koda-coding-evals-v1 … 404.` — optional
  scoring dataset; run `python -m eval.upload_dataset` once or ignore.
