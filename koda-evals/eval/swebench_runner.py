"""Run KODA against SWE-bench Lite.

Two-phase pipeline (matches every other SWE-bench harness):

  1. INFER: clone each instance's repo at base_commit, hand the problem
     statement to KODA, capture `git diff` as the predicted patch.
     Output → predictions.jsonl  (one row per instance).

  2. GRADE: invoke the official `swebench.harness.run_evaluation` to apply
     each patch in the per-instance Docker image and run FAIL_TO_PASS /
     PASS_TO_PASS tests.
     Output → swebench_results.json + per-instance logs.

Usage
-----
    # Run dev-20 split end-to-end (infer + grade)
    python -m eval.swebench_runner

    # Just produce predictions; grade later on a beefier box
    python -m eval.swebench_runner --no-grade

    # Run the full SWE-bench Lite (300 instances) — only do this on release
    python -m eval.swebench_runner --split full

    # One specific instance for debugging
    python -m eval.swebench_runner --instance django__django-11099

The agent is invoked through the same `eval.agent_adapter.run_agent` used by
the synthetic-tasks runner, so model selection / EVAL_AGENT_MODE / Langfuse
session_id propagation work identically.

Requirements: Docker daemon running (only for grading), `swebench` and
`datasets` pip packages — see requirements.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

from eval.agent_adapter import run_agent
from eval.langfuse_reporter import LangfuseReporter

ROOT = Path(__file__).resolve().parent.parent
DEV_SPLIT_PATH = ROOT / "swebench" / "dev_split.json"
DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
DATASET_SPLIT = "test"


@dataclass
class Prediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str
    agent_elapsed_s: float
    agent_error: str | None
    session_id: str


def _hf_token() -> str | None:
    """HuggingFace token read from the env (populated from .env by
    :func:`_load_env`). Accepts the ``HUGGING_FACE_HUB_TOKEN`` alias that some
    tooling uses. Returns ``None`` when unset/blank so anonymous (rate-limited)
    access still works."""
    tok = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    tok = (tok or "").strip()
    return tok or None


def _load_instances(args: argparse.Namespace) -> list[dict]:
    from datasets import load_dataset

    ds = load_dataset(DATASET_NAME, split=DATASET_SPLIT, token=_hf_token())

    if args.instance:
        rows = [r for r in ds if r["instance_id"] == args.instance]
        if not rows:
            sys.exit(f"instance not found in {DATASET_NAME}: {args.instance}")
        return rows

    if args.split == "full":
        return list(ds)

    split_path = args.split_file or DEV_SPLIT_PATH
    if not split_path.exists():
        sys.exit(
            f"{split_path} not found. Run `python -m swebench.pick_dev_split` "
            "once to generate it."
        )
    wanted = set(json.loads(split_path.read_text())["instance_ids"])
    rows = [r for r in ds if r["instance_id"] in wanted]
    missing = wanted - {r["instance_id"] for r in rows}
    if missing:
        print(f"[warn] {len(missing)} dev-split instance_ids not in dataset: "
              f"{sorted(missing)[:3]}…")
    return rows


def _checkout(repo: str, base_commit: str, workdir: Path) -> None:
    """Clone repo at base_commit into workdir. Shallow clone with full history
    of just that commit — fast enough and avoids pulling years of git history."""
    url = f"https://github.com/{repo}.git"
    subprocess.run(["git", "clone", url, str(workdir)], check=True,
                   capture_output=True)
    subprocess.run(["git", "checkout", base_commit], cwd=workdir, check=True,
                   capture_output=True)


def _build_prompt(row: dict) -> str:
    return (
        "You are fixing a bug in an open-source Python repository.\n\n"
        "## Problem statement\n"
        f"{row['problem_statement']}\n\n"
        "## Instructions\n"
        "- The repo is checked out in your current working directory at the "
        "exact base commit referenced in the issue.\n"
        "- Make the minimum-possible code change that resolves the issue.\n"
        "- Do NOT modify tests. The grader runs an existing test suite — your "
        "fix is judged purely on whether the hidden FAIL_TO_PASS tests pass "
        "and the PASS_TO_PASS tests don't regress.\n"
        "- When done, simply stop. The grader reads `git diff` from your "
        "working tree, so all your edits should be unstaged or staged but not "
        "committed.\n"
    )


def _capture_patch(workdir: Path) -> str:
    proc = subprocess.run(
        ["git", "diff"], cwd=workdir, capture_output=True, text=True,
    )
    return proc.stdout


def infer_one(row: dict, reporter: LangfuseReporter, model: str) -> Prediction:
    iid = row["instance_id"]
    workdir = Path(tempfile.mkdtemp(prefix=f"swebench_{iid}_"))
    session_id = f"{reporter.run_name}/{iid}"

    print(f"\n━━ {iid} ━━")
    try:
        _checkout(row["repo"], row["base_commit"], workdir)
    except subprocess.CalledProcessError as e:
        print(f"  clone failed: {e.stderr.decode()[-200:]}")
        return Prediction(iid, model, "", 0.0, f"clone failed: {e}", session_id)

    prompt = _build_prompt(row)

    with reporter.task_run(iid, prompt) as span:
        start = time.perf_counter()
        agent_out = run_agent(prompt, workdir, session_id=session_id)
        elapsed = time.perf_counter() - start
        patch = _capture_patch(workdir)

        span.update(
            output={"patch_len": len(patch), "patch_head": patch[:400]},
            metadata={
                "agent_elapsed_s": round(elapsed, 2),
                "agent_error": agent_out.error,
                "session_id": session_id,
                "repo": row["repo"],
                "base_commit": row["base_commit"],
                **agent_out.metadata,
            },
        )
        span.score("patch_nonempty", 1.0 if patch.strip() else 0.0)
        span.score("agent_latency_s", round(elapsed, 2))

    print(f"  agent={elapsed:.1f}s  patch={len(patch)}b  err={agent_out.error}")
    shutil.rmtree(workdir, ignore_errors=True)

    return Prediction(iid, model, patch, round(elapsed, 2), agent_out.error, session_id)


def _grade(predictions_path: Path, instances: list[dict], run_id: str) -> dict:
    """Invoke the official SWE-bench harness. Requires Docker."""
    try:
        from swebench.harness.run_evaluation import main as run_eval  # type: ignore
    except ImportError:
        print("[swebench] `swebench` package not installed — skipping grading.")
        print("  pip install swebench    # then re-run with --grade-only")
        return {"graded": False, "reason": "swebench not installed"}

    if shutil.which("docker") is None:
        print("[swebench] docker CLI not found — skipping grading.")
        return {"graded": False, "reason": "docker missing"}

    instance_ids = [r["instance_id"] for r in instances]
    print(f"\n[swebench] grading {len(instance_ids)} instance(s) via Docker harness…")
    # swebench ≥3 added required positional args (namespace, rewrite_reports,
    # modal). namespace=None forces *local* image builds — required on arm64
    # (Apple Silicon), where the published x86 `swebench/*` images don't run.
    # Override via SWEBENCH_NAMESPACE=swebench on x86 hosts to pull prebuilt
    # images instead. Passed as kwargs so the call survives further reordering.
    eval_kwargs = dict(
        dataset_name=DATASET_NAME,
        split=DATASET_SPLIT,
        instance_ids=instance_ids,
        predictions_path=str(predictions_path),
        max_workers=int(os.getenv("SWEBENCH_MAX_WORKERS", "4")),
        force_rebuild=False,
        cache_level="env",
        clean=False,
        open_file_limit=4096,
        run_id=run_id,
        timeout=int(os.getenv("SWEBENCH_TIMEOUT", "1800")),
        namespace=os.getenv("SWEBENCH_NAMESPACE") or None,
        rewrite_reports=False,
        modal=False,
    )
    try:
        run_eval(**eval_kwargs)
    except TypeError:
        # Older swebench versions lack the newer args — retry with the subset
        # they accept, dropping any kwargs their signature doesn't declare.
        import inspect
        accepted = set(inspect.signature(run_eval).parameters)
        run_eval(**{k: v for k, v in eval_kwargs.items() if k in accepted})

    # SWE-bench writes reports with varying name patterns depending on version.
    # Try the most common ones in order.
    candidate_paths = [
        Path(f"{_model_slug(model)}.{run_id}.json"),
        Path(f"{run_id}.{predictions_path.stem}.json"),
    ]
    for report_path in candidate_paths:
        if report_path.exists():
            return {"graded": True, "report": json.loads(report_path.read_text())}
    return {"graded": True, "report": None, "note": f"no {candidate_paths[0]} or {candidate_paths[1]}"}


def _resolve_fraction_split(fraction: float, seed: int) -> Path:
    """Map ``--fraction 0.10`` to ``swebench/dev10_split.json``, generating it
    via :mod:`swebench.pick_dev_split` (stratified by repo, seeded) when it
    doesn't exist yet. Existing files are reused so repeat runs stay
    comparable."""
    if not 0.0 < fraction <= 1.0:
        sys.exit(f"--fraction must be in (0, 1], got {fraction}")
    pct = f"{fraction * 100:g}".replace(".", "p")  # 0.10→10, 0.05→5, 0.125→12p5
    suffix = f"-seed{seed}" if seed != 42 else ""
    path = ROOT / "swebench" / f"dev{pct}{suffix}_split.json"
    if path.exists():
        print(f"[swebench] reusing split {path.name}")
        return path
    from swebench.pick_dev_split import pick
    out = pick(fraction, seed)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"[swebench] generated {path.name}: {out['n_selected']} instances "
          f"(fraction={fraction}, seed={seed})")
    return path


def _model_slug(model: str) -> str:
    """Filesystem/Langfuse-safe slug for a model spec like ``kimi:kimi-k2.6``."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")


