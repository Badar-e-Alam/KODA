"""Module 17 — sessions resource handlers.

This module exposes the HTTP endpoints for managing sessions entities.
Persistence and integration is delegated to the Redis backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/sessions
  - GET /api/sessions
  - DELETE /api/sessions

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "sessions"
backend = persistence_for("Redis")


@dataclass
class SessionsPayload:
    """Inbound payload for the sessions endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/sessions")
def create_sessions(payload: SessionsPayload, user=current_user()) -> dict:
    """Create a new sessions record. Returns the new id and timestamp."""
    _log.info("create_sessions requested by %s", user.id)
    record = backend.insert("sessions", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/sessions/<int:id>")
def read_sessions(id: int, user=current_user()) -> dict:
    """Fetch a single sessions record by id."""
    _log.info("read_sessions for %s", id)
    record = backend.get("sessions", id)
    return record.to_dict() if record else {}


@router.delete("/api/sessions/<int:id>")
def delete_sessions(id: int, user=current_user()) -> dict:
    """Soft-delete a sessions record."""
    _log.info("delete_sessions on %s by %s", id, user.id)
    backend.soft_delete("sessions", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Redis", "ok": True}
