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
import shutil
import subprocess
import sys
import tempfile
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


def _load_instances(args: argparse.Namespace) -> list[dict]:
    from datasets import load_dataset

    ds = load_dataset(DATASET_NAME, split=DATASET_SPLIT)

    if args.instance:
        rows = [r for r in ds if r["instance_id"] == args.instance]
        if not rows:
            sys.exit(f"instance not found in {DATASET_NAME}: {args.instance}")
        return rows

    if args.split == "full":
        return list(ds)

    if not DEV_SPLIT_PATH.exists():
        sys.exit(
            f"{DEV_SPLIT_PATH} not found. Run `python -m swebench.pick_dev_split` "
            "once to generate the frozen dev-20 split."
        )
    wanted = set(json.loads(DEV_SPLIT_PATH.read_text())["instance_ids"])
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
    try:
        run_eval(
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
        )
    except TypeError:
        # Older swebench versions have a slightly different signature.
        run_eval(
            dataset_name=DATASET_NAME,
            split=DATASET_SPLIT,
            instance_ids=instance_ids,
            predictions_path=str(predictions_path),
            run_id=run_id,
        )

    report_path = Path(f"{run_id}.{predictions_path.stem}.json")
    if report_path.exists():
        return {"graded": True, "report": json.loads(report_path.read_text())}
    return {"graded": True, "report": None, "note": f"no {report_path}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "full"], default="dev",
                        help="dev = swebench/dev_split.json (default); full = all SWE-bench Lite")
    parser.add_argument("--instance", help="run a single instance_id and ignore --split")
    parser.add_argument("--run-name", help="Langfuse run name (default: auto)")
    parser.add_argument("--no-grade", action="store_true",
                        help="produce predictions.jsonl but skip Docker grading")
    parser.add_argument("--grade-only", action="store_true",
                        help="skip inference, grade an existing predictions.jsonl")
    parser.add_argument("--predictions", type=Path, default=Path("predictions.jsonl"))
    parser.add_argument("--report-json", type=Path, default=Path("swebench_results.json"))
    args = parser.parse_args()

    load_dotenv()
    reporter = LangfuseReporter(run_name=args.run_name)
    model = os.getenv("KODA_MODEL", "ollama:qwen2.5-coder:7b")
    print(f"[koda] mode={os.getenv('EVAL_AGENT_MODE', 'import')}  model={model}")
    if reporter.enabled:
        print(f"[langfuse] run: {reporter.run_name}")

    instances = _load_instances(args)
    print(f"[swebench] {len(instances)} instance(s) selected from "
          f"{'dev-20' if args.split == 'dev' and not args.instance else args.split}")

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
