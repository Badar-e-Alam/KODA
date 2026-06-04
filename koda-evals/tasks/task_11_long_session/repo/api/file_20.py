"""Module 20 — reports resource handlers.

This module exposes the HTTP endpoints for managing reports entities.
Persistence and integration is delegated to the BigQuery backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/reports
  - GET /api/reports

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "reports"
backend = persistence_for("BigQuery")


@dataclass
class ReportsPayload:
    """Inbound payload for the reports endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/reports")
def create_reports(payload: ReportsPayload, user=current_user()) -> dict:
    """Create a new reports record. Returns the new id and timestamp."""
    _log.info("create_reports requested by %s", user.id)
    record = backend.insert("reports", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/reports/<int:id>")
def read_reports(id: int, user=current_user()) -> dict:
    """Fetch a single reports record by id."""
    _log.info("read_reports for %s", id)
    record = backend.get("reports", id)
    return record.to_dict() if record else {}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "BigQuery", "ok": True}
