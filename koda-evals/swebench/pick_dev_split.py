"""Generate ``swebench/dev_split.json`` — a frozen 20% sample of SWE-bench Lite.

Strategy: stratified by repo with a fixed seed.

  - Load all 300 SWE-bench Lite instances from HuggingFace.
  - Group by repo.
  - From each repo, pick ``ceil(repo_count * fraction)`` instances
    using ``random.Random(seed).sample(...)`` — every repo gets at
    least one instance as long as it has any instances in Lite, so the
    resulting dev split is representative across the dataset's
    distribution rather than dominated by alphabetically-early repos.

The output file pins ``instance_ids`` so re-running the picker (or a
teammate running it on another machine) produces the same set as long
as the dataset and ``--seed`` / ``--fraction`` are unchanged.

Run once::

    python -m swebench.pick_dev_split

Override sampling::

    python -m swebench.pick_dev_split --fraction 0.10 --seed 7

Requires ``pip install datasets``.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
DATASET_SPLIT = "test"
OUT_PATH = Path(__file__).resolve().parent / "dev_split.json"


def _load_dataset():
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit(
            "`datasets` not installed. Run:\n\n"
            "    pip install datasets\n\n"
            "then re-run `python -m swebench.pick_dev_split`."
        )
    return load_dataset(DATASET_NAME, split=DATASET_SPLIT)


def pick(fraction: float, seed: int) -> dict:
    ds = _load_dataset()

    # Group instance_ids by repo
    by_repo: dict[str, list[str]] = defaultdict(list)
    for row in ds:
        by_repo[row["repo"]].append(row["instance_id"])

    rng = random.Random(seed)
    selected: list[str] = []
    per_repo: dict[str, int] = {}

    for repo, instance_ids in sorted(by_repo.items()):
        # Sort within-repo first so the rng draws from a deterministic
        # order regardless of HF dataset iteration order.
        instance_ids = sorted(instance_ids)
        n_pick = max(1, math.ceil(len(instance_ids) * fraction))
        n_pick = min(n_pick, len(instance_ids))
        picked = rng.sample(instance_ids, n_pick)
        selected.extend(sorted(picked))
        per_repo[repo] = n_pick

    selected.sort()

    return {
        "dataset": DATASET_NAME,
        "split": DATASET_SPLIT,
        "fraction": fraction,
        "seed": seed,
        "n_total_in_dataset": sum(len(v) for v in by_repo.values()),
        "n_selected": len(selected),
        "per_repo_counts": per_repo,
        "instance_ids": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fraction",
        type=float,
        default=0.20,
        help="Fraction of each repo's instances to pick (default: 0.20 = 20%%).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUT_PATH,
        help=f"Output JSON path (default: {OUT_PATH.name} next to this script).",
    )
    args = parser.parse_args()

    if not 0.0 < args.fraction <= 1.0:
        sys.exit(f"--fraction must be in (0, 1], got {args.fraction}")

    out = pick(args.fraction, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")

    print(
        f"wrote {args.out}: {out['n_selected']}/{out['n_total_in_dataset']} "
        f"instances across {len(out['per_repo_counts'])} repos "
        f"(fraction={out['fraction']}, seed={out['seed']})"
    )
    for repo, n in sorted(out["per_repo_counts"].items()):
        print(f"  {repo}: {n}")


if __name__ == "__main__":
    main()
