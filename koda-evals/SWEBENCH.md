# Running KODA's coding agent on SWE-bench Lite

Two-phase harness (`eval/swebench_runner.py`):

1. **INFER** — clone each instance's repo at `base_commit` into a tempdir,
   hand the problem statement to the coding agent, capture `git diff` as
   the predicted patch → `predictions.jsonl`.
2. **GRADE** — run the official `swebench.harness.run_evaluation` Docker
   harness against those predictions → `swebench_results.json`.

The agent is reached through `eval/agent_adapter.py:run_agent`, which (in
default `import` mode) instantiates `koda.adapters.coding_agent.CodingAgentAdapter`
and consumes its `stream(prompt, [])`.

---

## Recipe: running with Kimi K2 (via Ollama Cloud)

`kimi:` is wired in `coding_agent/clients.py:build_chat_model` to route
through Ollama Cloud's OpenAI-compatible endpoint, honouring `OLLAMA_BASE_URL`
and either `KIMI_API_KEY` or `OLLAMA_API_KEY`.

```powershell
cd koda-evals

# One-time eval deps. The runner uses datasets to pull SWE-bench Lite from
# HuggingFace; swebench is the official grading harness.
pip install datasets swebench python-dotenv

# Ollama Cloud creds (Kimi K2 lives there)
$env:OLLAMA_API_KEY  = "<your-ollama-cloud-key>"
$env:OLLAMA_BASE_URL = "https://ollama.com/v1"   # only if non-default

# Skip per-instance AGENTS.md bootstrap (one LLM round-trip saved per task)
$env:KODA_DISABLE_BOOTSTRAP = "1"

# 1) Smoke test on one instance (no Docker needed)
python -m eval.swebench_runner --instance django__django-11099 --no-grade --model kimi:kimi-k2.6

# 2) Full dev-20 inference (66 instances)
python -m eval.swebench_runner --no-grade --model kimi:kimi-k2.6

# 3) Grade later, on a box with Docker daemon running
python -m eval.swebench_runner --grade-only
```

`--model kimi:kimi-k2.6` overrides `$KODA_MODEL` for the run. You can also
just `$env:KODA_MODEL = "kimi:kimi-k2.6"` and omit the flag.

---

## Env vars the runner / adapter read

| Var | Default | What it does |
|---|---|---|
| `KODA_MODEL` | `ollama:qwen2.5-coder:7b` | Model spec passed to `CodingAgentAdapter`. `--model` flag wins over this. |
| `EVAL_AGENT_MODE` | `import` | `import` runs in-process; `subprocess` shells out to the `koda` CLI. |
| `EVAL_AGENT_TIMEOUT` | `600` | Per-instance timeout (seconds) — only honored in `subprocess` mode. |
| `EVAL_AGENT_CMD` | `koda --cwd {workdir} --model {model} --no-tui` | `subprocess` mode command template. |
| `KODA_DISABLE_BOOTSTRAP` | unset | Set to `1` to skip the per-workdir AGENTS.md bootstrap (saves one LLM call per instance). |
| `OLLAMA_BASE_URL` | `https://ollama.com/v1` | Where the `kimi:` provider posts requests. |
| `KIMI_API_KEY` / `OLLAMA_API_KEY` | unset | Auth header. `KIMI_API_KEY` wins if both are set. |
| `SWEBENCH_MAX_WORKERS` | `4` | Docker grading parallelism. |
| `SWEBENCH_TIMEOUT` | `1800` | Per-instance grading timeout. |
| `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | unset | If all three are set, the runner traces each instance into Langfuse. Omit to disable tracing. |

---

## Dev split

`swebench/dev_split.json` pins a frozen 20% stratified sample (66 of 300
instances, 12 repos, seed 42). Django and sympy dominate (23 + 16).
**Do not regenerate** — comparable results across runs require the same
split. If you really need to: `python -m swebench.pick_dev_split --fraction 0.20 --seed 42`.

To run the full 300-instance Lite: `--split full`.
To run one: `--instance django__django-11099` (ignores `--split`).

---

## Output files

- `predictions.jsonl` — one row per instance: `instance_id`, `model_name_or_path`,
  `model_patch`, `agent_elapsed_s`, `agent_error`, `session_id`. Consumed by
  grading. Safe to delete and regenerate.
- `swebench_results.json` — top-level run summary + nested grading report.
- `<run_id>.predictions.json` — official harness report with
  `resolved_instances` / `unresolved_instances`. Lives next to the runner.
- Docker harness logs land under `logs/run_evaluation/<run_id>/...`.

---

## Things that will bite you

- **Docker daemon required for grading.** The harness builds per-instance
  images and runs the project's test suite inside them. `--no-grade` skips
  this entirely so inference can run on a laptop.
- **`subprocess` mode** (`EVAL_AGENT_MODE=subprocess`) expects a `koda --no-tui`
  flag that this repo doesn't actually expose today. Stick with the default
  `import` mode unless you patch the CLI first.
- **Each instance clones the full repo** at `base_commit` into a tempdir,
  then `shutil.rmtree`s it. Django/sympy clones aren't tiny — budget disk
  and network.
- **The agent runs in YOLO approval mode** inside the cloned repo
  (`set_approval_mode("yolo")` in `koda/adapters/coding_agent.py`). It can
  run arbitrary shell, including the repo's own tests/build. That's intended
  for SWE-bench but worth knowing.
- **`infer_one` runs instances serially.** No parallelism in the inference
  phase (the grading phase parallelises through Docker via `SWEBENCH_MAX_WORKERS`).
- **The Kimi/Ollama-Cloud route depends on the recent `kimi:` mapping** in
  `coding_agent/clients.py`. If you see "Unable to infer model provider for
  model='kimi:…'", that mapping is missing — pull latest.

## Non-fatal warnings you can ignore

These print to stderr but the run continues normally:

- `Warning: You are sending unauthenticated requests to the HF Hub.`
  HuggingFace rate-limits anonymous dataset pulls. Set `$env:HF_TOKEN`
  (free account) if you're doing many runs back-to-back.
- `[langfuse] could not fetch dataset koda-coding-evals-v1 … 404.`
  The reporter looks for an optional Langfuse dataset to score against.
  Either run `python -m eval.upload_dataset` once to create it or just
  ignore — task-level traces still flow through.

## Verified

Smoke-tested on `django__django-11099` with `kimi:kimi-k2.6`:
agent ran ~104 s, produced a 901-byte patch (`\A...\Z` regex anchors —
the canonical fix), no errors. End-to-end wiring confirmed working from
HF dataset fetch → repo clone → agent run → `git diff` capture →
`predictions.jsonl`.
