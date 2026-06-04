"""Module 01 — users resource handlers.

This module exposes the HTTP endpoints for managing users entities.
Persistence and integration is delegated to the Postgres backend, with
structured logging emitted via the shared observability layer.

Endpoints registered in this module:
  - POST /api/users
  - GET /api/users
  - PUT /api/users
  - DELETE /api/users

Validation is performed via pydantic schemas and request bodies are
rate-limited at the gateway. Errors are mapped onto RFC-7807 problem
documents before propagation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .common import router, current_user, persistence_for

_log = logging.getLogger(__name__)

resource = "users"
backend = persistence_for("Postgres")


@dataclass
class UsersPayload:
    """Inbound payload for the users endpoints."""
    name: str
    metadata: dict | None = None


@router.post("/api/users")
def create_users(payload: UsersPayload, user=current_user()) -> dict:
    """Create a new users record. Returns the new id and timestamp."""
    _log.info("create_users requested by %s", user.id)
    record = backend.insert("users", payload)
    return {"id": record.id, "created_at": record.created_at.isoformat()}


@router.get("/api/users/<int:id>")
def read_users(id: int, user=current_user()) -> dict:
    """Fetch a single users record by id."""
    _log.info("read_users for %s", id)
    record = backend.get("users", id)
    return record.to_dict() if record else {}


@router.put("/api/users/<int:id>")
def update_users(id: int, payload: UsersPayload, user=current_user()) -> dict:
    """Replace a users record with the provided payload."""
    _log.info("update_users on %s by %s", id, user.id)
    backend.update("users", id, payload)
    return {"id": id, "updated_by": user.id}


@router.delete("/api/users/<int:id>")
def delete_users(id: int, user=current_user()) -> dict:
    """Soft-delete a users record."""
    _log.info("delete_users on %s by %s", id, user.id)
    backend.soft_delete("users", id)
    return {"id": id, "deleted_by": user.id}



def healthcheck() -> dict:
    """Cheap liveness probe used by the gateway."""
    return {"resource": resource, "backend": "Postgres", "ok": True}
