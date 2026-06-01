"""Langfuse integration for the eval suite (v4 compatible).

Each task run becomes one Langfuse observation (span), scored 1.0 (pass) or 0.0
(fail), plus scores for elapsed time. Dataset runs are tracked via the
session_id so you can filter by run name in the Langfuse UI.

Set these env vars:
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL

If LANGFUSE_PUBLIC_KEY isn't set, this becomes a no-op so local dev / CI
without secrets still works.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from typing import Iterator

try:
    from langfuse import Langfuse
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False

_log = logging.getLogger("eval.langfuse")

# Used to find dataset items for metadata enrichment. Optional — the
# harness works fine without a dataset.
DATASET_NAME = os.getenv("KODA_EVAL_DATASET", "koda-coding-evals-v1")


def _client() -> "Langfuse | None":
    if not LANGFUSE_AVAILABLE:
        return None
    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    if not pk:
        return None
    return Langfuse(
        public_key=pk,
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )


class LangfuseReporter:
    """Reports each task run as a Langfuse span, scored + grouped by run.

    Uses the Langfuse v4 API (``start_observation`` + ``create_score``).
    Dataset items are looked up for metadata, but scoring is independent —
    the harness works even without a Langfuse dataset configured.
    """

    def __init__(self, run_name: str | None = None):
        self.client = _client()
        self.enabled = self.client is not None
        self.run_name = run_name or f"local-{uuid.uuid4().hex[:8]}"
        # Cache items keyed by task name so update() can fetch metadata.
        self._items: dict[str, object] = {}
        if self.enabled:
            try:
                ds = self.client.get_dataset(DATASET_NAME)
                self._items = {
                    it.input["task"]: it
                    for it in ds.items
                    if hasattr(it, "input") and isinstance(it.input, dict)
                }
                _log.info("Loaded %d items from dataset %s", len(self._items), DATASET_NAME)
            except Exception as exc:  # noqa: BLE001
                _log.info("Dataset '%s' not found or unreadable (%s) — skipping dataset link", DATASET_NAME, exc)

    @contextmanager
    def task_run(self, task_name: str, prompt: str) -> Iterator[object]:
        """Context manager wrapping one task run.

        Yields a ``LangfuseSpan`` (v4) which has ``update()`` and ``score()``
        methods, or a no-op stand-in when Langfuse is unavailable.
        """
        if not self.enabled:
            yield _NullSpan()
            return

        item = self._items.get(task_name)

        # ``start_as_current_observation`` with ``end_on_exit=True`` gives us
        # a ``LangfuseSpan`` as the context-var target.  Inside the ``with``
        # block, ``update_current_span`` works and ``span.score`` /
        # ``span.update`` are native v4 methods.
        with self.client.start_as_current_observation(
            name=task_name,
            as_type="span",
            input={"prompt": prompt, "task": task_name},
            metadata={
                "eval_run_name": self.run_name,
                "eval_task": task_name,
                "source": "koda-evals/runner.py",
                "agent_cmd": os.getenv("EVAL_AGENT_CMD", ""),
                "git_sha": os.getenv("GITHUB_SHA", "")[:8],
                "git_ref": os.getenv("GITHUB_REF_NAME", ""),
                "ci": os.getenv("CI", "false"),
                "dataset_item_id": getattr(item, "id", None) if item else None,
            },
            end_on_exit=True,
        ) as span:
            yield span

    def flush(self):
        if self.enabled:
            self.client.flush()


class _NullSpan:
    """No-op stand-in when Langfuse is unavailable."""

    def update(self, **_): pass

    def score(self, *_a, **_kw): pass
