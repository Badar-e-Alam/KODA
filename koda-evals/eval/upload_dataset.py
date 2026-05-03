"""One-time setup: push the tasks/ directory into Langfuse as a dataset.

After running this, every task becomes a `dataset_item` in Langfuse and
each eval run is linked to it for easy comparison.

    python -m eval.upload_dataset

Re-run after editing tasks; it upserts by task name (dedup on input.task).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from langfuse import Langfuse

from eval.langfuse_reporter import DATASET_NAME

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"


def main():
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        sys.exit("LANGFUSE_PUBLIC_KEY not set. Did you load .env?")

    client = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )

    # Create dataset (idempotent — Langfuse upserts)
    client.create_dataset(
        name=DATASET_NAME,
        description="KODA coding agent eval suite — 10 tasks across bug fixes, features, refactors, and perf.",
        metadata={"version": "1.0", "source": "github.com/yourorg/koda-evals"},
    )
    print(f"✓ dataset: {DATASET_NAME}")

    # Snapshot existing items so we can dedup by task name
    existing = {}
    try:
        for it in client.get_dataset(DATASET_NAME).items:
            existing[it.input.get("task")] = it.id
    except Exception:
        pass

    count = 0
    for task_dir in sorted(TASKS_DIR.iterdir()):
        if not task_dir.is_dir():
            continue
        prompt = (task_dir / "prompt.txt").read_text()
        hint = (task_dir / "solution_hint.md").read_text() if (task_dir / "solution_hint.md").exists() else ""

        # Stuff repo files into metadata so the dataset item is self-contained
        repo_files = {}
        for f in (task_dir / "repo").rglob("*"):
            if f.is_file():
                rel = f.relative_to(task_dir / "repo").as_posix()
                try:
                    repo_files[rel] = f.read_text()
                except UnicodeDecodeError:
                    repo_files[rel] = "<binary>"

        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=existing.get(task_dir.name),  # reuse id if it exists → upsert
            input={"task": task_dir.name, "prompt": prompt},
            expected_output={"grader": "test.sh exits 0", "hint": hint},
            metadata={
                "task": task_dir.name,
                "repo_files": repo_files,
                "difficulty": _difficulty(task_dir.name),
            },
        )
        print(f"  + {task_dir.name}")
        count += 1

    print(f"\n✓ uploaded {count} tasks to Langfuse dataset '{DATASET_NAME}'")
    client.flush()


def _difficulty(name: str) -> str:
    n = int(name.split("_")[1])
    return "easy" if n <= 3 else "medium" if n <= 8 else "hard"


if __name__ == "__main__":
    main()
