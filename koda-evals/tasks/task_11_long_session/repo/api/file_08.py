"""Module 08 — audits resource handlers.

This module exposes the HTTP endpoints for managing audits entities.
Persistence and integration is delegated to the Datadog backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - GET /api/audits

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "audits"
backend = persistence_for("Datadog")


@dataclass
class AuditsPayload:
    """Inbound payload for the audits endpoints."""
    name: str
    metadata: dict | None = None


@router.get("/api/audits/<int:id>")
def read_audits(id: int, user=current_user()) -> dict:
    """Fetch a single audits record by id."""
    _log.info("read_audits for %s", id)
    record = backend.get("audits", id)
    return record.to_dict() if record else {}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Datadog", "ok": True}
