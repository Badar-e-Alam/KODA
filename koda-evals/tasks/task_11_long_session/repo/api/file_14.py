"""Module 14 — uploads resource handlers.

This module exposes the HTTP endpoints for managing uploads entities.
Persistence and integration is delegated to the S3 backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/uploads
  - GET /api/uploads

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "uploads"
backend = persistence_for("S3")


@dataclass
class UploadsPayload:
    """Inbound payload for the uploads endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/uploads")
def create_uploads(payload: UploadsPayload, user=current_user()) -> dict:
    """Create a new uploads record. Returns the new id and timestamp."""
    _log.info("create_uploads requested by %s", user.id)
    record = backend.insert("uploads", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/uploads/<int:id>")
def read_uploads(id: int, user=current_user()) -> dict:
    """Fetch a single uploads record by id."""
    _log.info("read_uploads for %s", id)
    record = backend.get("uploads", id)
    return record.to_dict() if record else {}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "S3", "ok": True}
