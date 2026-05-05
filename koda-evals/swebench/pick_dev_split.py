"""Pick a frozen dev-20 split from SWE-bench Lite.

Run ONCE, commit the resulting dev_split.json, then never re-run except to
expand the split. Re-running with the same seed is reproducible, but the
point of "frozen" is that you stop iterating against the same 20 once they
become the dev set.

    python -m swebench.pick_dev_split            # writes dev_split.json
    python -m swebench.pick_dev_split --n 30     # bigger split
    python -m swebench.pick_dev_split --seed 7   # different sample

Stratifies across repos so you don't end up with all-django (which would
let you overfit to one codebase's idioms).
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dev_split.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20, help="split size (default 20)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--split", default="test", help="HF split name (SWE-bench Lite uses 'test')")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    from datasets import load_dataset

    ds = load_dataset(args.dataset, split=args.split)

    by_repo: dict[str, list[str]] = defaultdict(list)
    for row in ds:
        by_repo[row["repo"]].append(row["instance_id"])

    rng = random.Random(args.seed)
    repos = sorted(by_repo)
    for r in repos:
        by_repo[r].sort()
        rng.shuffle(by_repo[r])

    picked: list[str] = []
    cursors = {r: 0 for r in repos}
    while len(picked) < args.n:
        progressed = False
        for r in repos:
            if len(picked) >= args.n:
                break
            if cursors[r] < len(by_repo[r]):
                picked.append(by_repo[r][cursors[r]])
                cursors[r] += 1
                progressed = True
        if not progressed:
            break

    payload = {
        "dataset": args.dataset,
        "split": args.split,
        "seed": args.seed,
        "n": len(picked),
        "instance_ids": sorted(picked),
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {len(picked)} instances → {args.out}")
    counts: dict[str, int] = defaultdict(int)
    for iid in picked:
        counts[iid.split("__", 1)[0]] += 1
    for repo, c in sorted(counts.items()):
        print(f"  {repo}: {c}")


if __name__ == "__main__":
    main()