def _with_slug(path: Path, slug: str) -> Path:
    """predictions.jsonl + kimi-k2.6 → predictions.kimi-k2.6.jsonl"""
    return path.with_name(f"{path.stem}.{slug}{path.suffix}")


def _relay_output(slug: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout:  # type: ignore[union-attr]
        print(f"[{slug}] {line}", end="", flush=True)


def _run_multi(args: argparse.Namespace, models: list[str]) -> None:
    """Fan out one child runner per model, in parallel.

    Per-model subprocesses are mandatory, not a convenience: the import-mode
    adapter ``os.chdir``s into each instance's workdir and model selection
    rides on the process-global ``KODA_MODEL`` env var, so two models in one
    process would trample each other. Each child gets its own predictions /
    report / Langfuse run name so nothing collides on disk either.
    """
    base_run = args.run_name or f"multi-{time.strftime('%Y%m%d-%H%M%S')}"
    procs: list[tuple[str, subprocess.Popen]] = []
    threads: list[threading.Thread] = []

    for model in models:
        slug = _model_slug(model)
        cmd = [
            sys.executable, "-u", "-m", "eval.swebench_runner",
            "--model", model,
            "--predictions", str(_with_slug(args.predictions, slug)),
            "--report-json", str(_with_slug(args.report_json, slug)),
            "--run-name", f"{base_run}-{slug}",
        ]
        if args.instance:
            cmd += ["--instance", args.instance]
        else:
            cmd += ["--split", args.split]
            if args.split_file:
                cmd += ["--split-file", str(args.split_file)]
        if args.no_grade:
            cmd.append("--no-grade")
        if args.grade_only:
            cmd.append("--grade-only")
        if args.timeout is not None:
            cmd += ["--timeout", str(args.timeout)]

        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
        )
        t = threading.Thread(target=_relay_output, args=(slug, proc), daemon=True)
        t.start()
        procs.append((slug, proc))
        threads.append(t)
        print(f"[multi] launched {model} (pid {proc.pid}) → "
              f"{_with_slug(args.predictions, slug)}")

    codes = {slug: proc.wait() for slug, proc in procs}
    for t in threads:
        t.join(timeout=5)
    print("\n[multi] all runs finished:")
    for slug, code in codes.items():
        print(f"  {slug}: exit {code}")
    sys.exit(max(codes.values()) if codes else 0)


