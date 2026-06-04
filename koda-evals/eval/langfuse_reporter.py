"""Langfuse integration for the eval suite.

Each task run becomes one Langfuse trace, scored 1.0 (pass) or 0.0 (fail),
plus auxiliary scores for elapsed time and any other signals you want to
surface in the dashboard.

Set these env vars:
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL

If LANGFUSE_PUBLIC_KEY isn't set, this becomes a no-op (so local dev / CI
without secrets still works — you just don't get the dashboard).
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Iterator

try:
    from langfuse import Langfuse
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False


DATASET_NAME = "koda-coding-evals-v1"


def _client() -> "Langfuse | None":
    if not LANGFUSE_AVAILABLE:
        return None
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return None
    return Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )


class LangfuseReporter:
    """Reports each task run as a Langfuse trace + score.

    Wraps Langfuse's dataset_run feature so multiple runs of the suite show up
    as separate experiments you can compare in the UI.
    """

    def __init__(self, run_name: str | None = None):
        self.client = _client()
        self.enabled = self.client is not None
        self.run_name = run_name or f"local-{uuid.uuid4().hex[:8]}"
        self._items: dict[str, object] = {}  # task_name -> dataset_item
        if self.enabled:
            try:
                self._items = {
                    item.input["task"]: item
                    for item in self.client.get_dataset(DATASET_NAME).items
                }
            except Exception as e:
                print(f"[langfuse] could not fetch dataset {DATASET_NAME}: {e}")
                print("[langfuse] run `python -m eval.upload_dataset` first.")
                self.enabled = False

    @contextmanager
    def task_run(self, task_name: str, prompt: str) -> Iterator[object]:
        """Context manager wrapping one task run.

        Yields an object with an `update` method for adding metadata, plus
        `score(name, value, comment=...)` to attach scores.
        """
        if not self.enabled:
            yield _NullSpan()
            return

        item = self._items.get(task_name)
        if item is None:
            print(f"[langfuse] dataset item missing for {task_name}; skipping trace")
            yield _NullSpan()
            return

        # `item.run()` creates a trace linked to this run_name + dataset item.
        # In langfuse v4 the DatasetItem.run() context-manager was removed —
        # dataset run linking moved to client.create_dataset_run_item(trace_id=...).
        # Until the harness is ported to v4, gracefully degrade to a no-op outer
        # span. Inner KODA traces (the agent's own @observe / start_as_current_observation
        # calls) still flow to Langfuse independently.
        if not hasattr(item, "run"):
            yield _NullSpan()
            return
        with item.run(
            run_name=self.run_name,
            run_description=os.getenv("EVAL_RUN_DESCRIPTION", ""),
            run_metadata={
                "agent_cmd": os.getenv("EVAL_AGENT_CMD", ""),
                "git_sha": os.getenv("GITHUB_SHA", "")[:8],
                "git_ref": os.getenv("GITHUB_REF_NAME", ""),
                "ci": os.getenv("CI", "false"),
            },
        ) as trace:
            trace.update(input={"prompt": prompt, "task": task_name})
            yield _SpanWrapper(self.client, trace)

    def flush(self):
        if self.enabled:
            self.client.flush()


class _SpanWrapper:
    """Wraps a Langfuse trace so callers don't need to know about its API."""

    def __init__(self, client, trace):
        self.client = client
        self.trace = trace

    def update(self, **kwargs):
        self.trace.update(**kwargs)

    def score(self, name: str, value: float, comment: str | None = None):
        self.trace.score(name=name, value=value, comment=comment or "")


class _NullSpan:
    def update(self, **_): pass
    def score(self, *_a, **_kw): pass
