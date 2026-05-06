"""One-time setup: push SWE-bench (Lite by default) into Langfuse as a dataset.

Each instance becomes a `dataset_item` whose ``input.prompt`` is the bug
report (the SWE-bench ``problem_statement`` field). Repository state and
the FAIL_TO_PASS / PASS_TO_PASS test names live in metadata so a
downstream runner has everything it needs to evaluate a patch.

    python -m eval.upload_swebench                          # default: lite, 300 instances
    python -m eval.upload_swebench --variant verified       # verified variant (~500)
    python -m eval.upload_swebench --limit 10               # smoke slice
    python -m eval.upload_swebench --dataset swe-bench-lite

Pure metadata push — does NOT install Docker images, run patches, or
exercise the agent. Use the SWE-bench harness for execution.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from datasets import load_dataset
from langfuse import Langfuse


# Map the friendly variant name to (huggingface_dataset_id, default_langfuse_name).
_VARIANTS = {
    "lite":     ("princeton-nlp/SWE-bench_Lite",     "swe-bench-lite"),
    "verified": ("princeton-nlp/SWE-bench_Verified", "swe-bench-verified"),
    "full":     ("princeton-nlp/SWE-bench",          "swe-bench-full"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant",
        choices=list(_VARIANTS),
        default="lite",
        help="SWE-bench variant to upload (default: lite — 300 instances)",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Langfuse dataset name (default depends on --variant)",
    )
    parser.add_argument(
        "--split",
        default="test",
        help="HuggingFace split to read (default: test)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap on instances uploaded (0 = all). Useful for smoke tests.",
    )
    args = parser.parse_args()

    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        sys.exit("LANGFUSE_PUBLIC_KEY not set. Did you load .env?")

    hf_id, default_name = _VARIANTS[args.variant]
    dataset_name = args.dataset or default_name

    print(f"loading {hf_id} (split={args.split})...")
    ds = load_dataset(hf_id, split=args.split)
    n = len(ds)
    if args.limit and args.limit < n:
        ds = ds.select(range(args.limit))
        print(f"  using first {args.limit} of {n} instances")
    else:
        print(f"  {n} instances")

    client = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )

    client.create_dataset(
        name=dataset_name,
        description=(
            f"SWE-bench {args.variant} — {len(ds)} real-world Python bug-fix "
            f"instances from {hf_id}. ``input.prompt`` is the issue text; "
            f"``metadata.repo``/``base_commit`` pin the buggy state; "
            f"``metadata.FAIL_TO_PASS``/``PASS_TO_PASS`` are the gating tests."
        ),
        metadata={
            "source_hf_id": hf_id,
            "source_split": args.split,
            "variant": args.variant,
            "instance_count": len(ds),
        },
    )
    print(f"✓ dataset: {dataset_name}")

    # Snapshot existing items so re-running upserts cleanly.
    existing: dict[str, Any] = {}
    try:
        for it in client.get_dataset(dataset_name).items:
            key = (it.input or {}).get("instance_id")
            if key:
                existing[key] = it.id
    except Exception:
        pass

    pushed = 0
    for inst in ds:
        instance_id = inst["instance_id"]
        client.create_dataset_item(
            dataset_name=dataset_name,
            id=existing.get(instance_id),
            input={
                "instance_id": instance_id,
                "prompt": inst["problem_statement"],
                "task": instance_id,  # mirror the koda-evals runner's "task" key
            },
            expected_output={
                "patch": inst.get("patch", ""),  # the canonical fix, for reference
                "test_patch": inst.get("test_patch", ""),
                "fail_to_pass": inst.get("FAIL_TO_PASS", ""),
                "pass_to_pass": inst.get("PASS_TO_PASS", ""),
            },
            metadata={
                "instance_id": instance_id,
                "repo": inst["repo"],
                "base_commit": inst["base_commit"],
                "version": inst.get("version", ""),
                "created_at": str(inst.get("created_at", "")),
                "hints_text": inst.get("hints_text", "") or "",
            },
        )
        pushed += 1
        if pushed % 25 == 0:
            print(f"  ...{pushed}/{len(ds)}")

    client.flush()
    print(f"\n✓ uploaded {pushed} instances to Langfuse dataset '{dataset_name}'")


if __name__ == "__main__":
    main()
