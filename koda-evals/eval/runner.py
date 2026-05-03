"""Run the eval suite against KODA.

    python -m eval.runner                          # run all tasks
    python -m eval.runner --task task_01_fix_bug   # run one task
    python -m eval.runner --run-name my-experiment

Outputs:
    - results.json  (per-task pass/fail, elapsed, agent output)
    - results.md    (human-readable, used for PR comments)
    - One Langfuse trace per task, scored pass=1.0/0.0 + agent_latency_s
    - KODA's existing @observe-decorated traces nest INSIDE each eval trace
      via session_id propagation
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
from pathlib import Path

from dotenv import load_dotenv

from eval.agent_adapter import run_agent
from eval.langfuse_reporter import LangfuseReporter

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"


def grade(task_dir: Path, workdir: Path) -> tuple[bool, str]:
    """Run the task's grader, return (passed, output)."""
    try:
        proc = subprocess.run(
            ["bash", str(task_dir / "test.sh"), str(workdir)],
            capture_output=True, text=True, timeout=120,
        )
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "grader timed out (120s)"


def run_one(task_dir: Path, reporter: LangfuseReporter) -> dict:
    name = task_dir.name
    prompt = (task_dir / "prompt.txt").read_text()
    workdir = Path(tempfile.mkdtemp(prefix=f"eval_{name}_"))
    shutil.copytree(task_dir / "repo", workdir, dirs_exist_ok=True)

    # session_id ties together: this eval run + this task. KODA's @observe
    # picks it up and groups all LLM/tool calls under the same trace tree.
    session_id = f"{reporter.run_name}/{name}"

    print(f"\n━━ {name} ━━")

    with reporter.task_run(name, prompt) as span:
        start = time.perf_counter()
        agent_out = run_agent(prompt, workdir, session_id=session_id)
        agent_elapsed = time.perf_counter() - start

        passed, grader_out = grade(task_dir, workdir)
        total_elapsed = time.perf_counter() - start

        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}  agent={agent_elapsed:.1f}s  total={total_elapsed:.1f}s")
        if not passed:
            print(grader_out[-300:])

        span.update(
            output={
                "passed": passed,
                "agent_stdout_tail": agent_out.stdout[-500:],
                "grader_output_tail": grader_out[-500:],
            },
            metadata={
                "agent_elapsed_s": round(agent_elapsed, 2),
                "agent_error": agent_out.error,
                "session_id": session_id,
                **agent_out.metadata,
            },
        )
        span.score("pass", 1.0 if passed else 0.0,
                   comment=grader_out[-200:] if not passed else "")
        span.score("agent_latency_s", round(agent_elapsed, 2))

    shutil.rmtree(workdir, ignore_errors=True)

    return {
        "task": name,
        "passed": passed,
        "agent_elapsed_s": round(agent_elapsed, 2),
        "total_elapsed_s": round(total_elapsed, 2),
        "agent_error": agent_out.error,
        "session_id": session_id,
        "grader_tail": grader_out[-400:],
        "agent_stdout_tail": agent_out.stdout[-400:],
        "agent_stderr_tail": agent_out.stderr[-400:],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", help="run a single task by name")
    parser.add_argument("--run-name", help="Langfuse run name (default: auto)")
    parser.add_argument("--report-json", default="results.json")
    parser.add_argument("--report-md", default="results.md")
    args = parser.parse_args()

    load_dotenv()
    reporter = LangfuseReporter(run_name=args.run_name)
    if reporter.enabled:
        print(f"[langfuse] reporting to run: {reporter.run_name}")
    else:
        print("[langfuse] disabled (no LANGFUSE_PUBLIC_KEY)")

    print(f"[koda] mode={os.getenv('EVAL_AGENT_MODE', 'import')}  "
          f"model={os.getenv('KODA_MODEL', 'ollama:qwen2.5-coder:7b')}")

    tasks = sorted(t for t in TASKS_DIR.iterdir() if t.is_dir())
    if args.task:
        tasks = [t for t in tasks if t.name == args.task]
        if not tasks:
            sys.exit(f"task not found: {args.task}")

    results = [run_one(t, reporter) for t in tasks]
    reporter.flush()

    n_pass = sum(1 for r in results if r["passed"])
    n_total = len(results)
    pct = 100 * n_pass / n_total if n_total else 0

    Path(args.report_json).write_text(json.dumps({
        "run_name": reporter.run_name,
        "passed": n_pass,
        "total": n_total,
        "pass_rate": round(pct, 1),
        "agent_mode": os.getenv("EVAL_AGENT_MODE", "import"),
        "model": os.getenv("KODA_MODEL", "ollama:qwen2.5-coder:7b"),
        "results": results,
    }, indent=2))
    Path(args.report_md).write_text(_render_markdown(results, reporter.run_name))

    print(f"\n━━ {n_pass}/{n_total} passed ({pct:.0f}%) ━━")
    print(f"  json: {args.report_json}")
    print(f"  md:   {args.report_md}")

    sys.exit(0 if n_pass == n_total else 1)


def _render_markdown(results: list[dict], run_name: str) -> str:
    n_pass = sum(1 for r in results if r["passed"])
    n_total = len(results)
    pct = 100 * n_pass / n_total if n_total else 0
    model = os.getenv("KODA_MODEL", "ollama:qwen2.5-coder:7b")

    lines = [
        "## KODA coding agent — eval results",
        "",
        f"**{n_pass}/{n_total} passed ({pct:.0f}%)**",
        f"Run: `{run_name}` · Model: `{model}`",
        "",
        "| Task | Result | Agent time | Notes |",
        "|------|--------|-----------:|-------|",
    ]
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        note = r["agent_error"] or ("" if r["passed"] else "grader failed")
        lines.append(
            f"| `{r['task']}` | {icon} | {r['agent_elapsed_s']:.1f}s | {note} |"
        )

    if any(not r["passed"] for r in results):
        lines += ["", "<details><summary>Failure details</summary>", ""]
        for r in results:
            if not r["passed"]:
                lines += [
                    f"#### `{r['task']}`",
                    "```",
                    r["grader_tail"][-500:].strip(),
                    "```",
                    "",
                ]
        lines.append("</details>")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