def _force_utf8_stdio() -> None:
    """Make ``print`` on Windows tolerate the heavy box-drawing chars we
    use as separators (``━━``, ``↳`` in tool previews, etc.).

    Windows defaults stdout/stderr to cp1252, which can't encode those —
    the runner used to crash on its very first ``print(f"━━ {iid} ━━")``
    before infer_one even started. Reconfigure to utf-8 with
    ``errors='replace'`` so a stray glyph never aborts a run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is None:
            continue
        try:
            reconf(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "full"], default="dev",
                        help="dev = swebench/dev_split.json (default); full = all SWE-bench Lite")
    parser.add_argument("--split-file", type=Path,
                        help="path to an alternate split JSON (e.g. swebench/dev10_split.json); "
                             "implies --split dev semantics")
    parser.add_argument("--fraction", type=float,
                        help="stratified sample fraction of SWE-bench Lite (e.g. 0.10 = ~30 "
                             "instances). Generates swebench/dev<pct>_split.json on first use "
                             "and reuses it afterwards. Overrides --split/--split-file.")
    parser.add_argument("--seed", type=int, default=42,
                        help="seed for --fraction sampling (default: 42 — keep it for "
                             "comparable runs)")
    parser.add_argument("--instance", help="run a single instance_id and ignore --split")
    parser.add_argument("--run-name", help="Langfuse run name (default: auto)")
    parser.add_argument("--no-grade", action="store_true",
                        help="produce predictions.jsonl but skip Docker grading")
    parser.add_argument("--grade-only", action="store_true",
                        help="skip inference, grade an existing predictions.jsonl")
    parser.add_argument("--predictions", type=Path, default=Path("predictions.jsonl"))
    parser.add_argument("--report-json", type=Path, default=Path("swebench_results.json"))
    parser.add_argument(
        "--model",
        help="Model spec like 'kimi:kimi-k2.6' or 'anthropic:claude-sonnet-4-6'. "
             "Overrides $KODA_MODEL. Default: $KODA_MODEL or ollama:qwen2.5-coder:7b.",
    )
    parser.add_argument(
        "--models",
        help="Comma-separated model specs — run 1, 2, 3 or more, e.g. "
             "'kimi:kimi-k2.6' (single) or 'kimi:kimi-k2.6,ollama:minimax-m3' "
             "(two). Each model runs as its own parallel subprocess with "
             "per-model predictions/report files. Mutually exclusive with --model.",
    )
    parser.add_argument(
        "--timeout", type=int,
        help="Per-instance agent timeout in seconds (the threshold an instance "
             "is given before it's abandoned with an empty patch). Overrides "
             "$EVAL_AGENT_TIMEOUT. Default: 600.",
    )
    args = parser.parse_args()

    # Load koda-evals/.env explicitly so the keys (HF_TOKEN, Ollama, Langfuse)
    # are found no matter which directory the runner is launched from — not
    # just when CWD happens to be koda-evals.
    load_dotenv(ROOT / ".env")

    # Normalise the HuggingFace token across the two names tooling looks for,
    # so `datasets`/`huggingface_hub` pick it up (silences the "unauthenticated
    # requests to the HF Hub" warning and dodges anonymous rate limits). Child
    # subprocesses (--models) inherit it via the environment.
    if _hf_token():
        os.environ["HF_TOKEN"] = _hf_token()
        os.environ["HUGGING_FACE_HUB_TOKEN"] = _hf_token()

    # CLI threshold wins over .env; export so child subprocesses (--models) and
    # the in-process adapter (reads $EVAL_AGENT_TIMEOUT) both see it.
    if args.timeout is not None:
        os.environ["EVAL_AGENT_TIMEOUT"] = str(args.timeout)

    if args.fraction is not None:
        args.split_file = _resolve_fraction_split(args.fraction, args.seed)

    if args.models:
        if args.model:
            sys.exit("use either --model or --models, not both")
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        if not models:
            sys.exit("--models given but no model specs parsed")
        _run_multi(args, models)
        return  # _run_multi sys.exits; belt and braces
    # CLI flag wins over env, env wins over the historical default. The flag
    # is exported so eval.agent_adapter._run_via_import (which reads KODA_MODEL
    # at adapter-construction time inside _collect) sees the same value.
    if args.model:
        os.environ["KODA_MODEL"] = args.model
    reporter = LangfuseReporter(run_name=args.run_name)
    model = os.getenv("KODA_MODEL", "ollama:qwen2.5-coder:7b")
    print(f"[koda] mode={os.getenv('EVAL_AGENT_MODE', 'import')}  model={model}")
    if reporter.enabled:
        print(f"[langfuse] run: {reporter.run_name}")

    instances = _load_instances(args)
    if args.instance:
        split_label = "single"
    elif args.split_file:
        split_label = args.split_file.name
    else:
        split_label = "dev-20" if args.split == "dev" else args.split
    print(f"[swebench] {len(instances)} instance(s) selected from {split_label}")

    if not args.grade_only:
        preds = [infer_one(r, reporter, model) for r in instances]
        reporter.flush()
        with args.predictions.open("w") as f:
            for p in preds:
                f.write(json.dumps(asdict(p)) + "\n")
        print(f"\n[swebench] wrote {len(preds)} predictions → {args.predictions}")
    else:
        if not args.predictions.exists():
            sys.exit(f"--grade-only set but {args.predictions} missing")
        print(f"[swebench] grading existing {args.predictions}")

    if args.no_grade:
        print("[swebench] --no-grade: skipping Docker grading.")
        return

    grade_out = _grade(args.predictions, instances, run_id=reporter.run_name)
    args.report_json.write_text(json.dumps({
        "run_name": reporter.run_name,
        "model": model,
        "split": args.split if not args.instance else "single",
        "n_instances": len(instances),
        "predictions_path": str(args.predictions),
        "grading": grade_out,
    }, indent=2))
    print(f"[swebench] report → {args.report_json}")

    report = grade_out.get("report") if grade_out.get("graded") else None
    if isinstance(report, dict) and "resolved_instances" in report:
        n_pass = len(report["resolved_instances"])
        n_total = len(instances)
        pct = 100 * n_pass / n_total if n_total else 0
        print(f"\n━━ {n_pass}/{n_total} resolved ({pct:.0f}%) ━━")
        sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
