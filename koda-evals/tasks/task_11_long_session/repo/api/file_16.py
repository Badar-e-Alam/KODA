"""Module 16 — metrics resource handlers.

This module exposes the HTTP endpoints for managing metrics entities.
Persistence and integration is delegated to the Prometheus backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - GET /api/metrics

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "metrics"
backend = persistence_for("Prometheus")


@dataclass
class MetricsPayload:
    """Inbound payload for the metrics endpoints."""
    name: str
    metadata: dict | None = None


@router.get("/api/metrics/<int:id>")
def read_metrics(id: int, user=current_user()) -> dict:
    """Fetch a single metrics record by id."""
    _log.info("read_metrics for %s", id)
    record = backend.get("metrics", id)
    return record.to_dict() if record else {}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Prometheus", "ok": True}
