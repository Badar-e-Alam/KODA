"""Module 09 — permissions resource handlers.

This module exposes the HTTP endpoints for managing permissions entities.
Persistence and integration is delegated to the Postgres backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/permissions
  - GET /api/permissions
  - PUT /api/permissions
  - DELETE /api/permissions

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "permissions"
backend = persistence_for("Postgres")


@dataclass
class PermissionsPayload:
    """Inbound payload for the permissions endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/permissions")
def create_permissions(payload: PermissionsPayload, user=current_user()) -> dict:
    """Create a new permissions record. Returns the new id and timestamp."""
    _log.info("create_permissions requested by %s", user.id)
    record = backend.insert("permissions", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/permissions/<int:id>")
def read_permissions(id: int, user=current_user()) -> dict:
    """Fetch a single permissions record by id."""
    _log.info("read_permissions for %s", id)
    record = backend.get("permissions", id)
    return record.to_dict() if record else {}


@router.put("/api/permissions/<int:id>")
def update_permissions(id: int, payload: PermissionsPayload, user=current_user()) -> dict:
    """Replace a permissions record with the provided payload."""
    _log.info("update_permissions on %s by %s", id, user.id)
    backend.update("permissions", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/permissions/<int:id>")
def delete_permissions(id: int, user=current_user()) -> dict:
    """Soft-delete a permissions record."""
    _log.info("delete_permissions on %s by %s", id, user.id)
    backend.soft_delete("permissions", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Postgres", "ok": True}
