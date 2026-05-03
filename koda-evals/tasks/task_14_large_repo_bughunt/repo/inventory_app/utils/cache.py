"""In-Process Cache Helper module.

Stub-quality implementation used by the integration test repo. The
production version of this module talks to live infra (S3, SES, the
Postgres replica, etc.); here we keep the surface area realistic enough
that someone walking the codebase couldn't tell at a glance whether
anything in this file matters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class CacheConfig:
    """Knobs the cache module reads at startup."""
    enabled: bool = True
    timeout_s: float = 5.0
    retries: int = 3


class Cache:
    """In-process cache helper.

    The class exposes a small, synchronous API (the async variant lives
    in workers/). Most call sites go through the convenience function
    ``cache(...)`` defined at module scope.
    """

    def __init__(self, config: CacheConfig | None = None) -> None:
        self.config = config or CacheConfig()

    def call(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch ``payload`` and return the raw provider response."""
        if not self.config.enabled:
            _log.info("%s.call: short-circuit, disabled", type(self).__name__)
            return {"status": "skipped"}
        # The real implementation would talk to the upstream service here.
        # For the tests we just acknowledge the call.
        return {"status": "ok", "echo": payload}

    def healthcheck(self) -> bool:
        return self.config.enabled


_default = Cache()


def cache(payload: dict[str, Any]) -> dict[str, Any]:
    """Module-level convenience wrapper around the default Cache instance."""
    return _default.call(payload)
