"""Per-turn timing instrumentation for the coding agent.

Logs land in the configured session log (``logs/session_*.log``) under
the ``koda.timing`` logger at INFO level. Tail the latest session log
to see one summary line per user turn::

    tail -f logs/session_*.log | grep timing

Read the summary line as: where did the wall-clock of a turn go?
``ttft`` (time-to-first-token) is the wait the user feels before any
text appears; ``tools`` is total wall time spent inside tool calls;
the rest is overhead the harness added before/around the model.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator, Optional

_log = logging.getLogger("koda.timing")


@contextmanager
def timed(label: str) -> Iterator[None]:
    """Log how long a block takes (one-shot, for build_agent / cold-start work)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt_ms = (time.perf_counter() - t0) * 1000
        _log.info("[timing] %s: %.1f ms", label, dt_ms)


class TurnTimings:
    """Collect timings for one user turn and emit a one-line breakdown.

    Drives a single summary log line, not a stream of events — easier to
    eyeball and grep. All durations are ms; ``-1`` means "never observed
    this milestone in this turn" (e.g. a tools-only turn with no text
    output won't have a TTFT).
    """

    def __init__(self) -> None:
        self.t_start = time.perf_counter()
        self.t_first_event: Optional[float] = None
        self.t_first_text: Optional[float] = None
        self.callbacks_ms: float = 0.0
        self.graph_build_ms: float = 0.0
        # tool_id -> {"name": str, "start": float, "end": float}
        self.tools: dict[str, dict] = {}

    def mark_first_event(self) -> None:
        if self.t_first_event is None:
            self.t_first_event = time.perf_counter()

    def mark_first_text(self) -> None:
        if self.t_first_text is None:
            self.t_first_text = time.perf_counter()

    def tool_start(self, tool_id: str, name: str) -> None:
        self.tools[tool_id] = {
            "name": name,
            "start": time.perf_counter(),
            "end": 0.0,
        }

    def tool_end(self, tool_id: str) -> None:
        entry = self.tools.get(tool_id)
        if entry is not None:
            entry["end"] = time.perf_counter()

    def log(self) -> None:
        now = time.perf_counter()
        total_ms = (now - self.t_start) * 1000
        ttfe_ms = (
            (self.t_first_event - self.t_start) * 1000
            if self.t_first_event is not None
            else -1.0
        )
        ttft_ms = (
            (self.t_first_text - self.t_start) * 1000
            if self.t_first_text is not None
            else -1.0
        )

        tool_summary: list[str] = []
        total_tool_ms = 0.0
        for entry in self.tools.values():
            if entry["end"]:
                dur_ms = (entry["end"] - entry["start"]) * 1000
                total_tool_ms += dur_ms
                tool_summary.append(f"{entry['name']}={dur_ms:.0f}ms")

        _log.info(
            "[timing] turn total=%.0fms | graph_build=%.0fms callbacks=%.0fms "
            "ttfe=%.0fms ttft=%.0fms tools(%d)=%.0fms [%s]",
            total_ms,
            self.graph_build_ms,
            self.callbacks_ms,
            ttfe_ms,
            ttft_ms,
            len(self.tools),
            total_tool_ms,
            ", ".join(tool_summary) if tool_summary else "-",
        )


__all__ = ["TurnTimings", "timed"]
